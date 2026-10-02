# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""The Python port gives the same texts as the IDP web app (expected strings from
packages/frontend/src/lib/reminder.test.ts)."""

import copy
import re

import pytest

import fixtures
import reminders as r

SNEHA = fixtures.SNEHA_FILE_CHECK["applicants"][0]
CHECKLIST = fixtures.SNEHA_FILE_CHECK["checklist"]["id"]

SNEHA_STATEMENT_ONLY = {
    **copy.deepcopy(SNEHA),
    "checklist": [
        row if row["item_id"] == "bank_statement" else {**row, "status": "PRESENT", "ok": True, "missing_months": []}
        for row in SNEHA["checklist"]
    ],
    "missing_items": ["Bank statement: Mar 2026, Apr 2026, May 2026"],
    "consistency": [],
    "mismatches": [],
}

AMIT_TWO_SLIPS = {
    "applicant": "Amit Suresh Patil",
    "pan": "ABCPP1234K",
    "verdict": "NOT READY",
    "documents": [
        {"document_name": "application.pdf", "doc_type": "loan_application"},
        {"document_name": "slip_aug.pdf", "doc_type": "salary_slip"},
    ],
    "checklist": [
        {"item_id": "application", "item": "Loan application form", "required": True, "status": "PRESENT"},
        {"item_id": "salary_slips", "item": "Salary slips (last 3 months)", "required": True, "status": "MISSING",
         "missing_months": ["2026-06", "2026-07"]},
        {"item_id": "address_proof", "item": "Address proof", "required": True, "status": "REVIEW"},
        {"item_id": "form16", "item": "Form-16 / ITR", "required": False, "status": "MISSING"},
    ],
    "consistency": [],
    "missing_items": ["Salary slips: Jun 2026, Jul 2026"],
    "mismatches": [],
}

AMIT_PAN_MISMATCH = {
    **copy.deepcopy(AMIT_TWO_SLIPS),
    "pan": "DMVPP5928L",
    "checklist": [{**row, "status": row["status"] if row["status"] == "REVIEW" else "PRESENT", "missing_months": []}
                  for row in AMIT_TWO_SLIPS["checklist"]],
    "consistency": [{"check_id": "pan", "check": "PAN", "status": "MISMATCH",
                     "detail": "DMVPP5926L on application.pdf; DMVPP5928L on slip_aug.pdf", "documents": ["application.pdf", "slip_aug.pdf"]}],
    "missing_items": [],
    "mismatches": ["PAN: DMVPP5926L on application.pdf; DMVPP5928L on slip_aug.pdf (differs at character 9)"],
}


def no_ids_or_amounts(text: str):
    assert not re.search(r"[A-Z]{5}\d{4}[A-Z]", text)
    assert "₹" not in text and not re.search(r"\d{2},\d{3}", text) and ".pdf" not in text


def test_items_are_exactly_the_verdicts_missing_rows():
    items = r.reminder_items(SNEHA, CHECKLIST)
    assert [(i.item_id, i.noun, i.months, i.docs) for i in items] == [
        ("salary_slips", "salary_slip", ["2026-06"], []),
        ("bank_statement", "bank_statement", ["2026-03", "2026-04", "2026-05"], []),
        ("form16_itr", None, [], ["FORM16_ITR"]),
    ]
    amit = r.reminder_items(AMIT_TWO_SLIPS, CHECKLIST)
    assert [i.item_id for i in amit] == ["salary_slips"] and amit[0].months == ["2026-06", "2026-07"]


def test_falls_back_to_missing_items_text():
    items = r.reminder_items({**SNEHA, "checklist": [], "missing_items": ["Salary slip: May 2026, Jun 2026", "Address proof"]}, "ss_pl_sal")
    assert [(i.label, i.noun, i.months) for i in items] == [
        ("Salary slip", "salary_slip", ["2026-05", "2026-06"]),
        ("Address proof", None, []),
    ]


def test_t2_whatsapp_in_three_languages():
    def text(language):
        return r.build_reminder(SNEHA, "T2", language, "whatsapp", checklist_id=CHECKLIST, product="personal_loan").text

    assert text("en") == (
        "Hi Sneha, a gentle reminder from {{dsa_name}}. Your personal loan application (Ref {{ref}}) is still waiting for: "
        "June 2026 salary slip, bank statement for March to May 2026, Form-16 or ITR (latest FY). You can upload here: "
        "{{upload_link}} (link valid till {{link_expiry}}). Reply HELP for a call back."
    )
    assert text("hi") == (
        "नमस्ते Sneha, {{dsa_name}} की ओर से एक याद दिलाना। आपके personal loan आवेदन (Ref {{ref}}) के लिए ये documents अभी बाकी हैं: "
        "जून 2026 की salary slip, मार्च से मई 2026 तक का bank statement, Form-16 या ITR। यहाँ upload करें: {{upload_link}} "
        "(link {{link_expiry}} तक वैध)। Call back के लिए HELP लिखें।"
    )
    assert text("mr") == (
        "नमस्कार Sneha, {{dsa_name}} कडून एक आठवण. तुमच्या personal loan अर्जासाठी (Ref {{ref}}) ही कागदपत्रे अजून बाकी आहेत: "
        "जून 2026 ची salary slip, मार्च ते मे 2026 चे bank statement, Form-16 किंवा ITR. इथे अपलोड करा: {{upload_link}} "
        "(लिंक {{link_expiry}} पर्यंत वैध). कॉल बॅकसाठी HELP लिहा."
    )
    for language in r.LANGUAGES:
        no_ids_or_amounts(text(language))


def test_form16_year_from_the_verdict_date():
    draft = r.build_reminder(SNEHA, "T1", "hi", "whatsapp", checklist_id=CHECKLIST, as_of="2026-09-28")
    assert "Form-16 या ITR (FY 2025-26)" in draft.text
    assert r.latest_form16_fy("2026-06-14") == "FY 2024-25" and r.latest_form16_fy("2026-06-15") == "FY 2025-26"
    assert r.latest_form16_fy("bad") is None


def test_sms_short_forms():
    def docs(language):
        return ", ".join(r.build_reminder(SNEHA, "T1", language, "sms", checklist_id=CHECKLIST).documents)

    assert docs("en") == "Jun slip, Mar-May bank stmt, Form-16"
    assert docs("hi") == "जून slip, मार्च-मई bank statement, Form-16"
    assert docs("mr") == "जून slip, मार्च-मे bank statement, Form-16"


def test_unknown_values_stay_visible_and_known_ones_fill_in():
    draft = r.build_reminder(SNEHA, "T1", "en", "whatsapp", checklist_id=CHECKLIST)
    assert draft.placeholders == ["dsa_name", "product", "ref", "upload_link"]
    filled = r.build_reminder(SNEHA, "T1", "en", "whatsapp", checklist_id=CHECKLIST, product="personal_loan",
                              values={"dsa_name": "Varunika Loan Partners", "dsa_phone": ""})
    assert filled.placeholders == ["ref", "upload_link"]
    assert filled.text.startswith("Hi Sneha, this is Varunika Loan Partners about your personal loan application")
    assert "first_name" in r.build_reminder({**SNEHA, "applicant": "Unknown"}, "T2", "hi", "sms").placeholders


def test_mismatch_names_only_the_document_type():
    for language in r.LANGUAGES:
        text = r.build_reminder(AMIT_PAN_MISMATCH, "T5", language, "whatsapp").text
        no_ids_or_amounts(text)
        assert "DMVPP" not in text
    assert r.build_reminder(AMIT_PAN_MISMATCH, "T5", "en", "whatsapp").text == (
        "Hi Amit, a detail on your application doesn't match one document (PAN card copy). Please call {{dsa_phone}} or reply CALL."
    )
    assert r.build_reminder(AMIT_PAN_MISMATCH, "T5", "hi", "whatsapp").documents == ["PAN card की copy"]
    assert r.build_reminder(SNEHA, "T5", "en", "whatsapp").documents == ["salary slip", "bank statement"]


def test_preferred_template_for_a_voice_call():
    assert r.preferred_template(SNEHA, CHECKLIST) == "T1"  # right after a call
    assert r.preferred_template(SNEHA, CHECKLIST, after_call=False) == "T2"
    assert r.preferred_template(SNEHA_STATEMENT_ONLY, CHECKLIST) == "T6"
    assert r.preferred_template(AMIT_PAN_MISMATCH, CHECKLIST) == "T5"
    review_only = {**AMIT_PAN_MISMATCH, "consistency": [], "mismatches": []}
    assert r.preferred_template(review_only, CHECKLIST) is None
    foir_only = {**review_only, "consistency": [{"check_id": "foir", "status": "MISMATCH", "documents": []}]}
    assert r.preferred_template(foir_only, CHECKLIST) is None


def test_months_are_worded_like_the_glossary():
    assert r.whatsapp_month_phrase(["2026-06"], "en") == "June 2026"
    assert r.whatsapp_month_phrase(["2026-05", "2026-03", "2026-04"], "hi") == "मार्च से मई 2026 तक"
    assert r.whatsapp_month_phrase(["2026-06", "2026-07"], "mr") == "जून आणि जुलै 2026"
    assert r.whatsapp_month_phrase(["2025-12", "2026-01", "2026-02"], "en") == "December 2025 to February 2026"
    assert r.whatsapp_month_phrase(["2026-03", "2026-05"], "en") == "March and May 2026"
    assert r.sms_month_phrase(["2026-03", "2026-04", "2026-05"], "en", False) == "Mar-May"
    assert r.sms_month_phrase(["2026-03", "2026-05"], "hi", True) == "मार्च/मई 2026"
    assert r.sms_month_phrase(["2025-12", "2026-01"], "en", True) == "Dec 2025-Jan 2026"


def test_first_name_never_an_id():
    assert r.first_name("Sneha Anil Kulkarni") == "Sneha"
    assert r.first_name("Mr. Amit S. Patil") == "Amit"
    assert r.first_name("Unknown") is None
    assert r.first_name("CKRPK7314M") is None
    assert r.first_name("") is None


def test_several_missing_slip_months():
    assert r.build_reminder(AMIT_TWO_SLIPS, "T2", "en", "whatsapp", checklist_id=CHECKLIST).documents == ["salary slips for June and July 2026"]
    assert r.build_reminder(AMIT_TWO_SLIPS, "T2", "mr", "whatsapp", checklist_id=CHECKLIST).documents == ["जून आणि जुलै 2026 च्या salary slips"]


def test_t6_one_item_left():
    draft = r.build_reminder(SNEHA_STATEMENT_ONLY, "T6", "en", "sms", checklist_id=CHECKLIST)
    assert draft.documents == ["bank stmt for Mar-May 2026"]


def test_english_sms_lists_only_a_count_when_too_long():
    long_items = {**SNEHA, "checklist": [], "missing_items": ["Address proof", "Company ID card", "GST returns", "Rent agreement"]}
    draft = r.build_reminder(long_items, "T1", "en", "sms")
    assert draft.count_only and "4 documents" in draft.text


def test_bad_arguments():
    with pytest.raises(ValueError):
        r.build_reminder(SNEHA, "T9", "en")
    with pytest.raises(ValueError):
        r.build_reminder(SNEHA, "T1", "fr")
    assert r.product_from_checklist("salaried_personal_loan") == "personal_loan"
    assert r.product_from_checklist(None) is None
