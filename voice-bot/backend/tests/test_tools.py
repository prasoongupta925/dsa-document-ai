# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""The loan tools against the fake IDP API (demo projects, Sneha's verdict and eligibility)."""

import asyncio
import copy
import json

import pytest

import fixtures
from fakes import IDP_URL, FakeIdp
from idp_client import IdpClient, IdpError
from tools import CallSession, LoanTools, inr, lakh, mask_ids, name_tokens, plain, run_tool, tool_schemas


@pytest.fixture
def fake():
    return FakeIdp()


@pytest.fixture
def make_tools(fake, settings):
    def _make(channel="browser", language="hi", emit=None):
        session = CallSession(call_id="c1", channel=channel, language=language, idp_user="rohan.iyer", emit=emit)
        return LoanTools(IdpClient(IDP_URL, transport=fake.transport()), settings, session), session

    return _make


@pytest.mark.parametrize(
    "heard",
    ["Sneha Kulkarni", "sneha kulkarni ji", "Sneha Anil Kulkarni", "Mrs. Sneha Kulkarnee", "Snehaa Kulkarni", "Sneha"],
)
async def test_finds_the_file_by_name_with_speech_spelling_slips(make_tools, heard):
    tools, _ = make_tools()
    project, reason = await tools.find_project(heard)
    assert reason == "ok" and project["project_id"] == fixtures.SNEHA_PROJECT_ID


@pytest.mark.parametrize(
    "heard,reason",
    [("Priya Sharma", "not_found"), ("", "no_name"), ("Mr. ji", "no_name"), ("Telecaller QA", "not_found"), ("Sneha Patil", "ambiguous")],
)
async def test_unknown_ambiguous_and_qa_project_are_never_matched(make_tools, heard, reason):
    tools, _ = make_tools()
    project, got = await tools.find_project(heard)
    assert project is None and got == reason


async def test_projects_are_listed_once_per_minute(make_tools, fake):
    tools, _ = make_tools()
    await tools.find_project("Sneha Kulkarni")
    await tools.find_project("Rahul Deshmukh")
    assert fake.paths("GET").count("/projects") == 1


async def test_file_status_in_plain_words_without_ids_or_file_names(make_tools, fake):
    events = []

    async def emit(e):
        events.append(e)

    tools, session = make_tools(emit=emit)
    result = await tools.file_status("Sneha Kulkarni")
    assert result["found"] and result["verdict"] == "NOT READY" and result["applicant"] == "Sneha Anil Kulkarni"
    assert result["missing_documents"] == ["June 2026 salary slip", "bank statement for March to May 2026", "Form-16 or ITR (FY 2025-26)"]
    assert result["mismatches"][0].startswith("Declared net salary vs salary slips: declared ₹65,000 vs slip net ₹58,000")
    blob = json.dumps(result, ensure_ascii=False)
    assert "CKRPK7314M" not in blob and ".pdf" not in blob
    # The file check is filtered by the file's own spelling of the name, with the caller's audit label.
    check = next(c for c in fake.calls if c.path.endswith("/file-check"))
    assert check.body == {"applicant": "Sneha Kulkarni"} and check.headers["x-user-id"] == "rohan.iyer"
    assert session.project_id == fixtures.SNEHA_PROJECT_ID and session.verdict == "NOT READY"
    assert events == [{"event": "file_status", "verdict": "NOT READY", "missing": result["missing_documents"], "mismatches": 2}]


async def test_file_status_for_a_file_with_no_matching_applicant_checks_everyone(make_tools, fake):
    tools, _ = make_tools()
    fake.file_checks[fixtures.SNEHA_PROJECT_ID] = {**copy.deepcopy(fixtures.SNEHA_FILE_CHECK)}
    result = await tools.file_status("Sneha")
    assert result["found"]
    other = await tools.file_status("Rahul Deshmukh")  # project exists, file check returns no applicant
    assert other == {"found": False, "reason": "no_applicant", "name_heard": "Rahul Deshmukh", "say": other["say"]}
    rahul_checks = [c for c in fake.calls if "DemoRahul" in c.path]
    assert [c.body for c in rahul_checks] == [{"applicant": "Rahul Deshmukh"}, {}]  # then every applicant


async def test_pending_documents_are_reported(make_tools, fake):
    pending = copy.deepcopy(fixtures.SNEHA_FILE_CHECK)
    pending["pending_documents"] = [{"document_id": "x", "document_name": "06_form16.pdf", "status": "processing"}]
    fake.file_checks[fixtures.SNEHA_PROJECT_ID] = pending
    tools, _ = make_tools()
    result = await tools.file_status("Sneha Kulkarni")
    assert result["documents_still_being_read"] == 1 and "still being read" in result["say"]


async def test_eligibility_is_looked_up_by_pan_and_says_lakh_without_the_pan(make_tools, fake):
    tools, _ = make_tools()
    result = await tools.eligibility("Sneha Kulkarni")
    assert result["available"] and result["indicative_only"]
    # The IDP echoes the lookup key (the PAN) in "applicant": the tool reports the file's name instead.
    assert result["applicant"] == "Sneha Anil Kulkarni" and "CKRPK7314M" not in json.dumps(result, ensure_ascii=False)
    assert result["best"] == {"lender": "Bajaj Finance", "amount": "9.28 lakh", "emi_per_month": "₹17,391", "tenure": "7 years",
                              "indicative_rate_percent": 14.0}
    assert result["requested"] == {"amount": "4 lakh", "tenure": "3 years"}
    assert result["income_used_per_month"] == "₹58,000"
    hdfc = next(x for x in result["not_eligible"] if x["lender"] == "HDFC Bank")
    assert hdfc["why"][0] == "CIBIL score 712 is below HDFC Bank's minimum 750"
    assert "lender decides" in result["say"]
    calc = [c for c in fake.calls if c.path.endswith("/eligibility/calculate")]
    assert [c.body for c in calc] == [{"applicant": "CKRPK7314M"}]  # the web app's key: one call
    # The file check of this call is reused (no second file check for the same name).
    assert len([c for c in fake.calls if c.path.endswith("/file-check")]) == 1


async def test_eligibility_saved_under_the_name_is_found_next_then_says_not_available(make_tools, fake):
    fake.eligibility_keys = {"sneha anil kulkarni"}  # inputs saved before the IDP keyed them by PAN
    tools, _ = make_tools()
    assert (await tools.eligibility("Sneha Kulkarni"))["available"]
    calc = [c.body for c in fake.calls if c.path.endswith("/eligibility/calculate")]
    assert calc == [{"applicant": "CKRPK7314M"}, {"applicant": "Sneha Anil Kulkarni"}]
    fake.eligibility_keys = set()
    tools2, _ = make_tools()
    result = await tools2.eligibility("Sneha Kulkarni")
    assert result["available"] is False and "CIBIL" in result["say"]


async def test_reminder_text_is_the_template_with_only_missing_items(make_tools):
    events = []

    async def emit(e):
        events.append(e)

    tools, _ = make_tools(emit=emit)
    result = await tools.reminder("Sneha Kulkarni", "mr")
    assert result["drafted"] and result["sent"] is False and result["template"] == "T1" and result["language"] == "Marathi"
    assert result["text"] == (
        "नमस्कार Sneha, Varunika Loan Partners कडून तुमच्या personal loan अर्जाबद्दल (Ref {{ref}}). बाकी कागदपत्रे: "
        "जून 2026 ची salary slip, मार्च ते मे 2026 चे bank statement, Form-16 किंवा ITR (FY 2025-26). "
        "सुरक्षितपणे अपलोड करा: {{upload_link}} (7 दिवस वैध). कॉल बॅकसाठी HELP लिहा."
    )
    assert result["placeholders_for_team"] == ["ref", "upload_link"]
    assert events[0]["event"] == "reminder" and events[0]["text"] == result["text"]
    sms = await tools.reminder("Sneha Kulkarni", "en", "sms")
    assert sms["channel"] == "sms" and sms["text"].startswith("Varunika Loan Partners: Hi Sneha, docs pending")


async def test_reminder_when_nothing_is_missing(make_tools, fake):
    ready = copy.deepcopy(fixtures.SNEHA_FILE_CHECK)
    person = ready["applicants"][0]
    person.update(verdict="READY", missing_items=[], mismatches=[], consistency=[])
    for row in person["checklist"]:
        row.update(status="PRESENT", missing_months=[])
    fake.file_checks[fixtures.SNEHA_PROJECT_ID] = ready
    tools, _ = make_tools()
    assert (await tools.reminder("Sneha Kulkarni", "hi"))["drafted"] is False
    status = await tools.file_status("Sneha Kulkarni")
    assert status["verdict"] == "READY" and "lender still decides" in status["say"]


async def test_run_tool_turns_failures_into_sayable_results():
    async def slow():
        await asyncio.sleep(5)

    async def broken():
        raise IdpError(502, "projects/x/file-check")

    async def crash():
        raise KeyError("x")

    assert (await run_tool("file_status", slow(), timeout_s=0.05))["error"] == "timeout"
    assert (await run_tool("file_status", broken()))["error"] == "unavailable"
    assert (await run_tool("file_status", crash()))["error"] == "failed"


async def test_idp_down_is_reported_not_raised(settings):
    import httpx

    def down(request):
        return httpx.Response(503, json={"detail": "Service Unavailable"})

    session = CallSession(call_id="c2", channel="plivo")
    tools = LoanTools(IdpClient(IDP_URL, transport=httpx.MockTransport(down)), settings, session)
    result = await run_tool("file_status", tools.file_status("Sneha Kulkarni"))
    assert result["error"] == "unavailable" and "call back" in result["say"]


def test_tool_schemas_per_channel():
    browser = {s.name for s in tool_schemas("browser")}
    phone = {s.name for s in tool_schemas("plivo")}
    whatsapp = {s.name for s in tool_schemas("whatsapp")}
    assert browser == {"file_status", "eligibility", "reminder", "switch_language", "end_call"}
    assert phone == browser | {"transfer_to_human", "verify_caller"}
    assert whatsapp == browser | {"verify_caller"}
    reminder = next(s for s in tool_schemas("browser") if s.name == "reminder")
    assert reminder.properties["language"]["enum"] == ["hi", "en", "mr"] and reminder.required == ["applicant", "language"]


def test_money_and_text_helpers():
    assert inr(1600000.4) == "₹16,00,000" and inr(58000) == "₹58,000" and inr(999) == "₹999" and inr(None) is None
    assert inr(123456789) == "₹12,34,56,789"
    assert lakh(1600000) == "16 lakh" and lakh(450000) == "4.5 lakh" and lakh(928000) == "9.28 lakh"
    assert lakh(95000) == "₹95,000" and lakh(25000000) == "2.5 crore"
    assert mask_ids("PAN CKRPK7314M, Aadhaar 1234 5678 9012") == "PAN XXXXXX314M, Aadhaar XXXX XXXX XXXX"
    assert plain("covers Jun 2026 (05_bank_statement_2026-06.pdf); declared ₹65,000 [01_form.pdf]") == "covers Jun 2026; declared ₹65,000"
    assert name_tokens("Shri Rahul V. Deshmukh ji") == ["rahul", "deshmukh"]


# ------------------------------------------------------------------ who may hear a file
@pytest.fixture
def phone_tools(fake, settings):
    def _make(caller_number=None, outbound=False, hint=None, channel="plivo"):
        session = CallSession(call_id="p1", channel=channel, idp_user="voice-bot-phone", caller_number=caller_number,
                              outbound=outbound, applicant_hint=hint)
        return LoanTools(IdpClient(IDP_URL, transport=fake.transport()), settings, session), session

    return _make


async def test_inbound_caller_id_matching_the_file_is_verified(phone_tools, fake):
    tools, session = phone_tools(caller_number="+91 90000 00102")
    result = await tools.file_status("Sneha Kulkarni")
    assert result["found"] and result["verdict"] == "NOT READY"
    assert session.verified_for == "Sneha Anil Kulkarni"
    inputs = [c for c in fake.calls if c.path.endswith("/eligibility/inputs")]
    assert len(inputs) == 1


async def test_inbound_unknown_number_must_give_the_date_of_birth(phone_tools):
    tools, session = phone_tools(caller_number="+91" "9811111111", channel="whatsapp")
    gated = await tools.file_status("Sneha Kulkarni")
    assert gated["needs_verification"] and "missing_documents" not in gated and "Sneha Anil" not in json.dumps(gated)
    assert (await tools.eligibility("Sneha Kulkarni"))["needs_verification"]
    assert (await tools.reminder("Sneha Kulkarni", "hi"))["needs_verification"]
    wrong = await tools.verify_caller("Sneha Kulkarni", "1995-08-24")
    assert wrong["verified"] is False and wrong["attempts_left"] == 1 and "1995" not in json.dumps(wrong)
    right = await tools.verify_caller("Sneha Kulkarni", "23 August 1995")
    assert right["verified"] is True and session.verified_for == "Sneha Anil Kulkarni"
    assert (await tools.file_status("Sneha Kulkarni"))["missing_documents"]


async def test_caller_id_and_dob_come_from_the_inputs_saved_under_the_pan(phone_tools, fake):
    tools, session = phone_tools(caller_number="+91" "9000000102")
    assert (await tools.file_status("Sneha Kulkarni"))["verdict"] == "NOT READY"
    assert session.verified_for == "Sneha Anil Kulkarni"
    lookups = [c.query for c in fake.calls if c.path.endswith("/eligibility/inputs")]
    assert lookups == ["applicant=CKRPK7314M"]  # the key the web app saves under: one call


async def test_without_saved_inputs_only_the_documents_date_of_birth_can_verify(phone_tools, fake):
    fake.eligibility_keys = set()
    fake.draft_dob = "1995-08-23"
    tools, session = phone_tools(caller_number="+91" "9000000102")  # the right mobile, but none saved to match
    gated = await tools.file_status("Sneha Kulkarni")
    assert gated["needs_verification"] and session.verified_for is None
    lookups = [c.query for c in fake.calls if c.path.endswith("/eligibility/inputs")]
    assert lookups == ["applicant=CKRPK7314M", "applicant=Sneha%20Anil%20Kulkarni"]
    assert (await tools.verify_caller("Sneha Kulkarni", "23-08-1995"))["verified"] is True
    fake.draft_dob = None
    other, _ = phone_tools()
    result = await other.verify_caller("Sneha Kulkarni", "23-08-1995")
    assert result["verified"] is False and "no date of birth" in result["say"]


async def test_two_wrong_dates_lock_the_file_for_this_call(phone_tools):
    tools, session = phone_tools()
    assert (await tools.verify_caller("Sneha Kulkarni", "01/01/1990"))["attempts_left"] == 1
    assert (await tools.verify_caller("Sneha Kulkarni", "02/01/1990"))["attempts_left"] == 0
    assert (await tools.verify_caller("Sneha Kulkarni", "23/08/1995"))["verified"] is False
    assert (await tools.file_status("Sneha Kulkarni"))["reason"] == "not_verified"


async def test_outbound_call_only_discusses_its_own_file(phone_tools):
    tools, _ = phone_tools(outbound=True, hint="Sneha Kulkarni")
    assert (await tools.file_status("Sneha Kulkarni"))["verdict"] == "NOT READY"
    other = await tools.file_status("Rahul Deshmukh")
    assert other["found"] is False and other["reason"] in ("other_file", "no_applicant")
    assert (await tools.verify_caller("Sneha Kulkarni", "x"))["verified"] is True  # no check on a call we placed


async def test_browser_staff_are_not_asked_to_verify(make_tools):
    tools, _ = make_tools()
    assert (await tools.file_status("Sneha Kulkarni"))["found"] is True


@pytest.mark.parametrize(
    "said,iso",
    [("1995-08-23", "1995-08-23"), ("23-08-1995", "1995-08-23"), ("23/8/1995", "1995-08-23"), ("23 August 1995", "1995-08-23"),
     ("23rd Aug 1995", "1995-08-23"), ("August 23, 1995", "1995-08-23"), ("31/02/1995", None), ("yesterday", None), ("", None)],
)
def test_date_of_birth_parsing(said, iso):
    from tools import parse_dob

    assert parse_dob(said) == iso
