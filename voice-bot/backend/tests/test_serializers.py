# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import base64
import json

from pipecat.frames.frames import (
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageUrgentFrame,
    StartFrame,
)

import serializers
from serializers import BrowserJsonSerializer, PlivoBotSerializer


async def test_browser_audio_both_ways():
    s = BrowserJsonSerializer()
    pcm = b"\x01\x00\x02\x00" * 160
    frame = await s.deserialize(json.dumps({"event": "media", "data": base64.b64encode(pcm).decode()}))
    assert isinstance(frame, InputAudioRawFrame) and frame.audio == pcm and frame.sample_rate == 16000 and frame.num_channels == 1
    binary = await s.deserialize(pcm)
    assert isinstance(binary, InputAudioRawFrame) and binary.audio == pcm
    out = json.loads(await s.serialize(OutputAudioRawFrame(audio=pcm, sample_rate=16000, num_channels=1)))
    assert out == {"event": "media", "data": base64.b64encode(pcm).decode()}
    assert json.loads(await s.serialize(InterruptionFrame())) == {"event": "interruption", "data": None}


async def test_browser_rejects_junk():
    s = BrowserJsonSerializer()
    for data in ("not json", "[]", json.dumps({"event": "media", "data": "@@@"}), json.dumps({"event": "media", "data": "AA=="}),
                 json.dumps({"event": "media", "data": "A" * (serializers.MAX_AUDIO_B64 + 4)}), json.dumps({"event": "other"}), b"\x01"):
        assert await s.deserialize(data) is None


async def test_only_known_ui_events_reach_the_browser():
    s = BrowserJsonSerializer()
    ok = await s.serialize(OutputTransportMessageUrgentFrame(message={"event": "reminder", "text": "नमस्ते Sneha"}))
    assert json.loads(ok) == {"event": "reminder", "text": "नमस्ते Sneha"} and "नमस्ते" in ok  # not \\u-escaped
    assert await s.serialize(OutputTransportMessageUrgentFrame(message={"event": "debug_dump", "context": []})) is None
    assert await s.serialize(OutputTransportMessageUrgentFrame(message={"label": "rtvi-ai", "type": "bot-transcription"})) is None
    assert await s.serialize(OutputTransportMessageUrgentFrame(message="plain")) is None


async def test_plivo_serializer_options_and_no_hangup_after_transfer(monkeypatch):
    s = PlivoBotSerializer(stream_id="S1", call_id="C1", auth_id="MA1", auth_token="tok", resampler_quality="QQ",
                           input_resampler_quality="MQ", api_base="https://api.plivo.example")
    assert (s._output_resampler._quality, s._input_resampler._quality) == ("QQ", "MQ")
    same = PlivoBotSerializer(stream_id="S1", call_id="C1", auth_id="MA1", auth_token="tok", resampler_quality="LQ")
    assert (same._output_resampler._quality, same._input_resampler._quality) == ("LQ", "LQ")
    stock = PlivoBotSerializer(stream_id="S1", call_id="C1", auth_id="MA1", auth_token="tok")
    assert stock._output_resampler._quality == "VHQ"  # Pipecat's default when nothing is configured
    await s.setup(StartFrame(audio_in_sample_rate=16000, audio_out_sample_rate=16000))
    out = json.loads(await s.serialize(OutputAudioRawFrame(audio=b"\x00\x00" * 320, sample_rate=16000, num_channels=1)))
    assert out["event"] == "playAudio" and out["media"]["contentType"] == "audio/x-mulaw" and out["streamId"] == "S1"
    assert json.loads(await s.serialize(InterruptionFrame())) == {"event": "clearAudio", "streamId": "S1"}

    deleted = []

    class FakeResponse:
        status = 204

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def delete(self, url, **kwargs):
            deleted.append(url)
            return FakeResponse()

    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)
    await s._hang_up_call()
    assert deleted == ["https://api.plivo.example/v1/Account/MA1/Call/C1/"]
    s.transferred = True
    await s._hang_up_call()
    assert len(deleted) == 1
