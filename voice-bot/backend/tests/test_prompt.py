# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Persona and compliance rules: what the bot must always say, and what it may never say."""

from datetime import datetime

import pytest

from prompt import FILLERS, GOODBYE, IDLE_PROMPT, IST, SAY_AGAIN, TECH_PROBLEM, TIME_UP, TRANSFER, TRANSFER_FAILED, Persona, greeting, short_lang, system_prompt
from speech import ComplianceTextFilter, sanitize_speech

P = Persona(assistant_name="Varunika Loans", dsa_name="Varunika Loan Partners")


@pytest.mark.parametrize("lang", ["hi", "en", "mr"])
@pytest.mark.parametrize("channel", ["browser", "plivo", "exotel", "whatsapp"])
def test_opening_line_discloses_ai_and_recording(lang, channel):
    text = greeting(P, lang, channel)
    assert "AI assistant" in text and "Varunika Loans" in text and "Varunika Loan Partners" in text
    assert "record" in text
    assert ("agent" in text) == (channel != "browser")  # a person on request, where the channel can transfer
    assert "pakka" not in text.lower() and "OTP" not in text


def test_outbound_opening_confirms_the_person_by_first_name():
    assert "Sneha जी" in greeting(P, "hi", "plivo", "Sneha")
    assert greeting(P, "en", "exotel", "Sneha").endswith("Am I speaking with Sneha?")
    assert "Sneha यांच्याशी" in greeting(P, "mr", "plivo", "Sneha")


def test_system_prompt_rules():
    text = system_prompt(P, "browser", "hi", now=datetime(2026, 10, 1, 11, 30, tzinfo=IST))
    must = [
        "AI voice assistant of Varunika Loan Partners",
        "it is not the lender",
        "Never claim to be a human, a bank or the lender",
        "Devanagari",
        "Marathi",
        "in lakh",
        "Every fact about a loan file comes from your tools",
        "Never invent",
        "the lender decides",
        "Never say pakka, guaranteed or 100 percent",
        "OTP",
        "PIN",
        "full PAN",
        "full Aadhaar",
        "call mat karo",
        "end_call",
        "switch_language",
        "Thursday, 01 October 2026",
        "Ask for the applicant's full name",
    ]
    for phrase in must:
        assert phrase in text, phrase
    assert "transfer_to_human" not in text  # browser: a call back is offered instead
    assert "verify_caller" not in text  # signed-in staff
    phone = system_prompt(P, "plivo", "hi")
    assert "transfer_to_human" in phone
    assert "needs_verification" in phone and "verify_caller" in phone and "Never say a stored date of birth" in phone
    assert "verify_caller" in system_prompt(P, "whatsapp", "hi")


def test_system_prompt_for_an_outbound_call():
    text = system_prompt(P, "exotel", "en", applicant="Sneha Kulkarni")
    assert "outbound call about the loan file of Sneha Kulkarni" in text
    assert "confirm you are speaking with that person" in text
    assert "The caller's language at the start: English." in text


def test_fixed_lines_exist_in_every_language():
    for table in (GOODBYE, IDLE_PROMPT, TIME_UP, TRANSFER, TRANSFER_FAILED, TECH_PROBLEM, SAY_AGAIN, *FILLERS.values()):
        assert set(table) == {"hi", "en", "mr"} and all(table.values())


@pytest.mark.parametrize("value,expected", [("hindi", "hi"), ("hi-IN", "hi"), ("Marathi", "mr"), ("en-IN", "en"), ("english", "en"), ("", "hi"), ("tamil", "hi")])
def test_language_names(value, expected):
    assert short_lang(value) == expected


@pytest.mark.parametrize(
    "said",
    [
        "हाँ, loan pakka ho jayega!",
        "Aapka loan pakka approve ho jayega.",
        "Your loan is guaranteed.",
        "100% loan milega.",
        "आपका loan पक्का है।",
        "Don't worry, it will definitely approve.",
    ],
)
def test_approval_promises_are_rewritten(said):
    out = sanitize_speech(said, "hi")
    assert out in ("आख़िरी फ़ैसला lender का होगा, यह सिर्फ़ अंदाज़ा है।", "The final decision is the lender's; this is only indicative.")


def test_indicative_answers_pass_untouched():
    for said in (
        "Bajaj Finance से लगभग 9.28 lakh का loan अंदाज़न हो सकता है। आख़िरी फ़ैसला lender का होगा।",
        "The indicative amount is 9.28 lakh at Bajaj Finance; the lender decides.",
        "आपकी June 2026 की salary slip बाकी है।",
    ):
        assert sanitize_speech(said, "hi") == said


def test_ids_and_markdown_are_never_spoken():
    out = sanitize_speech("**Your PAN** CKRPK7314M and Aadhaar 1234 5678 9012, card 4111 1111 1111 1111.", "en")
    assert "CKRPK7314M" not in out and "1234 5678 9012" not in out and "4111" not in out and "*" not in out
    assert sanitize_speech("- salary slip\n- bank statement", "en") == "salary slip\nbank statement"


async def test_filter_counts_rewrites_and_keeps_spacing():
    f = ComplianceTextFilter(lambda: "en")
    assert await f.filter("The lender decides. ") == "The lender decides. "
    assert f.rewrites == 0
    assert await f.filter("It is guaranteed. ") == "The final decision is the lender's; this is only indicative. "
    assert f.rewrites == 1


SECRET_LINES = {
    "हमें आपका OTP, PIN, password या पूरा PAN या Aadhaar नंबर कभी नहीं चाहिए।",
    "We never need your OTP, PIN, password, or full PAN or Aadhaar number.",
    "आम्हाला तुमचा OTP, PIN, password किंवा पूर्ण PAN किंवा Aadhaar नंबर कधीही लागत नाही.",
}


@pytest.mark.parametrize(
    "said,lang",
    [
        ("Please tell me the OTP you just received.", "en"),
        ("Can you share your UPI PIN?", "en"),
        ("What is your full PAN?", "en"),
        ("Please read out your Aadhaar number.", "en"),
        ("Please give me your card number and CVV.", "en"),
        ("Kripya apna OTP batao.", "hi"),
        ("Aapka account number bata dijiye.", "hi"),
        ("कृपया अपना OTP बताइए।", "hi"),
        ("अपना पूरा आधार नंबर बताइए।", "hi"),
        ("अपना पैन नंबर शेयर कीजिए।", "hi"),
        ("कृपया तुमचा OTP सांगा.", "mr"),
        ("तुमचा आधार क्रमांक द्या.", "mr"),
    ],
)
def test_requests_for_secrets_are_never_spoken(said, lang):
    out = sanitize_speech(said, lang)
    assert out in SECRET_LINES, out
    if lang == "mr" and any("ऀ" <= ch <= "ॿ" for ch in said):
        assert out.startswith("आम्हाला")  # Marathi call, Marathi line


@pytest.mark.parametrize(
    "said",
    [
        "We will never ask for your OTP or PIN.",
        "Please do not share your OTP with anyone.",
        "OTP की ज़रूरत नहीं है, कृपया किसी को मत बताइए।",
        "हमें आपका पूरा आधार नंबर नहीं चाहिए।",
        "तुमचा OTP कोणालाही सांगू नका.",
        "Please upload your PAN card and Aadhaar card copy.",
        "आपका PAN card और bank statement बाकी है, कृपया upload कीजिए।",
        "कृपया applicant की date of birth बताइए।",
        "Please tell me your pin code.",
        "Your PAN ending 314M matches the file.",
    ],
)
def test_warnings_and_document_requests_pass(said):
    assert sanitize_speech(said, "hi") == said


def test_replacement_lines_pass_the_filter_unchanged():
    for lang, line in zip(("hi", "en", "mr"), sorted(SECRET_LINES)):
        assert sanitize_speech(line, lang) == line
