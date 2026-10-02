# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Transcribe locales (incl. mr-IN) and Polly Kajal requests, against fakes (no AWS)."""

from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

import pytest
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame
from pipecat.services.aws.utils import get_presigned_url

from speech import PollyKajalTTSService, create_stt, create_tts, polly_language_for, transcribe_language


@pytest.mark.parametrize("lang,code", [("hi", "hi-IN"), ("en", "en-IN"), ("mr", "mr-IN"), ("xx", "hi-IN")])
def test_transcribe_gets_the_exact_indian_locale(lang, code):
    stt = create_stt("ap-south-1", lang)
    # Stored as the service's language code (Pipecat would map plain English to en-US; mr-IN is not in its map).
    assert str(stt._settings.language) == code
    assert stt._credentials["region"] == "ap-south-1"
    assert str(transcribe_language(lang)) == code


def test_transcribe_url_is_mumbai_with_the_locale():
    stt = create_stt("ap-south-1", "mr")
    url = get_presigned_url(
        region=stt._credentials["region"],
        credentials={"access_key": "AKIAFAKE", "secret_key": "secret", "session_token": None},
        language_code=str(stt._settings.language),
        sample_rate=16000,
    )
    parsed = urlparse(url)
    assert parsed.hostname == "transcribestreaming.ap-south-1.amazonaws.com"
    assert parse_qs(parsed.query)["language-code"] == ["mr-IN"]


@pytest.mark.parametrize(
    "text,lang",
    [("आपकी salary slip बाकी है।", "hi-IN"), ("तुमची file तपासते.", "hi-IN"), ("Your salary slip is pending.", "en-IN"), ("", "en-IN")],
)
def test_polly_language_per_sentence(text, lang):
    assert polly_language_for(text) == lang


class FakeStream:
    def __init__(self, data):
        self._data = data

    async def read(self):
        return self._data


class FakePolly:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def synthesize_speech(self, **params):
        self.calls.append(params)
        if self.fail:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "InvalidSsmlException", "Message": "x"}}, "SynthesizeSpeech")
        return {"AudioStream": FakeStream(b"\x01\x00" * 3200)}


class FakeSession:
    def __init__(self, polly):
        self.polly = polly
        self.kwargs = None

    @asynccontextmanager
    async def client(self, name, **kwargs):
        assert name == "polly"
        self.kwargs = kwargs
        yield self.polly


async def _collect(tts, text):
    frames = []
    async for frame in tts.run_tts(text, "ctx-1"):
        frames.append(frame)
    return frames


async def test_polly_request_is_neural_kajal_escaped_with_language_code():
    tts = create_tts("ap-south-1")
    polly = FakePolly()
    tts._aws_session = FakeSession(polly)
    tts._sample_rate = 16000  # set by StartFrame in a pipeline
    frames = await _collect(tts, "Salary & bank <statement> बाकी है।")
    call = polly.calls[0]
    assert call["VoiceId"] == "Kajal" and call["Engine"] == "neural" and call["LanguageCode"] == "hi-IN"
    assert call["TextType"] == "ssml" and call["OutputFormat"] == "pcm" and call["SampleRate"] == "16000"
    assert "Salary &amp; bank &lt;statement&gt;" in call["Text"] and "<lang xml:lang='hi-IN'>" in call["Text"]
    assert tts._aws_session.kwargs["region_name"] == "ap-south-1"
    audio = b"".join(f.audio for f in frames if isinstance(f, TTSAudioRawFrame))
    assert len(audio) == 6400
    await _collect(tts, "Your file is ready.")
    assert polly.calls[1]["LanguageCode"] == "en-IN"


async def test_polly_errors_become_error_frames():
    tts = create_tts("ap-south-1")
    tts._aws_session = FakeSession(FakePolly(fail=True))
    tts._sample_rate = 16000
    frames = await _collect(tts, "hello")
    assert len(frames) == 1 and isinstance(frames[0], ErrorFrame)
    assert frames[0].error == "Polly error: ClientError"  # the type only, never the text


def test_tts_class():
    assert isinstance(create_tts("ap-south-1"), PollyKajalTTSService)


async def test_polly_renders_8khz_for_plivo_and_resamples_each_sentence_whole():
    from speech import polly_rate_for

    assert (polly_rate_for("plivo"), polly_rate_for("exotel"), polly_rate_for("browser"), polly_rate_for("whatsapp")) == (8000, 16000, 16000, 16000)
    tts = create_tts("ap-south-1", polly_sample_rate=8000)
    polly = FakePolly()  # returns 3,200 samples
    tts._aws_session = FakeSession(polly)
    tts._sample_rate = 16000
    first = b"".join(f.audio for f in await _collect(tts, "पहला वाक्य।") if isinstance(f, TTSAudioRawFrame))
    second = b"".join(f.audio for f in await _collect(tts, "दूसरा वाक्य।") if isinstance(f, TTSAudioRawFrame))
    assert polly.calls[0]["SampleRate"] == "8000"
    assert len(first) == len(second) == 12800  # exactly 2x per sentence: no tail held back into the next one
    with pytest.raises(ValueError):
        create_tts("ap-south-1", polly_sample_rate=22050)


async def test_the_plivo_line_path_is_clean_with_qq_only_when_polly_renders_8khz():
    """Why Plivo calls ask Polly for 8 kHz: the line's QQ resampler (0 ms) then has nothing above 4 kHz to alias."""
    import numpy as np
    from pipecat.audio.utils import create_file_resampler, create_stream_resampler

    def bandlimit(x, rate, hz):
        spectrum = np.fft.rfft(x)
        spectrum[np.fft.rfftfreq(len(x), 1 / rate) > hz] = 0
        return np.fft.irfft(spectrum, len(x))

    async def line(pcm16k: bytes) -> np.ndarray:  # what PlivoBotSerializer does to the bot's voice, 20 ms at a time
        qq, out = create_stream_resampler(quality="QQ"), bytearray()
        for i in range(0, len(pcm16k), 640):
            out += await qq.resample(pcm16k[i : i + 640], 16000, 8000)
        return np.frombuffer(bytes(out), dtype=np.int16).astype(float)

    def snr(ref, got):
        n = min(len(ref), len(got))
        return 10 * np.log10(np.sum(ref[:n] ** 2) / np.sum((ref[:n] - got[:n]) ** 2))

    rng = np.random.default_rng(7)
    voice8k = (bandlimit(rng.standard_normal(16000), 8000, 3400) * 6000).astype(np.int16)
    pipeline16k = await create_file_resampler().resample(voice8k.tobytes(), 8000, 16000)  # PollyKajalTTSService
    assert snr(voice8k.astype(float), await line(pipeline16k)) > 60  # measured 77.7 dB (VHQ: 79.0 dB)
    wide16k = (bandlimit(rng.standard_normal(32000), 16000, 7000) * 6000).astype(np.int16)
    ideal = np.frombuffer(await create_file_resampler().resample(wide16k.tobytes(), 16000, 8000), dtype=np.int16).astype(float)
    assert snr(ideal, await line(wide16k.tobytes())) < 10  # 16 kHz Polly through QQ: aliasing (measured 0.8 dB)


def test_padding_runs_are_not_spoken():
    from speech import sanitize_speech

    assert sanitize_speech("जी!!!!!!!! बताइए।") == "जी! बताइए।"
    assert sanitize_speech("Wait... ok!!") == "Wait. ok!!"
