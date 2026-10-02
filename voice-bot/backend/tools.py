# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""The bot's tools. Every fact about a loan file comes from the IDP API.

file_status(applicant)          -> READY / NOT READY, missing items in plain words, mismatches
eligibility(applicant)          -> best lender, amount (lakh), EMI, why other lenders are not eligible
reminder(applicant, language)   -> the exact WhatsApp/SMS text of the missing items (reminders.py)
verify_caller(applicant, dob)   -> phone/WhatsApp inbound only: date of birth check before any file detail
switch_language(language)       -> Transcribe + reply language: hi / en / mr
end_call(reason)                -> goodbye, then the call ends
transfer_to_human(reason)       -> phone only: hand the call to the telecaller line

Who may hear a file:
- browser: DSA staff signed in with the IDP's Cognito pool;
- outbound phone call: only the file of the applicant the call was placed for;
- inbound phone / WhatsApp: only after the caller is verified, by caller ID (the
  number matches the mobile on the file's CIBIL page) or by date of birth
  (verify_caller, 2 attempts). The bot never says the stored mobile or DOB.

Privacy: tool results never carry a full PAN or Aadhaar number or another
customer's name; nothing here logs arguments or results (only tool name,
status and timing).
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Any, Awaitable, Callable

from loguru import logger

import reminders
from config import Settings
from idp_client import IdpClient, IdpError
from prompt import LANG_NAME, short_lang

_FILE_REF = re.compile(r"\s*[\[(][^\])]*?\.(?:pdf|png|jpe?g|tiff?|docx?|xlsx?|csv|txt)[\])]", re.I)
_PAN = re.compile(r"\b[A-Za-z]{5}\d{4}[A-Za-z]\b")
_MASKED_PAN = re.compile(r"\bX{6}\d{3}[A-Z]\b")
_AADHAAR = re.compile(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)")
_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "shri", "sri", "smt", "kumari", "km", "ji", "sir", "madam", "mam", "saheb", "sahab", "bhai", "tai"}

PHONE_CHANNELS = ("plivo", "exotel")
VERIFY_CHANNELS = ("plivo", "exotel", "whatsapp")
MAX_VERIFY_ATTEMPTS = 2


# ------------------------------------------------------------------ session
@dataclass
class CallSession:
    """One call. ``idp_user`` is the x-user-id audit label sent to the IDP."""

    call_id: str
    channel: str  # browser | plivo | exotel | whatsapp
    language: str = "hi"  # hi | en | mr
    idp_user: str = "voice-bot"
    applicant_hint: str | None = None  # outbound: whose file the call is about
    provider_call_id: str | None = None
    outbound: bool = False
    started_at: float = field(default_factory=time.monotonic)
    applicant: str | None = None  # canonical name of the file discussed
    project_id: str | None = None
    verdict: str | None = None
    tools_used: list[str] = field(default_factory=list)
    ended_reason: str | None = None
    transfer_requested: bool = False
    caller_number: str | None = None  # inbound phone/WhatsApp caller ID (never logged)
    verified_for: str | None = None  # canonical applicant name this caller is verified for
    verify_attempts: int = 0
    emit: Callable[[dict], Awaitable[None]] | None = None  # browser UI events
    speak: Callable[[str], Awaitable[None]] | None = None  # say a fixed line now (set by the pipeline)
    _check: dict | None = None  # last file-check: {"query", "applicant", "result", "project_id"}
    _projects: tuple[float, list[dict]] | None = None
    _profiles: dict = field(default_factory=dict)  # canonical name -> profile (mobile, dob) or None

    @property
    def needs_verification(self) -> bool:
        return self.channel in VERIFY_CHANNELS and not self.outbound


# ------------------------------------------------------------------ text helpers
def _ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def name_tokens(name: str) -> list[str]:
    words = re.sub(r"[^a-z ]", " ", _ascii_fold(name or "").lower()).split()
    return [w for w in words if w not in _HONORIFICS and len(w) > 1]


def _token_match(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < 4:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.8


def _customer_part(project_name: str) -> str:
    """'Sneha Kulkarni – Personal Loan' -> 'Sneha Kulkarni'."""
    return re.split(r"\s+[–—-]\s+|\s*\|\s*|\s*:\s*", project_name or "", maxsplit=1)[0]


def mask_ids(text: str) -> str:
    text = _PAN.sub(lambda m: "XXXXXX" + m.group(0)[-4:].upper(), text)
    return _AADHAAR.sub("XXXX XXXX XXXX", text)


def plain(text: str, limit: int = 220) -> str:
    """Speakable finding: no file names, masked IDs, single spaces, bounded length."""
    text = _FILE_REF.sub("", str(text or ""))
    text = mask_ids(text)
    text = re.sub(r"\s+", " ", text).strip(" ;,")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def inr(amount: float | int | None) -> str | None:
    """Indian grouping: 1600000.4 -> '₹16,00,000'."""
    if amount is None:
        return None
    n = int(round(float(amount)))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return ("-" if n < 0 else "") + "₹" + s


def lakh(amount: float | int | None) -> str | None:
    """1600000 -> '16 lakh', 450000 -> '4.5 lakh', 95000 -> '₹95,000'."""
    if amount is None:
        return None
    value = float(amount)
    if abs(value) >= 1_00_00_000:
        crore = round(value / 1_00_00_000, 2)
        return f"{crore:g} crore"
    if abs(value) >= 1_00_000:
        return f"{round(value / 1_00_000, 2):g} lakh"
    return inr(value)


def _digits10(number: str | None) -> str:
    digits = re.sub(r"\D", "", number or "")
    return digits[-10:] if len(digits) >= 10 else ""


_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}


def parse_dob(value: str | None) -> str | None:
    """'1995-08-23' / '23-08-1995' / '23/08/1995' / '23 August 1995' / 'Aug 23, 1995' -> '1995-08-23'."""
    text = re.sub(r"[,\s]+", " ", str(value or "").strip().lower())
    if not text:
        return None
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r"(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4})", text)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)? ([a-z]+) (\d{4})", text) or re.fullmatch(r"([a-z]+) (\d{1,2})(?:st|nd|rd|th)? (\d{4})", text)
            if not m:
                return None
            first, second, y = m.group(1), m.group(2), int(m.group(3))
            day, month = (first, second) if first.isdigit() else (second, first)
            mo = next((n for name, n in _MONTHS.items() if name.startswith(month[:3])), 0) if len(month) >= 3 else 0
            d = int(day)
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def _months_words(months: int | None) -> str | None:
    if not months:
        return None
    if months % 12 == 0:
        years = months // 12
        return f"{years} year" + ("s" if years != 1 else "")
    return f"{months} months"


# ------------------------------------------------------------------ the tools
class LoanTools:
    """Tool implementations for one call (they share the session's file check)."""

    PROJECTS_TTL_S = 60.0

    def __init__(self, idp: IdpClient, settings: Settings, session: CallSession):
        self.idp = idp
        self.settings = settings
        self.session = session

    # ---------------------------------------------------- project lookup
    async def _projects(self) -> list[dict]:
        cached = self.session._projects
        if cached and time.monotonic() - cached[0] < self.PROJECTS_TTL_S:
            return cached[1]
        projects = await self.idp.list_projects(user=self.session.idp_user)
        self.session._projects = (time.monotonic(), projects)
        return projects

    def _is_qa_project(self, project: dict) -> bool:
        name = str(project.get("name") or "")
        return (
            project.get("project_id") == self.settings.qa_project_id
            or name == self.settings.qa_project_name
            or name.lower().startswith("telecaller qa")
        )

    async def find_project(self, applicant: str) -> tuple[dict | None, str]:
        """(project, reason) with reason in: ok | no_name | not_found | ambiguous."""
        wanted = name_tokens(applicant)
        if not wanted:
            return None, "no_name"
        scored: list[tuple[int, dict]] = []
        for project in await self._projects():
            if self._is_qa_project(project) or str(project.get("status", "active")).lower() not in ("active", ""):
                continue
            have = name_tokens(_customer_part(str(project.get("name") or "")))
            hits = sum(1 for w in wanted if any(_token_match(w, h) for h in have))
            if hits:
                scored.append((hits, project))
        if not scored:
            return None, "not_found"
        best = max(h for h, _ in scored)
        needed = min(2, len(wanted))
        top = [p for h, p in scored if h == best]
        if best < needed:
            return None, "not_found" if best == 0 else ("ambiguous" if len(scored) > 1 else "not_found")
        if len(top) > 1:
            return None, "ambiguous"
        return top[0], "ok"

    def _pick_applicant(self, result: dict, query: str) -> dict | None:
        people = [a for a in (result.get("applicants") or []) if isinstance(a, dict)]
        if len(people) <= 1:
            return people[0] if people else None
        wanted = name_tokens(query)
        best, best_hits = None, 0
        for person in people:
            have = name_tokens(str(person.get("applicant") or ""))
            hits = sum(1 for w in wanted if any(_token_match(w, h) for h in have))
            if hits > best_hits:
                best, best_hits = person, hits
        return best

    async def _check(self, applicant: str) -> tuple[dict | None, dict]:
        """(file-check applicant result, info) for a name; reuses this call's last check of the same name."""
        cached = self.session._check
        if cached and name_tokens(cached["query"]) == name_tokens(applicant) and time.monotonic() - cached["at"] < 120:
            return cached["applicant"], cached
        project, reason = await self.find_project(applicant)
        if project is None:
            return None, {"reason": reason}
        # The project name carries the file's own spelling of the customer's name (the caller's,
        # heard through speech recognition, may be off by a letter); the IDP matches it even
        # without the middle name. If that finds nobody, check every applicant of the file.
        filter_name = _customer_part(str(project.get("name") or "")) or applicant
        result = await self.idp.file_check(project["project_id"], filter_name, user=self.session.idp_user)
        person = self._pick_applicant(result, applicant)
        if person is None:
            result = await self.idp.file_check(project["project_id"], None, user=self.session.idp_user)
            person = self._pick_applicant(result, applicant)
        info = {
            "query": applicant,
            "at": time.monotonic(),
            "project_id": project["project_id"],
            "applicant": person,
            "result": result,
            "reason": "ok" if person else "no_applicant",
        }
        self.session._check = info
        if person:
            self.session.applicant = person.get("applicant")
            self.session.project_id = project["project_id"]
            self.session.verdict = person.get("verdict")
        return person, info

    @staticmethod
    def _applicant_keys(person: dict) -> list[str]:
        """How the IDP stores an applicant's CIBIL-page inputs: by PAN (what the web app and the demo
        loader use), and by name for inputs saved before that."""
        keys = [str(person["pan"]).strip()] if person.get("pan") else []
        canonical = str(person.get("applicant") or "").strip()
        if canonical and canonical not in keys:
            keys.append(canonical)
        return keys

    async def _profile(self, project_id: str, person: dict) -> dict | None:
        """The CIBIL-page profile (mobile, dob) of the applicant: saved inputs, else the documents' date of birth."""
        canonical = str(person.get("applicant") or "")
        if canonical in self.session._profiles:
            return self.session._profiles[canonical]
        profile = None
        draft_dob = None
        for key in self._applicant_keys(person):
            try:
                data = await self.idp.eligibility_inputs(project_id, key, user=self.session.idp_user)
            except IdpError as e:
                if e.status in (400, 404, 422):
                    continue
                raise
            found = dict((data.get("inputs") or {}).get("profile") or {})
            if data.get("saved"):
                profile = found
                break
            # A draft from the documents: no mobile, maybe a date of birth.
            draft_dob = draft_dob or found.get("dob") or (data.get("prefill") or {}).get("dob")
        if profile is None and draft_dob:
            profile = {"dob": draft_dob}
        elif profile is not None and not profile.get("dob") and draft_dob:
            profile["dob"] = draft_dob
        self.session._profiles[canonical] = profile
        return profile

    async def _gate(self, person: dict, info: dict) -> dict | None:
        """None when this caller may hear the file; else what to do first."""
        s = self.session
        if s.channel not in VERIFY_CHANNELS:
            return None
        canonical = str(person.get("applicant") or "")
        if s.outbound:
            hint = name_tokens(s.applicant_hint or "")
            have = name_tokens(canonical)
            if hint and sum(1 for w in hint if any(_token_match(w, h) for h in have)) >= min(2, len(hint)):
                return None
            return {"found": False, "reason": "other_file", "say": "This call is only about the file of the person it was placed for. Do not discuss any other file."}
        if s.verified_for == canonical:
            return None
        if s.caller_number:
            profile = await self._profile(info["project_id"], person)
            if profile and _digits10(profile.get("mobile")) and _digits10(profile.get("mobile")) == _digits10(s.caller_number):
                s.verified_for = canonical
                logger.bind(event="caller_verified", how="caller_id").info("caller verified")
                return None
        if s.verify_attempts >= MAX_VERIFY_ATTEMPTS:
            return {"found": False, "reason": "not_verified", "say": "The caller could not be verified: do not share the file. Offer a call back from the team on the registered number."}
        return {
            "found": True,
            "needs_verification": True,
            "say": "Before sharing anything about this file, ask the caller for the applicant's date of birth and call verify_caller. Do not share any file detail yet.",
        }

    async def verify_caller(self, applicant: str, date_of_birth: str) -> dict:
        s = self.session
        if not s.needs_verification:
            return {"verified": True, "say": "No check is needed on this call."}
        if s.verify_attempts >= MAX_VERIFY_ATTEMPTS:
            return {"verified": False, "say": "Too many attempts: do not share the file. Offer a call back from the team on the registered number."}
        person, info = await self._check(applicant)
        if person is None:
            return self._not_found(info["reason"], applicant)
        profile = await self._profile(info["project_id"], person)
        expected = parse_dob(str((profile or {}).get("dob") or ""))
        if not expected:
            return {"verified": False, "say": "The file has no date of birth to check: do not share the file. Offer a call back from the team."}
        s.verify_attempts += 1
        if parse_dob(date_of_birth) == expected:
            s.verified_for = str(person.get("applicant") or "")
            logger.bind(event="caller_verified", how="dob").info("caller verified")
            return {"verified": True, "say": "Verified. Now answer the caller's question with the file tools."}
        left = MAX_VERIFY_ATTEMPTS - s.verify_attempts
        logger.bind(event="caller_not_verified", attempts_left=left).info("date of birth did not match")
        if left <= 0:
            return {"verified": False, "attempts_left": 0, "say": "It does not match: do not share the file. Offer a call back from the team on the registered number."}
        return {"verified": False, "attempts_left": left, "say": "It does not match. Ask once more for the date of birth. Never say the stored one."}

    @staticmethod
    def _not_found(reason: str, applicant: str) -> dict:
        say = {
            "no_name": "Ask for the applicant's full name (first name and surname).",
            "not_found": "No loan file matches this name. Ask the caller to repeat the full name, or offer a call back from the team.",
            "other_file": "This call is only about the file of the person it was placed for.",
            "ambiguous": "More than one file matches. Ask for the full name with the surname. Never tell the caller other customers' names.",
            "no_applicant": "The file has no applicant details yet. Offer a call back from the team.",
        }[reason]
        return {"found": False, "reason": reason, "name_heard": applicant[:80], "say": say}

    # ---------------------------------------------------- tool 1: file status
    async def file_status(self, applicant: str) -> dict:
        person, info = await self._check(applicant)
        if person is None:
            return self._not_found(info["reason"], applicant)
        gate = await self._gate(person, info)
        if gate:
            return gate
        result = info["result"]
        checklist_id = (result.get("checklist") or {}).get("id")
        items = reminders.reminder_items(person, checklist_id)
        missing = reminders.reminder_document_phrases(items, "en", "whatsapp", as_of=str(result.get("as_of") or ""))
        pending = len(result.get("pending_documents") or [])
        mismatches = [plain(m) for m in (person.get("mismatches") or [])][:3]
        needs_review = [plain(m) for m in (person.get("needs_review") or []) + (person.get("manual_review") or [])][:3]
        verdict = person.get("verdict") or result.get("overall_verdict")
        out: dict[str, Any] = {
            "found": True,
            "applicant": person.get("applicant"),
            "verdict": verdict,
            "missing_documents": missing,
            "mismatches": mismatches,
            "to_be_checked_by_team": needs_review,
            "documents_still_being_read": pending,
        }
        if verdict == "READY" and not pending:
            out["say"] = "The file is complete and ready for the lender login. The lender still decides."
        elif pending:
            out["say"] = f"{pending} uploaded document(s) are still being read; the result can change once they are done."
        elif missing:
            out["say"] = "Tell the caller the missing documents in plain words, then offer a WhatsApp reminder."
        elif mismatches:
            out["say"] = "Nothing is missing, but some details do not match. The team will call to clarify."
        if self.session.emit:
            await self.session.emit({"event": "file_status", "verdict": verdict, "missing": missing, "mismatches": len(mismatches)})
        return out

    # ---------------------------------------------------- tool 2: eligibility
    async def eligibility(self, applicant: str) -> dict:
        person, info = await self._check(applicant)
        if person is None:
            return self._not_found(info["reason"], applicant)
        gate = await self._gate(person, info)
        if gate:
            return gate
        project_id = info["project_id"]
        canonical = str(person.get("applicant") or applicant)
        calc = None
        for key in self._applicant_keys(person) or [canonical]:
            try:
                calc = await self.idp.eligibility(project_id, key, user=self.session.idp_user)
                break
            except IdpError as e:
                if e.status not in (400, 404):
                    raise
        if calc is None:
            return {
                "found": True,
                "available": False,
                "applicant": canonical,
                "say": "The team has not filled the eligibility (CIBIL) details for this file yet. Offer a call back.",
            }
        rows = [r for r in (calc.get("per_lender") or []) if isinstance(r, dict)]
        eligible = sorted(
            (r for r in rows if r.get("status") == "eligible" and (r.get("eligible_amount") or 0) > 0),
            key=lambda r: r.get("eligible_amount") or 0,
            reverse=True,
        )
        best_name = calc.get("best_lender")
        best = next((r for r in eligible if r.get("lender") == best_name), eligible[0] if eligible else None)

        def offer(r: dict) -> dict:
            return {
                "lender": r.get("lender"),
                "amount": lakh(r.get("eligible_amount")),
                "emi_per_month": inr(r.get("emi")),
                "tenure": _months_words(r.get("tenure_months")),
                "indicative_rate_percent": r.get("roi"),
            }

        not_eligible = [
            {"lender": r.get("lender"), "status": r.get("status_label") or r.get("status"), "why": [plain(x, 140) for x in (r.get("reasons") or [])][:2]}
            for r in rows
            if r.get("status") != "eligible"
        ][:4]
        requested = calc.get("requested") or {}
        out: dict[str, Any] = {
            "found": True,
            "available": True,
            "applicant": canonical,  # never calc["applicant"]: it echoes the key, a full PAN when looked up by PAN
            "indicative_only": True,
            "best": offer(best) if best else None,
            "best_reason": plain(calc.get("best_lender_reason") or "", 160) or None,
            "other_eligible": [offer(r) for r in eligible if r is not best][:2],
            "not_eligible": not_eligible,
            "requested": {"amount": lakh(requested.get("amount")), "tenure": _months_words(requested.get("tenure_months"))},
            "income_used_per_month": inr(calc.get("income_considered")),
            "existing_emis_per_month": inr(calc.get("obligations")),
            "say": "Indicative only, from sample lender policies: the lender decides. Never promise approval.",
        }
        if not eligible:
            out["say"] = "No lender is eligible on the current details. Explain the main reason simply and offer a call back. Never promise anything."
        if self.session.emit:
            await self.session.emit({"event": "eligibility", "best_lender": best.get("lender") if best else None, "amount": out["best"]["amount"] if best else None})
        return out

    # ---------------------------------------------------- tool 3: reminder
    async def reminder(self, applicant: str, language: str = "", channel: str = "whatsapp") -> dict:
        person, info = await self._check(applicant)
        if person is None:
            return self._not_found(info["reason"], applicant)
        gate = await self._gate(person, info)
        if gate:
            return gate
        lang = short_lang(language or self.session.language)
        channel = channel if channel in reminders.CHANNELS else "whatsapp"
        result = info["result"]
        checklist_id = (result.get("checklist") or {}).get("id")
        template = reminders.preferred_template(person, checklist_id, after_call=True)
        if template is None:
            return {"drafted": False, "say": "Nothing is missing in this file, so no reminder is needed."}
        draft = reminders.build_reminder(
            person,
            template,
            lang,
            channel,
            checklist_id=checklist_id,
            product=reminders.product_from_checklist(checklist_id),
            as_of=str(result.get("as_of") or ""),
            values={"dsa_name": self.settings.dsa_name, "dsa_phone": self.settings.dsa_phone},
        )
        if self.session.emit:
            await self.session.emit({
                "event": "reminder",
                "template": draft.template,
                "language": draft.language,
                "channel": draft.channel,
                "text": draft.text,
                "placeholders": draft.placeholders,
            })
        return {
            "drafted": True,
            "sent": False,
            "template": draft.template,
            "language": LANG_NAME[lang],
            "channel": channel,
            "documents_listed": draft.documents,
            "text": draft.text,
            "placeholders_for_team": draft.placeholders,
            "say": "Tell the caller which documents the message lists and that the team will send it on WhatsApp. Do not read the text, placeholders or links aloud.",
        }


# ------------------------------------------------------------------ tool schemas
def tool_schemas(channel: str):
    """Pipecat FunctionSchemas. transfer_to_human only exists on phone calls."""
    from pipecat.adapters.schemas.function_schema import FunctionSchema

    applicant = {
        "type": "string",
        "description": "The applicant's full name in English letters, for example 'Sneha Kulkarni', even if it was said in Hindi or Marathi.",
    }
    schemas = [
        FunctionSchema(
            name="file_status",
            description="Check the applicant's loan file: READY or NOT READY for the lender login, which documents are still missing (with exact months), and details that do not match.",
            properties={"applicant": applicant},
            required=["applicant"],
        ),
        FunctionSchema(
            name="eligibility",
            description="Indicative eligible loan amount and EMI at the best matching lender, the other lenders and why some are not eligible. Indicative only; the lender decides.",
            properties={"applicant": applicant},
            required=["applicant"],
        ),
        FunctionSchema(
            name="reminder",
            description="Draft (not send) the WhatsApp or SMS reminder that lists only the documents still missing, from fixed templates.",
            properties={
                "applicant": applicant,
                "language": {"type": "string", "enum": ["hi", "en", "mr"], "description": "Message language: hi Hindi, en English, mr Marathi."},
                "channel": {"type": "string", "enum": ["whatsapp", "sms"], "description": "whatsapp (default) or sms."},
            },
            required=["applicant", "language"],
        ),
        FunctionSchema(
            name="switch_language",
            description="Switch the call to another language when the caller asks: speech recognition and your replies.",
            properties={"language": {"type": "string", "enum": ["hi", "en", "mr"], "description": "hi Hindi, en English, mr Marathi."}},
            required=["language"],
        ),
        FunctionSchema(
            name="end_call",
            description="End the call after you have said goodbye, or when the caller asks not to be called.",
            properties={
                "reason": {
                    "type": "string",
                    "enum": ["done", "do_not_call", "wrong_person", "caller_busy", "abusive", "other"],
                    "description": "Why the call ends.",
                }
            },
            required=["reason"],
        ),
    ]
    if channel in VERIFY_CHANNELS:
        schemas.append(
            FunctionSchema(
                name="verify_caller",
                description="Check the caller before any file detail is shared (only when a file tool says needs_verification): the applicant's date of birth as the caller says it.",
                properties={
                    "applicant": applicant,
                    "date_of_birth": {"type": "string", "description": "The date of birth the caller said, as YYYY-MM-DD, for example 1995-08-23."},
                },
                required=["applicant", "date_of_birth"],
            )
        )
    if channel in PHONE_CHANNELS:
        schemas.append(
            FunctionSchema(
                name="transfer_to_human",
                description="Hand this phone call to a human telecaller when the caller asks for a person or agent, or is upset.",
                properties={"reason": {"type": "string", "description": "A few words on why."}},
                required=["reason"],
            )
        )
    return schemas


async def run_tool(name: str, coro: Awaitable[dict], timeout_s: float = 25.0) -> dict:
    """Run a tool with a timeout; failures become a plain result the LLM can say, never a crash."""
    started = time.monotonic()
    status = "ok"
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except asyncio.TimeoutError:
        status = "timeout"
        return {"error": "timeout", "say": "The loan system is slow right now. Apologise and offer a call back from the team."}
    except IdpError as e:
        status = f"idp_{e.status}"
        return {"error": "unavailable", "say": "The loan system is not reachable right now. Apologise and offer a call back from the team."}
    except Exception as e:  # noqa: BLE001
        status = type(e).__name__
        return {"error": "failed", "say": "Something went wrong while checking. Apologise and offer a call back from the team."}
    finally:
        logger.bind(event="tool", tool=name, status=status, ms=int((time.monotonic() - started) * 1000)).info("tool call")
