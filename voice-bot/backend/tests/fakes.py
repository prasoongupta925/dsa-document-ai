# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""In-process fakes: IDP API (+ S3 presigned PUT), Bedrock Converse stream, STT, TTS, WebSocket."""

from __future__ import annotations

import asyncio
import copy
import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
from botocore.exceptions import ClientError
from pipecat.frames.frames import TranscriptionFrame, TTSAudioRawFrame
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService
from pipecat.utils.time import time_now_iso8601
from starlette.websockets import WebSocketState

import fixtures

IDP_URL = "https://abc123defg.execute-api.ap-south-1.amazonaws.com"
S3_URL = "https://idp-docs.s3.ap-south-1.amazonaws.com"


# ------------------------------------------------------------------ IDP API
@dataclass
class IdpCall:
    method: str
    path: str
    body: dict | None
    headers: dict
    query: str = ""


@dataclass
class FakeIdp:
    """httpx MockTransport handler that behaves like the IDP backend API for the demo data."""

    calls: list[IdpCall] = field(default_factory=list)
    uploads: dict[str, bytes] = field(default_factory=dict)
    fail: dict[str, int] = field(default_factory=dict)  # path regex -> status (once)
    file_checks: dict[str, dict] = field(default_factory=lambda: {fixtures.SNEHA_PROJECT_ID: fixtures.SNEHA_FILE_CHECK})
    # Keys (case/space-insensitive) under which Sneha's CIBIL-page inputs are saved. The live demo saves
    # them under the PAN (extras/demo-seed/seed-demo.py, 1 Oct 2026), like the web app does.
    eligibility_keys: set = field(default_factory=lambda: {"ckrpk7314m"})
    draft_dob: str | None = None  # date of birth the IDP's document draft finds (None: not extracted)
    unsigned: int = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def paths(self, method: str | None = None) -> list[str]:
        return [c.path for c in self.calls if method is None or c.method == method]

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body = json.loads(request.content) if request.content and url.startswith(IDP_URL) else None
        path = request.url.path
        headers = {k.lower(): v for k, v in request.headers.items()}
        self.calls.append(IdpCall(request.method, path if url.startswith(IDP_URL) else url, body, headers, request.url.query.decode()))
        for pattern, status in list(self.fail.items()):
            if re.search(pattern, path):
                del self.fail[pattern]
                return httpx.Response(status, json={"detail": "injected failure"})
        if url.startswith(S3_URL):
            if request.method != "PUT" or headers.get("content-type") != "audio/wav" or int(headers.get("content-length", -1)) != len(request.content):
                return httpx.Response(403)
            self.uploads[path] = request.content
            return httpx.Response(200)
        auth = headers.get("authorization", "")
        if not (auth.startswith("AWS4-HMAC-SHA256 Credential=") and "/ap-south-1/execute-api/aws4_request" in auth and headers.get("x-amz-date") and headers.get("x-user-id")):
            self.unsigned += 1
            return httpx.Response(403, json={"message": "Missing Authentication Token"})
        if request.method == "GET" and path == "/projects":
            return httpx.Response(200, json=copy.deepcopy(fixtures.PROJECTS))
        m = re.fullmatch(r"/projects/([^/]+)/file-check", path)
        if m and request.method == "POST":
            result = self.file_checks.get(m.group(1))
            if result is None:
                return httpx.Response(200, json={**copy.deepcopy(fixtures.SNEHA_FILE_CHECK), "applicants": [], "overall_verdict": "NOT READY"})
            return httpx.Response(200, json=copy.deepcopy(result))
        m = re.fullmatch(r"/projects/([^/]+)/eligibility/calculate", path)
        if m and request.method == "POST":
            given = str((body or {}).get("applicant", ""))
            if " ".join(given.split()).casefold() in self.eligibility_keys:
                # Like the IDP (routers/eligibility.py _run): "applicant" echoes the key it was asked for.
                return httpx.Response(200, json={**copy.deepcopy(fixtures.SNEHA_ELIGIBILITY), "applicant": given})
            return httpx.Response(404, json={"detail": "No saved eligibility inputs for this applicant"})
        m = re.fullmatch(r"/projects/([^/]+)/eligibility/inputs", path)
        if m and request.method == "GET":
            given = request.url.params.get("applicant", "")
            if " ".join(given.split()).casefold() in self.eligibility_keys:
                profile = {"pan": "CKRPK7314M", "name": "Sneha Anil Kulkarni", "mobile": "9000000102", "dob": "1995-08-23"}
                return httpx.Response(200, json={"applicant": given, "saved": True, "inputs": {"profile": profile, "cibil": {}, "loan": {}},
                                                 "from_documents": [], "prefill": None})
            draft = {"name": "Sneha Anil Kulkarni", "pan": "XXXXXX314M", "dob": self.draft_dob}
            return httpx.Response(200, json={"applicant": given, "saved": False, "inputs": {"profile": draft, "cibil": {}, "loan": {}},
                                             "from_documents": ["name", "pan"] + (["dob"] if self.draft_dob else []),
                                             "prefill": {"available": True, "detail": "verified figures", "dob": self.draft_dob}})
        m = re.fullmatch(r"/projects/([^/]+)/documents", path)
        if m and request.method == "POST":
            n = len([c for c in self.calls if c.method == "POST" and c.path.endswith("/documents")])
            doc = f"doc-{n}"
            return httpx.Response(200, json={"document_id": doc, "upload_url": f"{S3_URL}/projects/{m.group(1)}/documents/{doc}/{doc}.wav?X-Amz-Signature=x", "file_name": body["file_name"], "expires_in": 300})
        m = re.fullmatch(r"/projects/([^/]+)/documents/([^/]+)/status", path)
        if m and request.method == "PUT":
            return httpx.Response(200, json={"document_id": m.group(2), "status": (body or {}).get("status")})
        return httpx.Response(404, json={"detail": "Not Found"})


# ------------------------------------------------------------------ Bedrock
def text_events(*chunks: str, stop: str = "end_turn") -> list[dict]:
    events = [{"messageStart": {"role": "assistant"}}, {"contentBlockStart": {"start": {}, "contentBlockIndex": 0}}]
    events += [{"contentBlockDelta": {"delta": {"text": c}, "contentBlockIndex": 0}} for c in chunks]
    events += [{"contentBlockStop": {"contentBlockIndex": 0}}, {"messageStop": {"stopReason": stop}},
               {"metadata": {"usage": {"inputTokens": 100, "outputTokens": 20}}}]
    return events


def tool_events(*calls: tuple[str, dict], text: str = "", stop: str = "tool_use", reasoning: str = "") -> list[dict]:
    events: list[dict] = [{"messageStart": {"role": "assistant"}}]
    index = 0
    if reasoning:
        events += [{"contentBlockDelta": {"delta": {"reasoningContent": {"text": reasoning}}, "contentBlockIndex": index}},
                   {"contentBlockStop": {"contentBlockIndex": index}}]
        index += 1
    if text:
        events += [{"contentBlockDelta": {"delta": {"text": text}, "contentBlockIndex": index}}, {"contentBlockStop": {"contentBlockIndex": index}}]
        index += 1
    for i, (name, args) in enumerate(calls):
        raw = json.dumps(args, ensure_ascii=False)
        half = len(raw) // 2
        events += [
            {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"tooluse_{name}_{i}", "name": name}}, "contentBlockIndex": index}},
            {"contentBlockDelta": {"delta": {"toolUse": {"input": raw[:half]}}, "contentBlockIndex": index}},
            {"contentBlockDelta": {"delta": {"toolUse": {"input": raw[half:]}}, "contentBlockIndex": index}},
            {"contentBlockStop": {"contentBlockIndex": index}},
        ]
        index += 1
    events += [{"messageStop": {"stopReason": stop}}, {"metadata": {"usage": {"inputTokens": 120, "outputTokens": 30}}}]
    return events


def client_error(code: str, message: str = "") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message or code}}, "ConverseStream")


class _Stream:
    def __init__(self, events, delay_s: float = 0.0, fail_after: int | None = None):
        self._events = list(events)
        self._delay = delay_s
        self._fail_after = fail_after

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for i, event in enumerate(self._events):
            if self._fail_after is not None and i == self._fail_after:
                raise client_error("ModelStreamErrorException")
            if self._delay:
                await asyncio.sleep(self._delay)
            yield event


class FakeBedrock:
    """Scripted Converse stream. ``script`` gets (request, model_id, turn) and returns events or an exception."""

    def __init__(self, script):
        self.script = script
        self.requests: list[dict] = []
        self.turns = 0

    @asynccontextmanager
    async def factory(self):
        yield self

    async def converse_stream(self, **request):
        self.requests.append(copy.deepcopy(request))
        outcome = self.script(request, request["modelId"], self.turns)
        if isinstance(outcome, BaseException):
            raise outcome
        self.turns += 1
        if isinstance(outcome, _Stream):
            return {"stream": outcome}
        return {"stream": _Stream(outcome)}


# ------------------------------------------------------------------ STT / TTS
class FakeSTT(STTService):
    """Emits the transcripts the test asks for; ignores audio."""

    def __init__(self, **kwargs):
        super().__init__(sample_rate=16000, settings=STTSettings(model=None, language="hi-IN"), ttfs_p99_latency=0.5, **kwargs)
        self.language_updates: list = []

    async def run_stt(self, audio: bytes):
        yield None

    async def say(self, text: str, language: str = "hi-IN"):
        await self.push_frame(TranscriptionFrame(text, "caller", time_now_iso8601(), language, finalized=True))

    async def _update_settings(self, delta):
        self.language_updates.append(getattr(delta, "language", None))
        return await super()._update_settings(delta)


class FakeTTS(TTSService):
    """100 ms of silence per sentence; remembers what it was asked to say (after the text filters)."""

    def __init__(self, **kwargs):
        super().__init__(
            sample_rate=16000, push_start_frame=True, push_stop_frames=True, stop_frame_timeout_s=0.3,
            settings=TTSSettings(model=None, voice="fake", language="hi-IN"), **kwargs,
        )
        self.spoken: list[str] = []

    def can_generate_metrics(self) -> bool:
        return False

    async def run_tts(self, text: str, context_id: str):
        self.spoken.append(text)
        yield TTSAudioRawFrame(b"\x01\x00" * 1600, 16000, 1, context_id=context_id)


# ------------------------------------------------------------------ WebSocket
class FakeWebSocket:
    """Enough of starlette's WebSocket for Pipecat's FastAPIWebsocketTransport."""

    def __init__(self):
        self.client_state = WebSocketState.CONNECTED
        self.application_state = WebSocketState.CONNECTED
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list = []
        self.closed_code: int | None = None

    async def receive(self):
        return await self.inbox.get()

    async def send_text(self, data: str):
        self.sent.append(data)

    async def send_bytes(self, data: bytes):
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str | None = None):
        self.closed_code = code
        self.client_state = WebSocketState.DISCONNECTED
        self.application_state = WebSocketState.DISCONNECTED
        await self.inbox.put({"type": "websocket.disconnect"})

    def push_text(self, text: str):
        self.inbox.put_nowait({"type": "websocket.receive", "text": text})

    def hang_up(self):
        self.client_state = WebSocketState.DISCONNECTED
        self.inbox.put_nowait({"type": "websocket.disconnect"})

    def events(self) -> list[dict]:
        out = []
        for item in self.sent:
            if isinstance(item, str):
                try:
                    out.append(json.loads(item))
                except json.JSONDecodeError:
                    pass
        return out
