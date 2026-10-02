# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Speech in and out, both in ap-south-1.

- Amazon Transcribe streaming: hi-IN (also handles Hinglish), en-IN and mr-IN.
  Pipecat 1.3.0's Transcribe map has no mr-IN and maps plain English to en-US,
  so the exact locale is passed as a Language value and goes to AWS unchanged.
- Amazon Polly neural voice Kajal (en-IN, bilingual hi-IN). Pipecat's Polly
  service puts the text into SSML unescaped (a "&" or "<" breaks the request)
  and only sets the language in a <lang> tag; this subclass escapes the text and
  sends LanguageCode per sentence: hi-IN for Devanagari (Hindi and Marathi
  replies; Polly has no Marathi voice), en-IN otherwise.
  On a Plivo call (8 kHz line) Polly renders 8 kHz audio, resampled once per
  sentence to the 16 kHz pipeline: the line's fast QQ resampler then never sees
  sound above 4 kHz, so it adds no delay and no aliasing.
- ComplianceTextFilter runs on every sentence before it is spoken: it rewrites a
  sentence that promises approval or asks for an OTP, PIN, password, CVV, card or
  account number or a full PAN/Aadhaar number (with fixed lines from prompt.py),
  masks PAN/Aadhaar/card numbers and strips markdown and padding runs ("!!!!").
  Pipecat 1.3.0 builds the caption and context text (TTSTextFrame) from the
  filtered text, so the raw wording reaches neither the caller nor the captions.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from xml.sax.saxutils import escape

from loguru import logger
from pipecat.audio.utils import create_file_resampler
from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.aws.stt import AWSTranscribeSTTService
from pipecat.services.aws.tts import AWSPollyTTSService
from pipecat.transcriptions.language import Language
from pipecat.utils.text.base_text_filter import BaseTextFilter

from prompt import FORBIDDEN_PROMISES, SAFE_PROMISE_REPLACEMENT, SAFE_SECRET_REPLACEMENT

SAMPLE_RATE = 16000
POLLY_RATES = (8000, 16000)  # the PCM rates Polly offers
TRANSCRIBE_CODES = {"hi": "hi-IN", "en": "en-IN", "mr": "mr-IN"}
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def transcribe_language(lang: str) -> Language:
    """Short language -> the Transcribe streaming locale as a Language value (passed to AWS as is)."""
    return Language(TRANSCRIBE_CODES.get(lang, "hi-IN"))


def create_stt(region: str, lang: str, sample_rate: int = SAMPLE_RATE) -> AWSTranscribeSTTService:
    return AWSTranscribeSTTService(
        region=region,
        sample_rate=sample_rate,
        settings=AWSTranscribeSTTService.Settings(language=transcribe_language(lang)),
    )


def polly_language_for(text: str) -> str:
    return "hi-IN" if _DEVANAGARI.search(text or "") else "en-IN"


class PollyKajalTTSService(AWSPollyTTSService):
    """Polly neural (Kajal) with escaped SSML, a per-sentence LanguageCode and a choice of Polly rate."""

    def __init__(self, *, polly_sample_rate: int = 16000, **kwargs):
        if polly_sample_rate not in POLLY_RATES:
            raise ValueError(f"Polly renders PCM at {POLLY_RATES} Hz only")
        super().__init__(**kwargs)
        self.polly_sample_rate = polly_sample_rate
        # Whole sentences arrive at once: a one-shot resampler keeps no tail between sentences.
        self._sentence_resampler = create_file_resampler()

    def _construct_ssml(self, text: str) -> str:  # noqa: D401 - pipecat hook
        lang = polly_language_for(text)
        ssml = f"<speak><lang xml:lang='{lang}'>"
        rate = self._settings.rate
        if rate:
            ssml += f"<prosody rate='{escape(str(rate))}'>{escape(text)}</prosody>"
        else:
            ssml += escape(text)
        return ssml + "</lang></speak>"

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        lang = polly_language_for(text)
        try:
            params = {
                "Text": self._construct_ssml(text),
                "TextType": "ssml",
                "OutputFormat": "pcm",
                "VoiceId": self._settings.voice,
                "Engine": self._settings.engine or "neural",
                "LanguageCode": lang,
                "SampleRate": str(self.polly_sample_rate),
            }
            async with self._aws_session.client("polly", **self._aws_params) as polly:
                response = await polly.synthesize_speech(**params)
                stream = response.get("AudioStream")
                if stream is None:
                    yield ErrorFrame(error="Polly returned no audio")
                    return
                audio = await stream.read()
            if self.polly_sample_rate != self.sample_rate:
                audio = await self._sentence_resampler.resample(audio, self.polly_sample_rate, self.sample_rate)
            await self.start_tts_usage_metrics(text)
            chunk = self.chunk_size
            for i in range(0, len(audio), chunk):
                part = audio[i : i + chunk]
                if part:
                    await self.stop_ttfb_metrics()
                    yield TTSAudioRawFrame(part, self.sample_rate, 1, context_id=context_id)
        except Exception as e:  # noqa: BLE001 - surfaced as a pipeline error, no text in the log
            logger.bind(event="tts_error", error=type(e).__name__).warning("Polly synthesis failed")
            yield ErrorFrame(error=f"Polly error: {type(e).__name__}")


def polly_rate_for(channel: str) -> int:
    """Polly's PCM rate for a channel: 8 kHz for the 8 kHz Plivo line (see the module docstring), else 16 kHz."""
    return 8000 if channel == "plivo" else 16000


def create_tts(
    region: str, voice: str = "Kajal", sample_rate: int = SAMPLE_RATE, text_filters=None, polly_sample_rate: int = 16000
) -> PollyKajalTTSService:
    return PollyKajalTTSService(
        region=region,
        sample_rate=sample_rate,
        polly_sample_rate=polly_sample_rate,
        settings=PollyKajalTTSService.Settings(voice=voice, engine="neural", language="hi-IN"),
        text_filters=text_filters or [],
    )


# ------------------------------------------------------------------ compliance filter
_PAN = re.compile(r"\b[A-Za-z]{5}\s?\d{4}\s?[A-Za-z]\b")
_AADHAAR = re.compile(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_MARKDOWN = re.compile(r"[*_#`>|~]+")
_BULLET = re.compile(r"(?m)^\s*(?:[-•]|\d+[.)])\s+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?।॥])\s+")
_PUNCT_RUN = re.compile(r"([!?.,;:।॥])\1{2,}")  # a run of 3+ of one mark ("!!!!" padding, "...") -> one mark


# A sentence that ASKS for a secret (term + request word, no negation). Document requests stay
# allowed ("PAN card", "Aadhaar card", "bank statement"), and so do warnings ("never share your OTP").
_SECRET_TERM = re.compile(
    r"\b(?:otp|o\.t\.p|cvv|cvc|password|passcode)\b"
    r"|\b(?:upi\s*|m-?|atm\s*)?pin\b(?!\s*code)"
    r"|\b(?:full|complete|entire|poora|pura|poori|puri)\s+(?:pan|aadhaa?r)\b"
    r"|\b(?:pan|aadhaa?r|aadhar|card|account|a/c)\s*(?:card\s*)?(?:number|no\b\.?|num)"
    r"|ओटीपी|ओ\.टी\.पी|सीवीवी|पासवर्ड|पिन(?!\s*कोड|कोड)"
    r"|(?:pan|aadhaa?r|पैन|आधार|card|कार्ड|account|खाता|खाते|अकाउंट)\s*(?:card\s*|कार्ड\s*)?(?:नंबर|नम्बर|क्रमांक)"
    r"|(?:पूरा|पूर्ण|पूरी)\s*(?:pan|aadhaa?r|पैन|आधार)",
    re.I,
)
_ASKS = re.compile(
    r"\b(?:tell|share|give|send|provide|say|read|enter|type|confirm|repeat|spell|what\s+is|what's|may\s+i\s+have|can\s+i\s+have)\b"
    r"|\b(?:batao|bataiye|bataye|bataen|bata\s*d[ie]\w*|boliye|bol\s*d\w*|dijiye|de\s*d\w*|bhej\w*|share\s*k\w*)\b"
    r"|बताइए|बताइये|बताएं|बताएँ|बताओ|बता\s*दीजिए|बता\s*दें|बोलिए|बोल\s*दीजिए|दीजिए|दे\s*दीजिए|भेजिए|भेज\s*दीजिए|शेयर|लिखिए"
    r"|सांगा|सांगाल|सांगू\s*शकता|द्या|पाठवा|शेअर|कळवा|लिहा",
    re.I,
)
_NEGATION = re.compile(
    r"\b(?:not|never|no\s+need|don't|dont|do\s+not|won't|will\s+not|without|nahi|nahin|mat|kabhi\s+nahi)\b"
    r"|नहीं|मत|कभी\s*न|नका|नाही|नये|कधीही\s*न",
    re.I,
)


def _asks_for_secret(sentence: str) -> bool:
    return bool(_SECRET_TERM.search(sentence) and _ASKS.search(sentence) and not _NEGATION.search(sentence))


def _has_promise(sentence: str) -> bool:
    low = sentence.lower()
    for word in FORBIDDEN_PROMISES:
        w = word.lower()
        if w.isascii() and w.replace(" ", "").isalpha():
            if re.search(rf"\b{re.escape(w)}\b", low):
                return True
        elif w in low:
            return True
    return False


def sanitize_speech(text: str, lang: str = "hi") -> str:
    """The text the bot may say: no approval promise, no ID numbers, no markdown."""
    text = _BULLET.sub("", text)
    text = _MARKDOWN.sub("", text)
    text = _PUNCT_RUN.sub(r"\1", text)
    text = _PAN.sub("PAN", text)
    text = _CARD.sub(" ", text)  # 13-19 digits: card or account number
    text = _AADHAAR.sub(" ", text)  # 12 digits
    out = []
    for sentence in _SENTENCE_SPLIT.split(text):
        if not sentence.strip():
            continue
        sentence_lang = ("mr" if lang == "mr" else "hi") if _DEVANAGARI.search(sentence) else ("en" if lang == "en" else lang)
        if _has_promise(sentence):
            out.append(SAFE_PROMISE_REPLACEMENT.get(sentence_lang, SAFE_PROMISE_REPLACEMENT["hi"]))
        elif _asks_for_secret(sentence):
            out.append(SAFE_SECRET_REPLACEMENT.get(sentence_lang, SAFE_SECRET_REPLACEMENT["hi"]))
        else:
            out.append(sentence)
    return re.sub(r"[ \t]{2,}", " ", " ".join(out)).strip()


class ComplianceTextFilter(BaseTextFilter):
    """Pipecat TTS text filter around ``sanitize_speech``."""

    def __init__(self, language_getter=lambda: "hi"):
        self._language_getter = language_getter
        self.rewrites = 0

    async def filter(self, text: str) -> str:
        cleaned = sanitize_speech(text, self._language_getter())
        if cleaned != text.strip():
            self.rewrites += 1
        # Keep the trailing space TTS aggregation relies on.
        return cleaned + (" " if text.endswith(" ") else "")
