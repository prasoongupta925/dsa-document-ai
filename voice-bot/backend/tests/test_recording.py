# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Recording: stereo WAV blocks -> IDP Telecaller QA upload (Transcribe on) -> local file deleted."""

import io
import os
import time
import wave
from datetime import datetime

import httpx
import pytest

import fixtures
import recording
from conftest import make_settings
from fakes import IDP_URL, FakeIdp
from idp_client import IdpClient
from prompt import IST
from recording import CallRecorder, recording_file_name, resolve_qa_project, sweep_stale_recordings
from tools import CallSession


@pytest.fixture(autouse=True)
def _clear_cache():
    recording._qa_project_cache.clear()
    yield
    recording._qa_project_cache.clear()


def stereo(seconds: float, left: int = 100, right: int = -100) -> bytes:
    frame = left.to_bytes(2, "little", signed=True) + right.to_bytes(2, "little", signed=True)
    return frame * int(16000 * seconds)


async def make_recorder(tmp_path, fake=None, **settings_overrides):
    fake = fake or FakeIdp()
    settings = make_settings(RECORDINGS_DIR=str(tmp_path / "rec"), **settings_overrides)
    session = CallSession(call_id="a1b2c3d4e5f6", channel="plivo", idp_user="voice-bot-phone", applicant="Sneha Anil Kulkarni")
    rec = CallRecorder(settings, IdpClient(IDP_URL, transport=fake.transport()), session)
    await rec.start()
    return rec, fake, settings


async def test_blocks_are_written_in_order_and_uploaded_then_deleted(tmp_path):
    rec, fake, settings = await make_recorder(tmp_path)
    path = rec.path
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    await rec._on_audio_data(rec.processor, stereo(1.0, 1, 2), 16000, 2)
    await rec._on_audio_data(rec.processor, stereo(0.5, 3, 4), 16000, 2)
    await rec._on_audio_data(rec.processor, b"\x01\x02\x03", 16000, 2)  # partial frame dropped
    await rec._on_audio_data(rec.processor, stereo(0.5), 8000, 2)  # wrong rate ignored
    assert rec.seconds == pytest.approx(1.5)
    document_id = await rec.finish_and_upload()
    assert document_id == "doc-1" and not os.path.exists(path) and os.listdir(settings.recordings_dir) == []

    post = next(c for c in fake.calls if c.method == "POST" and c.path.endswith("/documents"))
    assert post.path == f"/projects/{fixtures.QA_PROJECT_ID}/documents"
    assert post.body["use_transcribe"] is True and post.body["content_type"] == "audio/wav"
    assert post.body["transcribe_options"] == {"language_mode": "multi", "language_options": ["hi-IN", "en-IN", "mr-IN"]}
    assert post.body["file_name"].startswith("voicebot_plivo_") and post.body["file_name"].endswith("_a1b2c3.wav")
    assert "sneha" not in post.body["file_name"].lower()  # the session knows the applicant; the file name does not
    assert post.headers["x-user-id"] == "voice-bot-phone"
    wav = next(iter(fake.uploads.values()))
    assert post.body["file_size"] == len(wav)
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (2, 2, 16000, 24000)
        first = w.readframes(1)
    assert first == (1).to_bytes(2, "little", signed=True) + (2).to_bytes(2, "little", signed=True)  # caller left, bot right
    assert fake.calls[-1].body == {"status": "uploaded"}


async def test_too_short_calls_are_not_uploaded_but_deleted(tmp_path):
    rec, fake, settings = await make_recorder(tmp_path)
    await rec._on_audio_data(rec.processor, stereo(0.3), 16000, 2)
    path = rec.path
    assert await rec.finish_and_upload() is None
    assert not os.path.exists(path) and not [c for c in fake.calls if c.path.endswith("/documents")]


async def test_upload_failures_retry_then_give_up_and_still_delete(tmp_path):
    fake = FakeIdp(fail={r"/documents$": 503})
    rec, fake, _ = await make_recorder(tmp_path, fake)
    await rec._on_audio_data(rec.processor, stereo(2.0), 16000, 2)
    path = rec.path
    assert await rec.finish_and_upload(attempts=3) == "doc-2"  # the API client retried the 503 once
    assert not os.path.exists(path)

    def refuse(request):
        if request.url.path == "/projects":
            return httpx.Response(200, json=fixtures.PROJECTS)
        return httpx.Response(403, json={"detail": "denied"})

    settings = make_settings(RECORDINGS_DIR=str(tmp_path / "rec2"))
    session = CallSession(call_id="ffffffffffff", channel="browser")
    rec2 = CallRecorder(settings, IdpClient(IDP_URL, transport=httpx.MockTransport(refuse)), session)
    await rec2.start()
    await rec2._on_audio_data(rec2.processor, stereo(2.0), 16000, 2)
    path2 = rec2.path
    assert await rec2.finish_and_upload() is None
    assert not os.path.exists(path2)


async def test_qa_project_by_id_or_name(tmp_path):
    fake = FakeIdp()
    idp = IdpClient(IDP_URL, transport=fake.transport())
    assert await resolve_qa_project(idp, make_settings(QA_PROJECT_ID="proj_fixed"), "u") == "proj_fixed"
    assert fake.calls == []
    s = make_settings()
    assert await resolve_qa_project(idp, s, "u") == fixtures.QA_PROJECT_ID
    assert await resolve_qa_project(idp, s, "u") == fixtures.QA_PROJECT_ID
    assert fake.paths("GET").count("/projects") == 1  # cached
    assert await resolve_qa_project(idp, make_settings(QA_PROJECT_NAME="No such project"), "u") is None


def test_file_name_and_sweeper(tmp_path):
    session = CallSession(call_id="0123456789ab", channel="whatsapp")
    name = recording_file_name(session, datetime(2026, 10, 1, 9, 5, tzinfo=IST))
    assert name == "voicebot_whatsapp_2026-10-01_0905_012345.wav"
    named = CallSession(call_id="ABCDEF999999", channel="plivo", applicant="Sneha Anil Kulkarni", applicant_hint="Sneha")
    assert recording_file_name(named, datetime(2026, 10, 1, 17, 45, tzinfo=IST)) == "voicebot_plivo_2026-10-01_1745_abcdef.wav"
    folder = tmp_path / "rec"
    folder.mkdir()
    old, new = folder / "old.wav", folder / "new.wav"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    assert sweep_stale_recordings(str(folder)) == 1
    assert sorted(os.listdir(folder)) == ["new.wav"]
    assert sweep_stale_recordings(str(tmp_path / "missing")) == 0


async def test_a_failed_put_is_retried_on_the_same_document(tmp_path):
    # S3 says 503 once: the retry PUTs again to the same document (no "pending" twin in Telecaller QA).
    fake = FakeIdp(fail={r"\.wav$": 503})
    rec, fake, _ = await make_recorder(tmp_path, fake)
    await rec._on_audio_data(rec.processor, stereo(2.0), 16000, 2)
    path = rec.path
    assert await rec.finish_and_upload(attempts=3) == "doc-1"
    registrations = [c for c in fake.calls if c.method == "POST" and c.path.endswith("/documents")]
    puts = [c for c in fake.calls if c.method == "PUT" and c.path.startswith("https://idp-docs.s3")]
    assert len(registrations) == 1 and len(puts) == 2 and len(fake.uploads) == 1
    assert fake.calls[-1].path.endswith("/documents/doc-1/status") and fake.calls[-1].body == {"status": "uploaded"}
    assert not os.path.exists(path)


async def test_a_network_error_on_the_put_is_retried(tmp_path):
    fake = FakeIdp()
    state = {"dropped": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.host.startswith("idp-docs.s3") and not state["dropped"]:
            state["dropped"] += 1
            raise httpx.ConnectError("connection reset", request=request)
        return fake.handle(request)

    settings = make_settings(RECORDINGS_DIR=str(tmp_path / "rec"))
    session = CallSession(call_id="0f0f0f0f0f0f", channel="exotel", idp_user="voice-bot-phone")
    rec = CallRecorder(settings, IdpClient(IDP_URL, transport=httpx.MockTransport(flaky)), session)
    await rec.start()
    await rec._on_audio_data(rec.processor, stereo(1.5), 16000, 2)
    path = rec.path
    assert await rec.finish_and_upload(attempts=3) == "doc-1"
    assert state["dropped"] == 1 and len(fake.uploads) == 1
    assert len([c for c in fake.calls if c.method == "POST" and c.path.endswith("/documents")]) == 1
    assert not os.path.exists(path)


async def test_a_stale_cached_qa_project_is_looked_up_again(tmp_path):
    # The demo data was reloaded: the cached QA project id answers 404, the current one is found by name.
    recording._qa_project_cache["Telecaller QA – Sample calls"] = "proj_gone"
    fake = FakeIdp(fail={r"^/projects/proj_gone/documents$": 404})
    rec, fake, _ = await make_recorder(tmp_path, fake)
    await rec._on_audio_data(rec.processor, stereo(1.5), 16000, 2)
    path = rec.path
    assert await rec.finish_and_upload(attempts=3) == "doc-2"
    posts = [c.path for c in fake.calls if c.method == "POST" and c.path.endswith("/documents")]
    assert posts == ["/projects/proj_gone/documents", f"/projects/{fixtures.QA_PROJECT_ID}/documents"]
    assert recording._qa_project_cache["Telecaller QA – Sample calls"] == fixtures.QA_PROJECT_ID
    assert not os.path.exists(path)
    # The lookup does not use up an attempt: with a single attempt the found project still gets the upload.
    recording._qa_project_cache["Telecaller QA – Sample calls"] = "proj_gone"
    fake1 = FakeIdp(fail={r"^/projects/proj_gone/documents$": 404})
    rec1, fake1, _ = await make_recorder(tmp_path / "a", fake1)
    await rec1._on_audio_data(rec1.processor, stereo(1.5), 16000, 2)
    assert await rec1.finish_and_upload(attempts=1) == "doc-2"
    # A fixed QA_PROJECT_ID is never second-guessed: a 404 there just ends the upload.
    fake2 = FakeIdp(fail={r"/documents$": 404})
    rec2, fake2, _ = await make_recorder(tmp_path / "b", fake2, QA_PROJECT_ID="proj_fixed")
    await rec2._on_audio_data(rec2.processor, stereo(1.5), 16000, 2)
    assert await rec2.finish_and_upload(attempts=3) is None
    assert fake2.paths("GET") == []  # no project listing
