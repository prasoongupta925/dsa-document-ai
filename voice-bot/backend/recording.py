# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Call recording -> IDP "Telecaller QA – Sample calls" -> deleted from this server.

Pipecat's AudioBufferProcessor (after the output transport) keeps both sides
in sync; stereo 16 kHz 16-bit: caller on the left channel, bot on the right.
The caller's audio is taken right after the input transport (CallerAudioTap):
further down, Pipecat drops it while the caller is muted (opening line, tool
calls), and Call QA must hear everything the caller said.
Audio is appended to a temporary WAV file in 5-second blocks (no whole call in
memory). After the call the file is uploaded with the same three calls the web
app makes (register document -> presigned PUT -> status "uploaded") with
Amazon Transcribe on (hi-IN, en-IN, mr-IN auto-detected), so the IDP's Call
QA scores it. The local file is deleted right after, whatever the outcome; a
sweeper removes anything a crash left behind. The IDP keeps it 7 days.
The file name carries no customer name: channel, India time and the call ref.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import time
import wave
from datetime import datetime

from loguru import logger
from pipecat.frames.frames import Frame, InputAudioRawFrame
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from config import Settings
from idp_client import IdpClient, IdpError
from prompt import IST
from tools import CallSession

SAMPLE_RATE = 16000
CHANNELS = 2
BLOCK_SECONDS = 5
MIN_SECONDS = 1.0
TRANSCRIBE_OPTIONS = {"language_mode": "multi", "language_options": ["hi-IN", "en-IN", "mr-IN"]}
STALE_AFTER_S = 3600

_qa_project_cache: dict[str, str] = {}


def recording_file_name(session: CallSession, when: datetime | None = None) -> str:
    """'voicebot_plivo_2026-10-01_1745_a1b2c3.wav': no customer name (file names reach IDP logs)."""
    when = (when or datetime.now(IST)).astimezone(IST)
    channel = re.sub(r"[^a-z0-9]+", "", session.channel.lower())[:12] or "call"
    ref = re.sub(r"[^a-z0-9]+", "", session.call_id.lower())[:6] or "000000"
    return f"voicebot_{channel}_{when:%Y-%m-%d_%H%M}_{ref}.wav"


class CallAudioRecorder(AudioBufferProcessor):
    """AudioBufferProcessor whose caller track is fed by CallerAudioTap, not by the frames reaching it."""

    async def _process_recording(self, frame: Frame):
        if isinstance(frame, InputAudioRawFrame):
            return  # the tap at the input already recorded it (and these miss the muted parts)
        await super()._process_recording(frame)

    async def record_caller_audio(self, frame: InputAudioRawFrame) -> None:
        if self._recording and self._sample_rate:
            await AudioBufferProcessor._process_recording(self, frame)


class CallerAudioTap(FrameProcessor):
    """Placed right after the input transport: hands every caller audio frame to the recorder."""

    def __init__(self, recorder: CallAudioRecorder, **kwargs):
        super().__init__(**kwargs)
        self._recorder = recorder

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, InputAudioRawFrame):
            await self._recorder.record_caller_audio(frame)
        await self.push_frame(frame, direction)


def sweep_stale_recordings(directory: str, max_age_s: int = STALE_AFTER_S) -> int:
    """Delete recordings older than max_age_s (left behind by a crash). Returns how many."""
    removed = 0
    if not os.path.isdir(directory):
        return 0
    now = time.time()
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        try:
            if name.endswith(".wav") and now - os.path.getmtime(path) > max_age_s:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.bind(event="recording_sweep", removed=removed).info("removed stale recordings")
    return removed


async def resolve_qa_project(idp: IdpClient, settings: Settings, user: str) -> str | None:
    if settings.qa_project_id:
        return settings.qa_project_id
    if settings.qa_project_name in _qa_project_cache:
        return _qa_project_cache[settings.qa_project_name]
    for project in await idp.list_projects(user=user):
        if project.get("name") == settings.qa_project_name:
            _qa_project_cache[settings.qa_project_name] = project["project_id"]
            return project["project_id"]
    return None


class CallRecorder:
    def __init__(self, settings: Settings, idp: IdpClient, session: CallSession):
        self.settings = settings
        self.idp = idp
        self.session = session
        self.processor = CallAudioRecorder(
            sample_rate=SAMPLE_RATE,
            num_channels=CHANNELS,
            buffer_size=SAMPLE_RATE * 2 * BLOCK_SECONDS,
        )
        self.processor.add_event_handler("on_audio_data", self._on_audio_data)
        self.tap = CallerAudioTap(self.processor)  # goes right after transport.input()
        self.path: str | None = None
        self._wav: wave.Wave_write | None = None
        self._frames = 0
        self.document_id: str | None = None

    @property
    def seconds(self) -> float:
        return self._frames / SAMPLE_RATE

    async def start(self) -> None:
        os.makedirs(self.settings.recordings_dir, mode=0o700, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix=f"call-{self.session.call_id}-", suffix=".wav", dir=self.settings.recordings_dir)
        os.close(fd)
        os.chmod(path, 0o600)
        wav = wave.open(path, "wb")
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        self.path, self._wav = path, wav
        await self.processor.start_recording()

    async def _on_audio_data(self, _processor, audio: bytes, sample_rate: int, num_channels: int) -> None:
        # No await in here: blocks are written in the order the processor produced them.
        if self._wav is None or not audio or num_channels != CHANNELS or sample_rate != SAMPLE_RATE:
            return
        usable = len(audio) - (len(audio) % (2 * CHANNELS))
        self._wav.writeframes(audio[:usable])
        self._frames += usable // (2 * CHANNELS)

    def _close(self) -> None:
        if self._wav is not None:
            try:
                self._wav.close()
            finally:
                self._wav = None

    def _delete(self) -> None:
        if self.path and os.path.exists(self.path):
            try:
                os.remove(self.path)
            except OSError:
                logger.bind(event="recording_delete_failed").warning("could not delete a local recording")
        self.path = None

    async def finish_and_upload(self, attempts: int = 3) -> str | None:
        """Close the WAV, upload it to the QA project, delete it locally. Returns the IDP document id."""
        try:
            await self.processor.stop_recording()  # flushes the last block (no-op if already stopped)
        except Exception:  # noqa: BLE001
            pass
        # The block handlers run as event tasks: wait for the last ones before closing the file.
        pending = [task for _, task in list(getattr(self.processor, "_event_tasks", ())) if not task.done()]
        if pending:
            await asyncio.wait(pending, timeout=5)
        self._close()
        log = logger.bind(event="recording", call_id=self.session.call_id, seconds=round(self.seconds, 1))
        try:
            if not self.path or self.seconds < MIN_SECONDS:
                log.info("recording too short: not uploaded")
                return None
            project_id = await resolve_qa_project(self.idp, self.settings, self.session.idp_user)
            if not project_id:
                log.warning("QA project not found: recording not uploaded")
                return None
            size = os.path.getsize(self.path)
            name = recording_file_name(self.session)
            # One IDP document per call: a retry continues with the step that failed (a new
            # registration per attempt would leave "pending" twins in Telecaller QA).
            ticket = None
            put_done = False
            looked_again = False
            attempt = 0
            while attempt < attempts:
                attempt += 1
                try:
                    if ticket is None:
                        ticket = await self.idp.create_document(
                            project_id, name, "audio/wav", size, use_transcribe=True,
                            transcribe_options=TRANSCRIBE_OPTIONS, user=self.session.idp_user,
                        )
                    if not put_done:
                        await self.idp.put_file(ticket.upload_url, self.path, "audio/wav")
                        put_done = True
                    await self.idp.mark_uploaded(project_id, ticket.document_id, user=self.session.idp_user)
                    self.document_id = ticket.document_id
                    log.bind(document_id=ticket.document_id, bytes=size).info("recording uploaded to Call QA")
                    return ticket.document_id
                except IdpError as e:
                    if e.status == 404 and ticket is None and not looked_again and not self.settings.qa_project_id:
                        # The project id cached by name is gone (demo data reloaded): look it up once more.
                        looked_again = True
                        _qa_project_cache.pop(self.settings.qa_project_name, None)
                        project_id = await resolve_qa_project(self.idp, self.settings, self.session.idp_user)
                        if project_id:
                            attempt -= 1  # the lookup is not an upload attempt (it happens at most once)
                            continue
                        log.warning("QA project not found: recording not uploaded")
                        return None
                    if attempt == attempts or e.status in (400, 401, 403, 404, 422):
                        log.bind(status=e.status).warning("recording upload failed")
                        return None
                    await asyncio.sleep(1.5 * attempt)
            return None
        finally:
            self._delete()
