# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""A whole browser call through the real Pipecat 1.3.0 pipeline, with fake STT/TTS/Bedrock/IDP.

Covers: fixed opening line, Kimi throttled -> gpt-oss fallback, tool calling (file status,
reminder, eligibility, end call), filler line, compliance rewrite of "pakka", browser UI events,
stereo recording uploaded to the QA project with Transcribe on, local file deleted.
"""

import asyncio
import base64
import io
import json
import os
import wave

import pytest
from pipecat.frames.frames import TTSAudioRawFrame
from pipecat.turns.user_start.transcription_user_turn_start_strategy import TranscriptionUserTurnStartStrategy
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

import fixtures
from bot import BotDeps, run_call, websocket_transport
from conftest import make_settings
from fakes import IDP_URL, FakeBedrock, FakeIdp, FakeSTT, FakeTTS, FakeWebSocket, client_error, text_events, tool_events
from idp_client import IdpClient
from llm import MumbaiBedrockLLMService
from serializers import BrowserJsonSerializer
from tools import CallSession

CHAIN = ["moonshotai.kimi-k2.5", "openai.gpt-oss-120b-1:0"]  # in-Region, ap-south-1


def last_user_text(request) -> str:
    for message in reversed(request["messages"]):
        if message["role"] == "user":
            return " ".join(b.get("text", "") for b in message["content"] if "text" in b)
    return ""


def has_tool_result(request, tool_prefix: str) -> bool:
    last = request["messages"][-1]
    return last["role"] == "user" and any(
        "toolResult" in b and b["toolResult"]["toolUseId"].startswith(f"tooluse_{tool_prefix}") for b in last["content"]
    )


def script(request, model_id, turn):
    if model_id == "moonshotai.kimi-k2.5":
        return client_error("ThrottlingException", "Too many requests")
    if has_tool_result(request, "file_status"):
        return text_events("आपकी June 2026 की salary slip, ", "March से May 2026 का bank statement और Form-16 बाकी है। ", "क्या मैं WhatsApp reminder भेजूँ?")
    if has_tool_result(request, "reminder"):
        return text_events("ठीक है, team आपको WhatsApp पर list भेजेगी।")
    if has_tool_result(request, "eligibility"):
        return text_events("Bajaj Finance से लगभग 9.28 lakh का loan अंदाज़न हो सकता है। ", "हाँ, loan pakka ho jayega! ")
    said = last_user_text(request)
    if "क्या बाकी" in said:
        return tool_events(("file_status", {"applicant": "Sneha Kulkarni"}), stop="end_turn", reasoning="The user wants the file status.")
    if "reminder" in said:
        return tool_events(("reminder", {"applicant": "Sneha Kulkarni", "language": "hi"}))
    if "pakka" in said:
        return tool_events(("eligibility", {"applicant": "Sneha Kulkarni"}), text="एक मिनट। ")
    if "bye" in said:
        return tool_events(("end_call", {"reason": "done"}), text="धन्यवाद, आपका दिन शुभ हो। ")
    return text_events("जी, बताइए।")


@pytest.fixture
def harness(tmp_path):
    settings = make_settings(RECORDINGS_DIR=str(tmp_path / "rec"), USER_IDLE_SECONDS=60, CALL_MAX_SECONDS=120)
    idp_fake = FakeIdp()
    idp = IdpClient(IDP_URL, transport=idp_fake.transport())
    bedrock = FakeBedrock(script)
    stt = FakeSTT()
    tts_holder = {}

    def tts_factory(filters):
        tts_holder["tts"] = FakeTTS(text_filters=filters)
        return tts_holder["tts"]

    deps = BotDeps(
        settings=settings,
        idp=idp,
        stt_factory=lambda lang: stt,
        tts_factory=tts_factory,
        llm_factory=lambda instructions: MumbaiBedrockLLMService(
            model_chain=CHAIN, region="ap-south-1", system_instruction=instructions, client_factory=bedrock.factory
        ),
        vad_factory=lambda: None,
        user_turn_strategies=UserTurnStrategies(
            start=[TranscriptionUserTurnStartStrategy()],
            stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.2)],
        ),
    )
    return settings, idp_fake, bedrock, stt, tts_holder, deps


async def _wait_for(predicate, timeout=10.0):
    end = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.05)


async def test_browser_call_end_to_end(harness, tmp_path):
    settings, idp_fake, bedrock, stt, tts_holder, deps = harness
    ws = FakeWebSocket()
    session = CallSession(call_id="abc123def456", channel="browser", language="hi", idp_user="asha.verma")

    async def caller():
        silence = base64.b64encode(b"\x00\x00" * 320).decode()  # 20 ms

        async def mic():
            while ws.client_state.name == "CONNECTED":
                ws.push_text(json.dumps({"event": "media", "data": silence}))
                await asyncio.sleep(0.02)

        mic_task = asyncio.create_task(mic())
        try:
            await _wait_for(lambda: "tts" in tts_holder and len(tts_holder["tts"].spoken) >= 1)
            await asyncio.sleep(0.6)  # greeting finished: the caller is unmuted
            turns = [
                ("मेरी file में क्या बाकी है? मेरा नाम Sneha Kulkarni है।", "salary slip"),
                ("हाँ, reminder भेज दो।", "WhatsApp पर list"),
                ("loan pakka ho jayega na?", "lender"),
            ]
            for said, expect in turns:
                await stt.say(said)
                await _wait_for(lambda: any(expect in s for s in tts_holder["tts"].spoken))
                await asyncio.sleep(0.5)
            await stt.say("ठीक है, धन्यवाद, bye")
            await _wait_for(lambda: ws.client_state.name != "CONNECTED", timeout=15)
        finally:
            mic_task.cancel()

    caller_task = asyncio.create_task(caller())
    summary = await asyncio.wait_for(run_call(websocket_transport(ws, BrowserJsonSerializer()), session, deps), timeout=60)
    await caller_task
    spoken = tts_holder["tts"].spoken
    full = " ".join(spoken)

    # Fixed opening line: AI assistant + recorded call, before anything else.
    assert "AI assistant" in spoken[0] and "Varunika Loans" in spoken[0]
    assert "record" in full.split("कृपया")[0]
    # Filler while the file check runs (the model said nothing before calling the tool).
    assert "मैं आपकी file check कर रही हूँ" in full
    # The model's text before the eligibility tool call is spoken, and no second filler is added.
    assert "एक मिनट। " in full or "एक मिनट।" in full
    # Compliance: the approval promise never reaches the caller.
    assert "pakka" not in full.lower()
    assert "lender का होगा" in full
    assert summary["compliance_rewrites"] >= 1
    # Goodbye and the call ended by the end_call tool.
    assert summary["ended"] == "done"
    assert session.tools_used[:3] == ["file_status", "reminder", "eligibility"] and "end_call" in session.tools_used

    # LLM: Kimi throttled -> gpt-oss served every turn; tool results go back as text blocks.
    models = [r["modelId"] for r in bedrock.requests]
    assert models[0] == "moonshotai.kimi-k2.5" and models[1] == "openai.gpt-oss-120b-1:0"
    assert "moonshotai.kimi-k2.5" not in models[2:]  # skipped while throttled
    assert summary["model"] == "openai.gpt-oss-120b-1:0"
    for request in bedrock.requests:
        assert request["messages"][0]["role"] == "user"
        assert request["messages"][-1]["role"] == "user"
        assert {t["toolSpec"]["name"] for t in request["toolConfig"]["tools"]} == {
            "file_status", "eligibility", "reminder", "switch_language", "end_call"
        }  # no transfer_to_human on a browser call
    after_file_check = next(r for r in bedrock.requests if has_tool_result(r, "file_status"))
    block = next(b for b in after_file_check["messages"][-1]["content"] if "toolResult" in b)["toolResult"]["content"][0]
    assert set(block) == {"text"}
    result = json.loads(block["text"])
    assert result["verdict"] == "NOT READY"
    assert result["missing_documents"] == [
        "June 2026 salary slip",
        "bank statement for March to May 2026",
        "Form-16 or ITR (FY 2025-26)",
    ]
    assert "CKRPK7314M" not in block["text"] and ".pdf" not in block["text"]
    after_elig = next(r for r in bedrock.requests if has_tool_result(r, "eligibility"))
    # Text spoken before the tool call is kept in front of it (Converse order), not after the result.
    assistant_turn = after_elig["messages"][-2]
    assert assistant_turn["role"] == "assistant" and "text" in assistant_turn["content"][0] and "toolUse" in assistant_turn["content"][-1]
    elig = json.loads(next(b for b in after_elig["messages"][-1]["content"] if "toolResult" in b)["toolResult"]["content"][0]["text"])
    assert elig["best"]["lender"] == "Bajaj Finance" and elig["best"]["amount"] == "9.28 lakh"
    assert elig["best"]["emi_per_month"] == "₹17,391" and elig["best"]["tenure"] == "7 years"

    # IDP: SigV4 on every call, the browser user as audit label, canonical names.
    assert idp_fake.unsigned == 0
    api_calls = [c for c in idp_fake.calls if not c.path.startswith("https://")]
    assert all(c.headers["x-user-id"] == "asha.verma" for c in api_calls)
    checks = [c for c in api_calls if c.path.endswith("/file-check")]
    assert checks[0].path == f"/projects/{fixtures.SNEHA_PROJECT_ID}/file-check" and checks[0].body == {"applicant": "Sneha Kulkarni"}
    calc = [c for c in api_calls if c.path.endswith("/eligibility/calculate")]
    assert calc[0].body == {"applicant": "CKRPK7314M"}  # saved under the PAN, like the web app
    assert elig["applicant"] == "Sneha Anil Kulkarni"  # never the PAN the IDP echoes back

    # Recording: stereo 16 kHz WAV uploaded to the QA project with Transcribe on, then deleted here.
    doc_post = next(c for c in api_calls if c.method == "POST" and c.path.endswith("/documents"))
    assert doc_post.path == f"/projects/{fixtures.QA_PROJECT_ID}/documents"
    assert doc_post.body["content_type"] == "audio/wav" and doc_post.body["use_transcribe"] is True
    assert doc_post.body["transcribe_options"] == {"language_mode": "multi", "language_options": ["hi-IN", "en-IN", "mr-IN"]}
    file_name = doc_post.body["file_name"]
    assert file_name.startswith("voicebot_browser_") and file_name.endswith("_abc123.wav")  # the web app shows _<ref>.wav
    assert "sneha" not in file_name.lower() and "kulkarni" not in file_name.lower()  # no customer name in IDP logs
    wav_bytes = next(iter(idp_fake.uploads.values()))
    with wave.open(io.BytesIO(wav_bytes)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, 2, 16000)
        frames = w.readframes(w.getnframes())
        assert w.getnframes() > 16000  # more than a second of call
    right = frames[2::4] + frames[3::4]
    assert any(b != 0 for b in right)  # the bot's channel has the (fake) TTS audio
    status = next(c for c in api_calls if c.path.endswith("/status"))
    assert status.method == "PUT" and status.body == {"status": "uploaded"}
    assert summary["recording_document_id"] == "doc-1"
    assert os.listdir(settings.recordings_dir) == []

    # Browser UI events (captions, tool progress, the reminder card).
    events = ws.events()
    kinds = [e.get("event") for e in events]
    assert kinds.count("media") > 10 and "call_started" in kinds
    assert any(e.get("event") == "user_transcript" and "Sneha" in e["text"] for e in events)
    assert any(e.get("event") == "bot_transcript" for e in events)
    assert {"event": "tool", "name": "file_status", "status": "started"} in events
    # Fixed lines are captioned once each, like the model's replies; the browser learns why the call ended.
    bot_lines = [e["text"] for e in events if e.get("event") == "bot_transcript"]
    assert bot_lines.count(spoken[0]) == 1 and "AI assistant" in bot_lines[0]
    assert any("file check कर रही हूँ" in line for line in bot_lines)
    assert {"event": "call_ended", "reason": "end_call"} in events
    # The rewrite reaches the captions and the model's own context too (TTSTextFrame carries the filtered text).
    assert not any("pakka" in line.lower() for line in bot_lines)
    assert any("lender का होगा" in line for line in bot_lines)
    history = [m for r in bedrock.requests for m in r["messages"] if m["role"] == "assistant"]
    assert history and not any("pakka" in json.dumps(m, ensure_ascii=False).lower() for m in history)
    reminder = next(e for e in events if e.get("event") == "reminder")
    assert reminder["template"] == "T1" and reminder["language"] == "hi"
    assert "जून 2026 की salary slip, मार्च से मई 2026 तक का bank statement, Form-16 या ITR (FY 2025-26)" in reminder["text"]
    assert reminder["placeholders"] == ["ref", "upload_link"]
    assert any(e.get("event") == "file_status" and e["verdict"] == "NOT READY" for e in events)


async def test_production_turn_taking_models_load_and_answer(tmp_path):
    """Default (production) turn-taking: Silero VAD + Pipecat's smart-turn v3 model, both local ONNX."""
    settings = make_settings(RECORDINGS_DIR=str(tmp_path / "rec"), RECORDING_ENABLED="false")
    bedrock = FakeBedrock(lambda request, model, turn: text_events("जी, मैं सुन रही हूँ।"))
    stt = FakeSTT()
    holder = {}

    def tts_factory(filters):
        holder["tts"] = FakeTTS(text_filters=filters)
        return holder["tts"]

    deps = BotDeps(
        settings=settings,
        idp=IdpClient(IDP_URL, transport=FakeIdp().transport()),
        stt_factory=lambda lang: stt,
        tts_factory=tts_factory,
        llm_factory=lambda instructions: MumbaiBedrockLLMService(
            model_chain=CHAIN[:1], region="ap-south-1", system_instruction=instructions, client_factory=bedrock.factory
        ),
    )
    ws = FakeWebSocket()
    session = CallSession(call_id="prod00000001", channel="browser")

    async def caller():
        silence = base64.b64encode(b"\x00\x00" * 320).decode()

        async def mic():
            while ws.client_state.name == "CONNECTED":
                ws.push_text(json.dumps({"event": "media", "data": silence}))
                await asyncio.sleep(0.02)

        task = asyncio.create_task(mic())
        try:
            await _wait_for(lambda: "tts" in holder and holder["tts"].spoken)
            await asyncio.sleep(0.6)
            await stt.say("नमस्ते")
            await _wait_for(lambda: any("सुन रही" in s for s in holder["tts"].spoken), timeout=15)
            ws.hang_up()
        finally:
            task.cancel()

    caller_task = asyncio.create_task(caller())
    summary = await asyncio.wait_for(run_call(websocket_transport(ws, BrowserJsonSerializer()), session, deps), timeout=40)
    await caller_task
    assert summary["ended"] == "caller_hung_up" and len(bedrock.requests) == 1


async def test_the_caller_is_recorded_while_muted(tmp_path):
    """The caller is muted during the opening line, yet Call QA must hear them: their audio is tapped at the input."""
    settings = make_settings(RECORDINGS_DIR=str(tmp_path / "rec"), USER_IDLE_SECONDS=60)
    idp_fake = FakeIdp()
    stt = FakeSTT()
    holder = {}

    class LongGreetingTTS(FakeTTS):
        async def run_tts(self, text, context_id):
            self.spoken.append(text)
            seconds = 1.5 if len(self.spoken) == 1 else 0.1
            yield TTSAudioRawFrame(b"\x01\x00" * int(16000 * seconds), 16000, 1, context_id=context_id)

    def tts_factory(filters):
        holder["tts"] = LongGreetingTTS(text_filters=filters)
        return holder["tts"]

    deps = BotDeps(
        settings=settings,
        idp=IdpClient(IDP_URL, transport=idp_fake.transport()),
        stt_factory=lambda lang: stt,
        tts_factory=tts_factory,
        llm_factory=lambda instructions: MumbaiBedrockLLMService(
            model_chain=CHAIN[:1], region="ap-south-1", system_instruction=instructions,
            client_factory=FakeBedrock(lambda r, m, t: text_events("जी।")).factory,
        ),
        vad_factory=lambda: None,
        user_turn_strategies=UserTurnStrategies(
            start=[TranscriptionUserTurnStartStrategy()], stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.2)]
        ),
    )
    ws = FakeWebSocket()
    session = CallSession(call_id="mute00000001", channel="browser")
    voice = base64.b64encode((1000).to_bytes(2, "little", signed=True) * 320).decode()  # 20 ms of a steady "voice"

    async def caller():
        async def mic():
            while ws.client_state.name == "CONNECTED":
                ws.push_text(json.dumps({"event": "media", "data": voice}))
                await asyncio.sleep(0.02)

        task = asyncio.create_task(mic())
        try:
            await _wait_for(lambda: "tts" in holder and holder["tts"].spoken)
            await asyncio.sleep(2.5)  # the 1.5 s opening line plays, then a second of open line
            ws.hang_up()
        finally:
            task.cancel()

    caller_task = asyncio.create_task(caller())
    summary = await asyncio.wait_for(run_call(websocket_transport(ws, BrowserJsonSerializer()), session, deps), timeout=30)
    await caller_task
    assert summary["recording_document_id"] == "doc-1"
    with wave.open(io.BytesIO(next(iter(idp_fake.uploads.values())))) as w:
        frames = w.readframes(w.getnframes())
    left = [int.from_bytes(frames[i : i + 2], "little", signed=True) for i in range(0, len(frames), 4)]
    right = [int.from_bytes(frames[i + 2 : i + 4], "little", signed=True) for i in range(0, len(frames), 4)]
    greeting = [i for i, v in enumerate(right) if v == 1]  # the bot's opening line (the caller is muted then)
    assert len(greeting) > 16000  # more than a second of it
    during = [left[i] for i in greeting]
    # Measured: ~3% of it survives without the tap, 85-95% with it (the rest is Pipecat's track-alignment padding).
    assert sum(1 for v in during if v == 1000) / len(during) > 0.6
