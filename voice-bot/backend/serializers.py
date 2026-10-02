# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Wire formats.

Browser (/ws), JSON text messages:
  client -> bot  {"event": "media", "data": "<base64 PCM16 mono 16 kHz>"}   (binary PCM frames work too)
                 (close the WebSocket to hang up)
  bot -> client  {"event": "media", "data": "<base64 PCM16 mono 16 kHz>"}
                 {"event": "interruption"}                                 (stop playback now)
                 {"event": "user_transcript" | "bot_transcript", "text": ...}
                 {"event": "tool", "name": ..., "status": "started" | "done"}
                 {"event": "file_status" | "eligibility" | "reminder" | "language" | "call_ended" | "recording", ...}
Transcripts go only to the signed-in browser that is on the call; they are never logged.

Plivo: Pipecat's PlivoFrameSerializer (mu-law 8 kHz) with three changes: the
resampler presets are configurable per direction (stock VHQ buffers ~52 ms each
way plus ~100 ms at the start; the bot's voice uses QQ, 0 ms, which is clean
because Polly renders 8 kHz for Plivo; the caller's audio uses MQ, ~26 ms), the
hang-up API base is configurable, and the call is not hung up after it was
transferred to a telecaller.
"""

from __future__ import annotations

import base64
import json

from loguru import logger
from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import (
    AudioRawFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.serializers.plivo import PlivoFrameSerializer

UI_EVENTS = {
    "user_transcript",
    "bot_transcript",
    "tool",
    "file_status",
    "eligibility",
    "reminder",
    "language",
    "call_started",
    "call_ended",
    "recording",
}
MAX_AUDIO_B64 = 512 * 1024  # ~10 s of 16 kHz PCM per message is plenty


class BrowserJsonSerializer(FrameSerializer):
    def __init__(self, sample_rate: int = 16000):
        super().__init__(FrameSerializer.InputParams(ignore_rtvi_messages=True))
        self._sample_rate = sample_rate

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, AudioRawFrame):
            return json.dumps({"event": "media", "data": base64.b64encode(frame.audio).decode("ascii")})
        if isinstance(frame, InterruptionFrame):
            return json.dumps({"event": "interruption", "data": None})
        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            message = frame.message
            if not isinstance(message, dict) or self.should_ignore_frame(frame) or message.get("event") not in UI_EVENTS:
                return None
            return json.dumps(message, ensure_ascii=False)
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, (bytes, bytearray)):
            if not data or len(data) % 2:
                return None
            return InputAudioRawFrame(audio=bytes(data), num_channels=1, sample_rate=self._sample_rate)
        try:
            message = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(message, dict):
            return None
        event = message.get("event")
        if event == "media":
            payload = message.get("data")
            if not isinstance(payload, str) or len(payload) > MAX_AUDIO_B64:
                return None
            try:
                audio = base64.b64decode(payload, validate=True)
            except (ValueError, TypeError):
                return None
            if not audio or len(audio) % 2:
                return None
            return InputAudioRawFrame(audio=audio, num_channels=1, sample_rate=self._sample_rate)
        return None


class PlivoBotSerializer(PlivoFrameSerializer):
    """Plivo wire format with configurable resamplers/API base and no hang-up after a transfer."""

    def __init__(
        self,
        *args,
        resampler_quality: str = "",
        input_resampler_quality: str = "",
        api_base: str = "https://api.plivo.com",
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if resampler_quality:  # bot -> caller
            self._output_resampler = create_stream_resampler(quality=resampler_quality)
        if input_resampler_quality or resampler_quality:  # caller -> bot (defaults to the same preset)
            self._input_resampler = create_stream_resampler(quality=input_resampler_quality or resampler_quality)
        self._api_base = api_base.rstrip("/")
        self.transferred = False

    async def _hang_up_call(self):
        if self.transferred:
            logger.bind(event="plivo_hangup_skipped").info("call was transferred: not hanging up")
            return
        try:
            import aiohttp

            endpoint = f"{self._api_base}/v1/Account/{self._auth_id}/Call/{self._call_id}/"
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    endpoint, auth=aiohttp.BasicAuth(self._auth_id, self._auth_token), timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    status = response.status
            level = "info" if status in (204, 404) else "warning"
            getattr(logger.bind(event="plivo_hangup", status=status), level)("plivo hang-up")
        except Exception as e:  # noqa: BLE001
            logger.bind(event="plivo_hangup", error=type(e).__name__).warning("plivo hang-up failed")
