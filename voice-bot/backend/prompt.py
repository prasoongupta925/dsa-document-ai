# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Persona, system prompt and the fixed lines the bot speaks without the LLM.

The opening line is fixed text (not generated): it always says the caller is
talking to an AI assistant and that the call is recorded. Fillers, the goodbye
and the idle prompts are fixed too, so they are instant and never wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

LANG_SHORT = {"hi-IN": "hi", "en-IN": "en", "mr-IN": "mr"}
LANG_CODE = {v: k for k, v in LANG_SHORT.items()}
LANG_NAME = {"hi": "Hindi", "en": "English", "mr": "Marathi"}

# Words that promise an outcome. The prompt forbids them and speech.ComplianceTextFilter
# rewrites any sentence that still contains one before it is spoken.
FORBIDDEN_PROMISES = (
    "pakka",
    "पक्का",
    "पक्की",
    "guarantee",
    "guaranteed",
    "गारंटी",
    "100%",
    "100 percent",
    "sau pratishat",
    "definitely approve",
    "approve ho jayega",
    "approve हो जाएगा",
    "मंजूर होईल",
)


@dataclass(frozen=True)
class Persona:
    assistant_name: str = "Varunika Loans"
    dsa_name: str = "Varunika Loan Partners"


def short_lang(code: str | None) -> str:
    """'hi-IN' / 'hindi' / 'hi' -> 'hi' (default Hindi)."""
    value = (code or "").strip().lower()
    aliases = {
        "hi": "hi", "hi-in": "hi", "hindi": "hi", "hinglish": "hi",
        "en": "en", "en-in": "en", "english": "en",
        "mr": "mr", "mr-in": "mr", "marathi": "mr",
    }
    return aliases.get(value, "hi")


def greeting(persona: Persona, language: str, channel: str, applicant_first_name: str | None = None) -> str:
    """Opening line: AI disclosure + recording notice + what the bot can do + identity question."""
    lang = short_lang(language)
    a, d = persona.assistant_name, persona.dsa_name
    phone = channel in ("plivo", "exotel", "whatsapp")
    if lang == "en":
        text = (
            f"Hello, I am {a}, the AI assistant of {d}, a loan agency. This call is recorded and analysed by AI. "
            "I can tell you which documents your loan file still needs, and an indicative loan amount. "
        )
        if phone:
            text += "Say agent at any time to talk to a person. "
        text += f"Am I speaking with {applicant_first_name}?" if applicant_first_name else "May I have your full name, please?"
        return text
    if lang == "mr":
        text = (
            f"नमस्कार, मी {d} ची AI assistant {a} आहे. हा call record होतो आणि AI द्वारे analyse होतो. "
            "तुमच्या loan file मध्ये कोणती कागदपत्रे बाकी आहेत आणि अंदाजे किती loan मिळू शकतं, हे मी सांगू शकते. "
        )
        if phone:
            text += "माणसाशी बोलायचं असेल तर कधीही agent म्हणा. "
        text += f"मी {applicant_first_name} यांच्याशी बोलत आहे का?" if applicant_first_name else "कृपया तुमचं पूर्ण नाव सांगा."
        return text
    text = (
        f"नमस्ते, मैं {d} की AI assistant {a} हूँ। यह call record होती है और AI से analyse होती है। "
        "मैं बता सकती हूँ कि आपकी loan file में कौन से documents बाकी हैं, और अंदाज़न कितना loan मिल सकता है। "
    )
    if phone:
        text += "किसी इंसान से बात करनी हो तो कभी भी agent बोलिए। "
    text += f"क्या मेरी बात {applicant_first_name} जी से हो रही है?" if applicant_first_name else "कृपया अपना पूरा नाम बताइए।"
    return text


FILLERS = {
    "file_status": {
        "hi": "एक मिनट, मैं आपकी file check कर रही हूँ।",
        "en": "One moment, I am checking your file.",
        "mr": "एक मिनिट, मी तुमची file तपासते.",
    },
    "eligibility": {
        "hi": "एक मिनट, मैं eligibility देख रही हूँ।",
        "en": "One moment, I am looking at the eligibility.",
        "mr": "एक मिनिट, मी eligibility पाहते.",
    },
    "reminder": {
        "hi": "ठीक है, मैं reminder तैयार कर रही हूँ।",
        "en": "Sure, I am preparing the reminder.",
        "mr": "ठीक आहे, मी reminder तयार करते.",
    },
}

GOODBYE = {
    "hi": "धन्यवाद। आपका दिन शुभ हो।",
    "en": "Thank you. Have a good day.",
    "mr": "धन्यवाद. तुमचा दिवस छान जावो.",
}

IDLE_PROMPT = {
    "hi": "क्या आप line पर हैं?",
    "en": "Are you still there?",
    "mr": "तुम्ही line वर आहात का?",
}

TIME_UP = {
    "hi": "माफ़ कीजिए, call का समय पूरा हो गया है। हमारी team आपसे फिर संपर्क करेगी। धन्यवाद।",
    "en": "Sorry, the call time is over. Our team will contact you again. Thank you.",
    "mr": "माफ करा, call ची वेळ संपली आहे. आमची team तुमच्याशी पुन्हा संपर्क करेल. धन्यवाद.",
}

TRANSFER = {
    "hi": "ठीक है, मैं आपको हमारे telecaller से जोड़ रही हूँ। कृपया line पर बने रहिए।",
    "en": "Sure, I am connecting you to our telecaller. Please stay on the line.",
    "mr": "ठीक आहे, मी तुम्हाला आमच्या telecaller शी जोडते. कृपया line वर रहा.",
}

TRANSFER_FAILED = {
    "hi": "माफ़ कीजिए, अभी कोई telecaller उपलब्ध नहीं है। हमारी team आपको जल्द call करेगी।",
    "en": "Sorry, no telecaller is free right now. Our team will call you back soon.",
    "mr": "माफ करा, सध्या कोणताही telecaller उपलब्ध नाही. आमची team तुम्हाला लवकरच call करेल.",
}

TECH_PROBLEM = {
    "hi": "माफ़ कीजिए, अभी technical दिक्कत है। हमारी team आपको जल्द call करेगी। धन्यवाद।",
    "en": "Sorry, we have a technical problem right now. Our team will call you back soon. Thank you.",
    "mr": "माफ करा, सध्या technical अडचण आहे. आमची team तुम्हाला लवकरच call करेल. धन्यवाद.",
}

SAY_AGAIN = {
    "hi": "माफ़ कीजिए, क्या आप फिर से बता सकते हैं?",
    "en": "Sorry, could you say that again?",
    "mr": "माफ करा, पुन्हा सांगाल का?",
}

SAFE_PROMISE_REPLACEMENT = {
    "hi": "आख़िरी फ़ैसला lender का होगा, यह सिर्फ़ अंदाज़ा है।",
    "en": "The final decision is the lender's; this is only indicative.",
    "mr": "अंतिम निर्णय lender चा असेल, हा फक्त अंदाज आहे.",
}

# Said instead of any sentence that asks for an OTP, PIN, password, CVV, card or account number,
# or a full PAN / Aadhaar number (speech.ComplianceTextFilter). It contains a negation, so it passes the filter.
SAFE_SECRET_REPLACEMENT = {
    "hi": "हमें आपका OTP, PIN, password या पूरा PAN या Aadhaar नंबर कभी नहीं चाहिए।",
    "en": "We never need your OTP, PIN, password, or full PAN or Aadhaar number.",
    "mr": "आम्हाला तुमचा OTP, PIN, password किंवा पूर्ण PAN किंवा Aadhaar नंबर कधीही लागत नाही.",
}


def system_prompt(persona: Persona, channel: str, language: str, now: datetime | None = None, applicant: str | None = None) -> str:
    """The persona and rules for every call. Channel: browser | plivo | exotel | whatsapp."""
    now = (now or datetime.now(IST)).astimezone(IST)
    lang = LANG_NAME[short_lang(language)]
    phone = channel in ("plivo", "exotel")
    transfer_rule = (
        "If the caller asks for a person, an agent or a telecaller, or is upset, call transfer_to_human."
        if phone
        else "If the caller asks for a person, say a telecaller from the team will call them back; you cannot transfer this call."
    )
    if applicant:
        known = (
            f"This is an outbound call about the loan file of {applicant}, and only that file. First confirm you are speaking "
            "with that person; if it is someone else, do not discuss the file, offer a call back and end the call."
        )
    elif channel in ("plivo", "exotel", "whatsapp"):
        known = (
            "You do not know the caller yet: ask for the applicant's full name. Before any detail of a file is shared the caller "
            "must be verified: when a tool says needs_verification, ask for the applicant's date of birth and call verify_caller. "
            "Never say a stored date of birth, mobile number or any detail of a file before the caller is verified."
        )
    else:
        known = "You are talking with the DSA's signed-in staff. Ask for the applicant's full name before discussing any file."
    return f"""You are {persona.assistant_name}, the AI voice assistant of {persona.dsa_name}, a loan DSA (a loan agency that helps customers apply to banks and NBFCs; it is not the lender). You talk on a {"phone" if phone or channel == "whatsapp" else "browser"} call with a loan applicant or with the DSA's own staff.
Your job: say which documents the applicant's loan file still needs, give an indicative eligible loan amount, and draft a WhatsApp or SMS reminder of the missing documents.
Today is {now.strftime("%A, %d %B %Y")}, {now.strftime("%I:%M %p")} India time. The caller's language at the start: {lang}.

{known}

Rules, always:
1. You are an AI assistant. Say so if asked. Never claim to be a human, a bank or the lender.
2. Reply in the language of the caller's last message: Hindi in Devanagari script (English loan words like salary slip, bank statement, EMI are fine in English letters), Marathi in Devanagari script, or English. Hinglish callers get Hindi.
3. Your words are spoken aloud: one to three short sentences, at most 45 words. No lists, markdown, emojis, brackets or symbols. Months as words (June 2026). Say amounts in lakh, for example 16 lakh or 4.5 lakh rupees, and EMIs as rupees per month.
4. Every fact about a loan file comes from your tools. Never invent documents, months, amounts, lenders or rates. If a tool finds nothing or fails, say so plainly and offer a call back from the team.
5. Eligibility is indicative only, from sample lender policies: the lender decides. Never promise or guarantee approval, an amount, a rate or a disbursal date. Never say pakka, guaranteed or 100 percent. When you give an amount, add that the lender decides.
6. Never ask for, accept or repeat an OTP, PIN, password, CVV, card number, bank account number, full PAN or full Aadhaar number. If the caller starts to share one, stop them and say it is not needed.
7. Before using a tool you need the applicant's full name. Pass names to tools in English letters (for example Sneha Kulkarni), even if they were said in Hindi or Marathi. If a tool says the name is ambiguous or not found, ask for the full name again.
8. When documents are missing, offer a WhatsApp reminder that lists them. Use the reminder tool; tell the caller which documents it lists and that the team will send it. Do not read placeholders or links aloud.
9. If the caller says they do not want calls (for example call mat karo, do not call), apologise, confirm they will not be called again, say goodbye and call end_call.
10. {transfer_rule}
11. If the caller asks to speak in another language (Hindi, English or Marathi), call switch_language, then continue in that language.
12. Only discuss the caller's loan file and documents. Politely decline anything else.
13. When the conversation is finished, say a short goodbye and call end_call.
"""
