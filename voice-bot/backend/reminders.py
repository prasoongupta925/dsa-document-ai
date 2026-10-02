# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""WhatsApp / SMS document reminders, built from the file-check verdict only.

A Python port of the IDP web app's reminder builder
(packages/frontend/src/lib/reminder.ts and data/reminderTemplates.ts), so the
voice bot drafts exactly the text the File Check panel shows. No model is
involved: the missing items and months are the verdict's own (its MISSING
required checklist rows), a mismatch message names the document type only,
and nothing unknown is invented: unknown values stay visible as {{placeholders}}.
Texts never carry PAN, Aadhaar, account numbers or amounts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

LANGUAGES = ("en", "hi", "mr")
CHANNELS = ("whatsapp", "sms")
SALARIED_PERSONAL_LOAN_ID = "salaried_personal_loan"

TEMPLATES: dict[str, dict] = {
    "T1": {
        "purpose": "documents",
        "whatsapp": {
            "en": "Hi {{first_name}}, this is {{dsa_name}} about your {{product}} application (Ref {{ref}}). Pending documents: {{documents}}. Upload securely: {{upload_link}} (valid 7 days). Reply HELP for a call back.",
            "hi": "नमस्ते {{first_name}}, {{dsa_name}} की ओर से आपके {{product}} आवेदन (Ref {{ref}}) के बारे में। बाकी documents: {{documents}}। सुरक्षित upload करें: {{upload_link}} (7 दिन वैध)। Call back के लिए HELP लिखें।",
            "mr": "नमस्कार {{first_name}}, {{dsa_name}} कडून तुमच्या {{product}} अर्जाबद्दल (Ref {{ref}}). बाकी कागदपत्रे: {{documents}}. सुरक्षितपणे अपलोड करा: {{upload_link}} (7 दिवस वैध). कॉल बॅकसाठी HELP लिहा.",
        },
        "sms": {
            "en": "{{dsa_name}}: Hi {{first_name}}, docs pending for Ref {{ref}}: {{documents}}. Upload: {{upload_link}} (valid 7 days).",
            "hi": "{{dsa_name}}: नमस्ते {{first_name}}, Ref {{ref}} के बाकी documents: {{documents}}। Upload: {{upload_link}} (7 दिन वैध)।",
            "mr": "{{dsa_name}}: नमस्कार {{first_name}}, Ref {{ref}} ची बाकी कागदपत्रे: {{documents}}. अपलोड: {{upload_link}} (7 दिवस वैध).",
        },
    },
    "T2": {
        "purpose": "documents",
        "whatsapp": {
            "en": "Hi {{first_name}}, a gentle reminder from {{dsa_name}}. Your {{product}} application (Ref {{ref}}) is still waiting for: {{documents}}. You can upload here: {{upload_link}} (link valid till {{link_expiry}}). Reply HELP for a call back.",
            "hi": "नमस्ते {{first_name}}, {{dsa_name}} की ओर से एक याद दिलाना। आपके {{product}} आवेदन (Ref {{ref}}) के लिए ये documents अभी बाकी हैं: {{documents}}। यहाँ upload करें: {{upload_link}} (link {{link_expiry}} तक वैध)। Call back के लिए HELP लिखें।",
            "mr": "नमस्कार {{first_name}}, {{dsa_name}} कडून एक आठवण. तुमच्या {{product}} अर्जासाठी (Ref {{ref}}) ही कागदपत्रे अजून बाकी आहेत: {{documents}}. इथे अपलोड करा: {{upload_link}} (लिंक {{link_expiry}} पर्यंत वैध). कॉल बॅकसाठी HELP लिहा.",
        },
        "sms": {
            "en": "{{dsa_name}}: Reminder {{first_name}}: Ref {{ref}} still needs {{documents}}. Upload: {{upload_link}} (till {{link_expiry}}).",
            "hi": "{{dsa_name}}: याद दिलाना {{first_name}}: Ref {{ref}} के लिए अभी बाकी: {{documents}}। Upload: {{upload_link}} ({{link_expiry}} तक)।",
            "mr": "{{dsa_name}}: आठवण {{first_name}}: Ref {{ref}} साठी अजून बाकी: {{documents}}. अपलोड: {{upload_link}} ({{link_expiry}} पर्यंत).",
        },
    },
    "T5": {
        "purpose": "mismatch",
        "whatsapp": {
            "en": "Hi {{first_name}}, a detail on your application doesn't match one document ({{document}}). Please call {{dsa_phone}} or reply CALL.",
            "hi": "नमस्ते {{first_name}}, आपके आवेदन की एक जानकारी एक document ({{document}}) से मेल नहीं खा रही है। कृपया {{dsa_phone}} पर call करें या CALL लिखकर reply करें।",
            "mr": "नमस्कार {{first_name}}, तुमच्या अर्जातील एक माहिती एका कागदपत्राशी ({{document}}) जुळत नाही. कृपया {{dsa_phone}} वर कॉल करा किंवा CALL असे उत्तर द्या.",
        },
        "sms": {
            "en": "{{dsa_name}}: Hi {{first_name}}, a detail on your application (Ref {{ref}}) does not match one document ({{document}}). Please call {{dsa_phone}}.",
            "hi": "{{dsa_name}}: नमस्ते {{first_name}}, आवेदन (Ref {{ref}}) की एक जानकारी एक document ({{document}}) से मेल नहीं खाती। कृपया {{dsa_phone}} पर call करें।",
            "mr": "{{dsa_name}}: नमस्कार {{first_name}}, अर्जातील (Ref {{ref}}) एक माहिती एका कागदपत्राशी ({{document}}) जुळत नाही. कृपया {{dsa_phone}} वर कॉल करा.",
        },
    },
    "T6": {
        "purpose": "documents",
        "whatsapp": {
            "en": "Hi {{first_name}}, your {{product}} application (Ref {{ref}}) needs one more document: {{documents}}. Please upload it here: {{upload_link}} (valid 7 days). Reply HELP for a call back.",
            "hi": "नमस्ते {{first_name}}, आपके {{product}} आवेदन (Ref {{ref}}) के लिए एक और document चाहिए: {{documents}}। कृपया यहाँ upload करें: {{upload_link}} (7 दिन वैध)। Call back के लिए HELP लिखें।",
            "mr": "नमस्कार {{first_name}}, तुमच्या {{product}} अर्जासाठी (Ref {{ref}}) अजून एक कागदपत्र हवे आहे: {{documents}}. कृपया इथे अपलोड करा: {{upload_link}} (7 दिवस वैध). कॉल बॅकसाठी HELP लिहा.",
        },
        "sms": {
            "en": "{{dsa_name}}: Hi {{first_name}}, Ref {{ref}} needs one more item: {{documents}}. Upload: {{upload_link}} (valid 7 days).",
            "hi": "{{dsa_name}}: नमस्ते {{first_name}}, Ref {{ref}} के लिए एक चीज़ बाकी है: {{documents}}। Upload: {{upload_link}} (7 दिन वैध)।",
            "mr": "{{dsa_name}}: नमस्कार {{first_name}}, Ref {{ref}} साठी एक गोष्ट बाकी आहे: {{documents}}. अपलोड: {{upload_link}} (7 दिवस वैध).",
        },
    },
}

DOC_NAMES: dict[str, dict] = {
    "SALARY_SLIP": {"whatsapp": {"en": "salary slip", "hi": "salary slip", "mr": "salary slip"}, "sms": "salary slip"},
    "BANK_STATEMENT": {"whatsapp": {"en": "bank statement", "hi": "bank statement", "mr": "bank statement"}, "sms": "bank statement"},
    "FORM16_ITR": {"whatsapp": {"en": "Form-16 or ITR", "hi": "Form-16 या ITR", "mr": "Form-16 किंवा ITR"}, "sms": "Form-16"},
    "APPLICATION_FORM": {
        "whatsapp": {"en": "signed application form", "hi": "sign किया हुआ application form", "mr": "सही केलेला अर्ज"},
        "sms": "App form",
    },
    "PAN_COPY": {"whatsapp": {"en": "PAN card copy", "hi": "PAN card की copy", "mr": "PAN कार्डची प्रत"}, "sms": "PAN copy"},
    "AADHAAR_MASKED": {
        "whatsapp": {"en": "masked Aadhaar copy", "hi": "masked Aadhaar की copy", "mr": "masked आधारची प्रत"},
        "sms": "Aadhaar copy",
    },
    "ADDRESS_PROOF": {
        "whatsapp": {"en": "address proof", "hi": "पते का प्रमाण (address proof)", "mr": "पत्त्याचा पुरावा"},
        "sms": "Addr proof",
    },
    "EMPLOYMENT_PROOF": {
        "whatsapp": {
            "en": "company ID card or appointment letter",
            "hi": "company ID card या appointment letter",
            "mr": "कंपनी ओळखपत्र किंवा appointment letter",
        },
        "sms": "Emp proof",
    },
    "GST_RETURNS": {
        "whatsapp": {
            "en": "GST returns for the last 12 months",
            "hi": "पिछले 12 महीनों के GST returns",
            "mr": "मागील 12 महिन्यांचे GST returns",
        },
        "sms": "GST returns",
    },
}

MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "hi": ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"],
    "mr": ["जानेवारी", "फेब्रुवारी", "मार्च", "एप्रिल", "मे", "जून", "जुलै", "ऑगस्ट", "सप्टेंबर", "ऑक्टोबर", "नोव्हेंबर", "डिसेंबर"],
}
SMS_MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_WORDS = {
    "en": {"rangeJoin": " to ", "rangeSuffix": "", "listAnd": " and "},
    "hi": {"rangeJoin": " से ", "rangeSuffix": " तक", "listAnd": " और "},
    "mr": {"rangeJoin": " ते ", "rangeSuffix": "", "listAnd": " आणि "},
}

DEFAULT_CHECKLIST_DOCS = {
    "loan_application": ["APPLICATION_FORM"],
    "identity_details": ["PAN_COPY", "AADHAAR_MASKED"],
    "form16_itr": ["FORM16_ITR"],
}
LATEST_FY_ITEMS = {"form16_itr"}
SMS_EN_LIST_MAX = 36
MISMATCH_DOCS = {
    "pan": "PAN_COPY",
    "aadhaar_last4": "AADHAAR_MASKED",
    "applicant_name": "PAN_COPY",
    "employer": "SALARY_SLIP",
    "employer_vs_bank_credits": "BANK_STATEMENT",
    "declared_vs_slip_net": "SALARY_SLIP",
    "declared_vs_bank_credits": "BANK_STATEMENT",
    "slip_net_vs_bank_credits": "BANK_STATEMENT",
    "form16_vs_slip_gross": "FORM16_ITR",
    "declared_emis_vs_bank_debits": "BANK_STATEMENT",
}
NOT_DOCUMENT_CHECKS = {"foir", "unclassified_documents", "declared_net_salary"}
DOC_TYPE_CODES = {
    "identity_details": "PAN_COPY",
    "salary_slip": "SALARY_SLIP",
    "bank_statement": "BANK_STATEMENT",
    "form16_itr": "FORM16_ITR",
}
PRODUCT_LABELS = {
    "personal_loan": "personal loan",
    "business_loan": "business loan",
    "home_loan": "home loan",
    "car_loan": "car loan",
    "education_loan": "education loan",
    "lap": "loan against property",
}
HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "shri", "sri", "smt", "kumari", "km"}
_PAN_RE = re.compile(r"^[a-z]{5}[0-9]{4}[a-z]$", re.I)
_YM_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_ENGINE_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def normalize_status(value) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_-]+", " ", str(value or "").strip().upper()))


@dataclass
class ReminderItem:
    item_id: str
    label: str
    months: list[str] = field(default_factory=list)
    noun: str | None = None  # salary_slip | bank_statement
    docs: list[str] = field(default_factory=list)


@dataclass
class ReminderDraft:
    template: str
    language: str
    channel: str
    text: str
    documents: list[str]
    count_only: bool
    placeholders: list[str]


# ------------------------------------------------------------------ inputs
def first_name(applicant: str | None) -> str | None:
    tokens = [t for t in str(applicant or "").strip().split() if t]
    first = next((t for t in tokens if t.rstrip(".").lower() not in HONORIFICS), None)
    if not first or first.lower() == "unknown" or _PAN_RE.match(first) or re.search(r"\d", first):
        return None
    return first


def latest_form16_fy(as_of: str | None) -> str | None:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(as_of or ""))
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    end = year if (month > 6 or (month == 6 and day >= 15)) else year - 1
    return f"FY {end - 1}-{end % 100:02d}"


def _form16_year_hint(language: str, as_of: str | None) -> str:
    fy = latest_form16_fy(as_of)
    if fy:
        return f" ({fy})"
    return " (latest FY)" if language == "en" else ""


def product_label(product: str | None) -> str | None:
    return PRODUCT_LABELS.get(product or "")


def product_from_checklist(checklist_id: str | None) -> str | None:
    """'salaried_personal_loan' -> 'personal_loan' (the checklist ids name their product)."""
    for product in PRODUCT_LABELS:
        if checklist_id and product in checklist_id:
            return product
    return None


def _month_noun(text: str) -> str | None:
    if re.search(r"salary\s*slip", text, re.I):
        return "salary_slip"
    if re.search(r"bank\s*statement", text, re.I):
        return "bank_statement"
    return None


def _valid_months(months) -> list[str]:
    return sorted({m for m in (months or []) if isinstance(m, str) and _YM_RE.match(m)})


def _parse_missing_item_text(text: str) -> tuple[str, list[str]]:
    colon = text.rfind(":")
    if colon < 0:
        return text.strip(), []
    months: list[str] = []
    for part in (s.strip() for s in text[colon + 1:].split(",")):
        m = re.match(r"^([a-z]{3})[a-z]*\s+(\d{4})$", part, re.I)
        idx = _ENGINE_MONTHS.index(m.group(1).lower()) if m and m.group(1).lower() in _ENGINE_MONTHS else -1
        if not m or idx < 0:
            return text.strip(), []
        months.append(f"{m.group(2)}-{idx + 1:02d}")
    return text[:colon].strip(), _valid_months(months)


def _is_missing_required(row: dict) -> bool:
    return normalize_status(row.get("status")) == "MISSING" and row.get("required") is not False


def reminder_items(applicant: dict, checklist_id: str | None = None) -> list[ReminderItem]:
    rows = [r for r in (applicant.get("checklist") or []) if isinstance(r, dict) and _is_missing_required(r)]
    texts = [s for s in (applicant.get("missing_items") or []) if isinstance(s, str) and s.strip()]
    is_default = checklist_id == SALARIED_PERSONAL_LOAN_ID
    if not rows:
        items = []
        for i, text in enumerate(texts):
            prefix, months = _parse_missing_item_text(text)
            items.append(
                ReminderItem(
                    item_id=f"missing_{i + 1}",
                    label=prefix if months else text.strip(),
                    months=months,
                    noun=_month_noun(prefix) if months else None,
                )
            )
        return items
    aligned = len(texts) == len(rows)
    items = []
    for i, row in enumerate(rows):
        months = _valid_months(row.get("missing_months"))
        prefix = _parse_missing_item_text(texts[i])[0] if aligned else ""
        label = str(row.get("item") or row.get("item_id") or "").strip()
        items.append(
            ReminderItem(
                item_id=str(row.get("item_id") or ""),
                label=label,
                months=months,
                noun=(_month_noun(prefix) or _month_noun(label)) if months else None,
                docs=list(DEFAULT_CHECKLIST_DOCS.get(str(row.get("item_id")), [])) if (not months and is_default) else [],
            )
        )
    return items


def reminder_mismatch_docs(applicant: dict) -> list[str]:
    type_by_name = {d.get("document_name"): d.get("doc_type") for d in (applicant.get("documents") or []) if isinstance(d, dict)}
    codes: list[str] = []

    def add(code: str | None) -> None:
        if code and code not in codes:
            codes.append(code)

    for row in applicant.get("consistency") or []:
        if not isinstance(row, dict) or normalize_status(row.get("status")) != "MISMATCH":
            continue
        check_id = str(row.get("check_id") or "")
        if check_id in NOT_DOCUMENT_CHECKS:
            continue
        if check_id in MISMATCH_DOCS:
            add(MISMATCH_DOCS[check_id])
            continue
        for name in row.get("documents") or []:
            add(DOC_TYPE_CODES.get(type_by_name.get(name) or ""))
    return codes


# ------------------------------------------------------------------ months
def _ym(value: str) -> tuple[int, int]:
    m = _YM_RE.match(value)
    return int(m.group(1)), int(m.group(2))


def _contiguous(yms: list[tuple[int, int]]) -> bool:
    return all(i == 0 or y * 12 + m == yms[i - 1][0] * 12 + yms[i - 1][1] + 1 for i, (y, m) in enumerate(yms))


def whatsapp_month_phrase(months: list[str], language: str) -> str:
    yms = [_ym(m) for m in _valid_months(months)]
    if not yms:
        return ""
    names, words = MONTHS[language], MONTH_WORDS[language]

    def label(ym, with_year: bool) -> str:
        return f"{names[ym[1] - 1]} {ym[0]}" if with_year else names[ym[1] - 1]

    if len(yms) == 1:
        return label(yms[0], True)
    same_year = all(y == yms[0][0] for y, _ in yms)
    last = yms[-1]
    if len(yms) >= 3 and _contiguous(yms):
        return f"{label(yms[0], not same_year)}{words['rangeJoin']}{label(last, True)}{words['rangeSuffix']}"
    parts = [label(m, not same_year) for m in yms]
    listed = f"{', '.join(parts[:-1])}{words['listAnd']}{parts[-1]}"
    return f"{listed} {yms[0][0]}" if same_year else listed


def sms_month_phrase(months: list[str], language: str, with_year: bool) -> str:
    yms = [_ym(m) for m in _valid_months(months)]
    if not yms:
        return ""
    names = SMS_MONTHS_EN if language == "en" else MONTHS[language]
    same_year = all(y == yms[0][0] for y, _ in yms)

    def label(ym, year: bool) -> str:
        return f"{names[ym[1] - 1]} {ym[0]}" if year else names[ym[1] - 1]

    each = with_year and not same_year
    tail = f" {yms[0][0]}" if (with_year and same_year) else ""
    if len(yms) == 1:
        return label(yms[0], with_year)
    if _contiguous(yms):
        return f"{label(yms[0], each)}-{label(yms[-1], each)}{tail}"
    return f"{'/'.join(label(m, each) for m in yms)}{tail}"


# ------------------------------------------------------------------ wording
def _month_item_phrase(item: ReminderItem, language: str, channel: str, single: bool) -> str:
    many = len(item.months) > 1
    if channel == "whatsapp":
        m = whatsapp_month_phrase(item.months, language)
        if item.noun == "salary_slip":
            if language == "en":
                return f"salary slips for {m}" if many else f"{m} salary slip"
            if language == "hi":
                return f"{m} की salary slips" if many else f"{m} की salary slip"
            return f"{m} च्या salary slips" if many else f"{m} ची salary slip"
        if item.noun == "bank_statement":
            if language == "en":
                return f"bank statement for {m}"
            if language == "hi":
                return f"{m} का bank statement"
            return f"{m} चे bank statement"
        return f"{item.label}: {m}"
    m = sms_month_phrase(item.months, language, single)
    slip = "slips" if many else "slip"
    if item.noun == "salary_slip":
        if not single:
            return f"{m} {slip}"
        if language == "en":
            return f"{slip} for {m}"
        if language == "hi":
            return f"{m} की {slip}"
        return f"{m} {'च्या' if many else 'ची'} {slip}"
    if item.noun == "bank_statement":
        if language == "en":
            return f"bank stmt for {m}" if single else f"{m} bank stmt"
        if not single:
            return f"{m} bank statement"
        return f"{m} का bank statement" if language == "hi" else f"{m} चे bank statement"
    return f"{item.label}: {m}"


def _doc_name(code: str, language: str, channel: str) -> str:
    name = DOC_NAMES[code]
    return name["whatsapp"][language] if channel == "whatsapp" else name["sms"]


def reminder_document_phrases(
    items: list[ReminderItem], language: str, channel: str, single: bool = False, as_of: str | None = None
) -> list[str]:
    phrases: list[str] = []
    for item in items:
        if item.months:
            phrases.append(_month_item_phrase(item, language, channel, single))
        elif item.docs:
            fy = _form16_year_hint(language, as_of) if (channel == "whatsapp" and item.item_id in LATEST_FY_ITEMS) else ""
            phrases.extend(f"{_doc_name(code, language, channel)}{fy}" for code in item.docs)
        else:
            phrases.append(item.label)
    return phrases


# ------------------------------------------------------------------ build
def preferred_template(applicant: dict, checklist_id: str | None = None, after_call: bool = True) -> str | None:
    """The message a voice call ends with: T6 for one month-based item, T1 for missing items
    (T2 when not right after a call), T5 for a document mismatch only; None when nothing is to be asked."""
    items = reminder_items(applicant, checklist_id)
    if items:
        if len(items) == 1 and items[0].months:
            return "T6"
        return "T1" if after_call else "T2"
    if reminder_mismatch_docs(applicant):
        return "T5"
    return None


def placeholders(text: str) -> list[str]:
    seen: list[str] = []
    for key in _PLACEHOLDER_RE.findall(text):
        if key not in seen:
            seen.append(key)
    return seen


def build_reminder(
    applicant: dict,
    template: str,
    language: str,
    channel: str = "whatsapp",
    checklist_id: str | None = None,
    product: str | None = None,
    as_of: str | None = None,
    values: dict[str, str] | None = None,
) -> ReminderDraft:
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template}")
    if language not in LANGUAGES or channel not in CHANNELS:
        raise ValueError("language must be en, hi or mr and channel whatsapp or sms")
    tpl = TEMPLATES[template]
    filled: dict[str, str] = {k: v for k, v in (values or {}).items() if v}
    name = first_name(applicant.get("applicant"))
    if name:
        filled["first_name"] = name
    label = product_label(product)
    if label:
        filled["product"] = label
    documents: list[str] = []
    count_only = False
    if tpl["purpose"] == "mismatch":
        documents = [
            DOC_NAMES[c]["whatsapp"][language] if channel == "whatsapp" else DOC_NAMES[c]["whatsapp"]["en"]
            for c in reminder_mismatch_docs(applicant)
        ]
        if documents:
            filled["document"] = ", ".join(documents)
    else:
        items = reminder_items(applicant, checklist_id)
        documents = reminder_document_phrases(items, language, channel, template == "T6", as_of)
        listed = ", ".join(documents)
        count_only = channel == "sms" and language == "en" and template != "T6" and len(listed) > SMS_EN_LIST_MAX
        if count_only:
            filled["documents"] = "1 document" if len(documents) == 1 else f"{len(documents)} documents"
        elif documents:
            filled["documents"] = listed
    body = tpl["whatsapp" if channel == "whatsapp" else "sms"][language]
    text = _PLACEHOLDER_RE.sub(lambda m: filled.get(m.group(1), m.group(0)), body)
    return ReminderDraft(
        template=template,
        language=language,
        channel=channel,
        text=text,
        documents=documents,
        count_only=count_only,
        placeholders=placeholders(text),
    )
