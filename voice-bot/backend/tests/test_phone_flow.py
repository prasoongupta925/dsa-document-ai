# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Phone calls through the real pipeline with Plivo's wire format: transfer to a human, the
10-minute cap (shortened), and silence handling."""

import asyncio
import base64
import json

import pytest
from pipecat.turns.user_start.transcription_user_turn_start_strategy import TranscriptionUserTurnStartStrategy
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from bot import BotDeps, run_call, websocket_transport
from conftest import make_settings
from fakes import IDP_URL, FakeBedrock, FakeIdp, FakeSTT, FakeTTS, FakeWebSocket, text_events, tool_events
from idp_client import IdpClient
from llm import MumbaiBedrockLLMService
from prompt import GOODBYE, IDLE_PROMPT, TIME_UP, TRANSFER
from serializers import PlivoBotSerializer
from tools import CallSession


def build(tmp_path, script, **settings_overrides):
    settings = make_settings(RECORDINGS_DIR=str(tmp_path / "rec"), RECORDING_ENABLED="false", **settings_overrides)
    stt = FakeSTT()
    holder = {}
    bedrock = FakeBedrock(script)

    def tts_factory(filters):
        holder["tts"] = FakeTTS(text_filters=filters)
        return holder["tts"]

    deps = BotDeps(
        settings=settings,
        idp=IdpClient(IDP_URL, transport=FakeIdp().transport()),
        stt_factory=lambda lang: stt,
        tts_factory=tts_factory,
        llm_factory=lambda instructions: MumbaiBedrockLLMService(
            model_chain=["moonshotai.kimi-k2.5"], region="ap-south-1", system_instruction=instructions, client_factory=bedrock.factory
        ),
        vad_factory=lambda: None,
        user_turn_strategies=UserTurnStrategies(
            start=[TranscriptionUserTurnStartStrategy()], stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.2)]
        ),
    )
    return deps, stt, holder, bedrock


def plivo_serializer(monkeypatch, hangups):
    async def fake_hang_up(self):
        hangups.append(self.transferred)

    monkeypatch.setattr(PlivoBotSerializer, "_hang_up_call", fake_hang_up)
    return PlivoBotSerializer(stream_id="STREAM-1", call_id="CALL-1", auth_id="MA1", auth_token="tok")


async def feed_mulaw(ws):
    silence = {"event": "media", "streamId": "STREAM-1", "media": {"payload": base64.b64encode(b"\xff" * 160).decode()}}
    while ws.client_state.name == "CONNECTED":
        ws.push_text(json.dumps(silence))
        await asyncio.sleep(0.02)


async def wait_for(predicate, timeout=10.0):
    end = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.05)


async def test_transfer_to_a_telecaller(tmp_path, monkeypatch):
    def script(request, model, turn):
        last = request["messages"][-1]["content"]
        if any("agent" in b.get("text", "") for b in last):
            return tool_events(("transfer_to_human", {"reason": "asked for a person"}))
        return text_events("जी, बताइए।")

    deps, stt, holder, bedrock = build(tmp_path, script)
    hangups = []
    serializer = plivo_serializer(monkeypatch, hangups)
    ws = FakeWebSocket()
    session = CallSession(call_id="phone0000001", channel="plivo", idp_user="voice-bot-phone", provider_call_id="CALL-1")
    transfers = []

    async def on_transfer(s):
        transfers.append(s.call_id)
        serializer.transferred = True
        return {"transferred": True, "end_stream": True}

    async def caller():
        feeder = asyncio.create_task(feed_mulaw(ws))
        try:
            await wait_for(lambda: "tts" in holder and holder["tts"].spoken)
            await asyncio.sleep(0.5)
            await stt.say("मुझे agent से बात करनी है")
            await wait_for(lambda: ws.client_state.name != "CONNECTED")
        finally:
            feeder.cancel()

    task = asyncio.create_task(caller())
    summary = await asyncio.wait_for(run_call(websocket_transport(ws, serializer), session, deps, on_transfer=on_transfer), timeout=30)
    await task
    spoken = holder["tts"].spoken
    assert "agent" in spoken[0]  # phone greeting offers a person
    assert TRANSFER["hi"] in spoken
    assert transfers == ["phone0000001"] and summary["ended"] == "transferred" and session.transfer_requested
    assert hangups == [True]  # the serializer was told the call is transferred: it must not hang up
    tools = {t["toolSpec"]["name"] for t in bedrock.requests[0]["toolConfig"]["tools"]}
    assert "transfer_to_human" in tools
    sent = [json.loads(m) for m in ws.sent if isinstance(m, str)]
    plays = [m for m in sent if m.get("event") == "playAudio"]
    assert plays and plays[0]["media"]["contentType"] == "audio/x-mulaw" and plays[0]["media"]["sampleRate"] == 8000
    assert not any(m.get("event") in ("user_transcript", "bot_transcript", "tool") for m in sent)  # no UI events on a phone line


async def test_call_time_cap(tmp_path, monkeypatch):
    deps, stt, holder, _ = build(tmp_path, lambda r, m, t: text_events("जी।"), CALL_MAX_SECONDS=2)
    hangups = []
    ws = FakeWebSocket()
    feeder = asyncio.create_task(feed_mulaw(ws))
    session = CallSession(call_id="phone0000002", channel="plivo")
    summary = await asyncio.wait_for(run_call(websocket_transport(ws, plivo_serializer(monkeypatch, hangups)), session, deps), timeout=20)
    feeder.cancel()
    assert summary["ended"] == "time_cap" and TIME_UP["hi"] in holder["tts"].spoken
    assert hangups == [False]  # Plivo's call is hung up at the end


async def test_silence_two_prompts_then_goodbye(tmp_path, monkeypatch):
    deps, stt, holder, _ = build(tmp_path, lambda r, m, t: text_events("जी।"), USER_IDLE_SECONDS=0.4)
    ws = FakeWebSocket()
    feeder = asyncio.create_task(feed_mulaw(ws))
    session = CallSession(call_id="phone0000003", channel="plivo", language="en")
    summary = await asyncio.wait_for(run_call(websocket_transport(ws, plivo_serializer(monkeypatch, [])), session, deps), timeout=20)
    feeder.cancel()
    spoken = holder["tts"].spoken
    assert spoken.count(IDLE_PROMPT["en"]) == 2 and spoken[-1] == GOODBYE["en"]
    assert summary["ended"] == "silence"


async def test_caller_hang_up_ends_the_call(tmp_path, monkeypatch):
    deps, stt, holder, _ = build(tmp_path, lambda r, m, t: text_events("जी।"))
    ws = FakeWebSocket()
    session = CallSession(call_id="phone0000004", channel="plivo")

    async def hang_up_soon():
        await wait_for(lambda: "tts" in holder and holder["tts"].spoken)
        ws.hang_up()

    task = asyncio.create_task(hang_up_soon())
    summary = await asyncio.wait_for(run_call(websocket_transport(ws, plivo_serializer(monkeypatch, [])), session, deps), timeout=20)
    await task
    assert summary["ended"] == "caller_hung_up"


@pytest.mark.parametrize("language,code", [("mr", "mr-IN"), ("en", "en-IN")])
async def test_switch_language_reconfigures_transcribe(tmp_path, monkeypatch, language, code):
    def script(request, model, turn):
        last = request["messages"][-1]["content"]
        if any("language" in b.get("text", "") for b in last):
            return tool_events(("switch_language", {"language": language}))
        if any("toolResult" in b for b in last):
            return tool_events(("end_call", {"reason": "done"}), text="ठीक है। ")
        return text_events("जी।")

    deps, stt, holder, _ = build(tmp_path, script)
    ws = FakeWebSocket()
    feeder = asyncio.create_task(feed_mulaw(ws))
    session = CallSession(call_id="phone0000005", channel="plivo")

    async def caller():
        await wait_for(lambda: "tts" in holder and holder["tts"].spoken)
        await asyncio.sleep(0.5)
        await stt.say("please change the language")

    task = asyncio.create_task(caller())
    summary = await asyncio.wait_for(run_call(websocket_transport(ws, plivo_serializer(monkeypatch, [])), session, deps), timeout=20)
    await task
    feeder.cancel()
    assert session.language == language and summary["ended"] == "done"
    assert [str(x) for x in stt.language_updates] == [code]


async def test_bedrock_down_apologises_then_ends(tmp_path, monkeypatch):
    from fakes import client_error
    from prompt import SAY_AGAIN, TECH_PROBLEM

    deps, stt, holder, _ = build(tmp_path, lambda r, m, t: client_error("ThrottlingException"))
    ws = FakeWebSocket()
    feeder = asyncio.create_task(feed_mulaw(ws))
    session = CallSession(call_id="phone0000006", channel="plivo")

    async def caller():
        await wait_for(lambda: "tts" in holder and holder["tts"].spoken)
        await asyncio.sleep(0.5)
        await stt.say("hello?")
        await wait_for(lambda: SAY_AGAIN["hi"] in holder["tts"].spoken)
        await asyncio.sleep(0.6)
        await stt.say("hello, kya aap sun rahe ho?")

    task = asyncio.create_task(caller())
    summary = await asyncio.wait_for(run_call(websocket_transport(ws, plivo_serializer(monkeypatch, [])), session, deps), timeout=20)
    await task
    feeder.cancel()
    assert TECH_PROBLEM["hi"] in holder["tts"].spoken and summary["ended"] == "llm_error"


async def test_a_crashing_tool_still_answers_and_unmutes(tmp_path, monkeypatch):
    import tools as tools_module

    async def boom(self, applicant):
        raise RuntimeError("bug")

    monkeypatch.setattr(tools_module.LoanTools, "file_status", boom)

    def script(request, model, turn):
        last = request["messages"][-1]["content"]
        if any("toolResult" in b for b in last):
            return tool_events(("end_call", {"reason": "done"}), text="माफ़ कीजिए, team call करेगी। ")
        return tool_events(("file_status", {"applicant": "Sneha Kulkarni"}))

    deps, stt, holder, bedrock = build(tmp_path, script)
    ws = FakeWebSocket()
    feeder = asyncio.create_task(feed_mulaw(ws))
    session = CallSession(call_id="phone0000007", channel="plivo")

    async def caller():
        await wait_for(lambda: "tts" in holder and holder["tts"].spoken)
        await asyncio.sleep(0.5)
        await stt.say("meri file?")

    task = asyncio.create_task(caller())
    summary = await asyncio.wait_for(run_call(websocket_transport(ws, plivo_serializer(monkeypatch, [])), session, deps), timeout=20)
    await task
    feeder.cancel()
    result_turn = next(r for r in bedrock.requests if any("toolResult" in b for b in r["messages"][-1]["content"]))
    text = result_turn["messages"][-1]["content"][0]["toolResult"]["content"][0]["text"]
    assert '"error": "failed"' in text and summary["ended"] == "done"


@pytest.mark.parametrize("comes_back", [True, False])
async def test_a_transcribe_hiccup_ends_the_call_only_if_the_stream_stays_down(tmp_path, monkeypatch, comes_back):
    from types import SimpleNamespace

    import bot as bot_module
    from prompt import TECH_PROBLEM

    monkeypatch.setattr(bot_module, "STT_GRACE_S", 0.3)
    deps, stt, holder, _ = build(tmp_path, lambda r, m, t: text_events("जी, बताइए।"))
    ws = FakeWebSocket()
    feeder = asyncio.create_task(feed_mulaw(ws))
    session = CallSession(call_id="phone0000008", channel="plivo")

    async def caller():
        await wait_for(lambda: "tts" in holder and holder["tts"].spoken)
        await asyncio.sleep(0.5)
        # Pipecat reports a dropped Transcribe stream, then reconnects it (or not).
        stt._websocket = SimpleNamespace(state=SimpleNamespace(name="OPEN")) if comes_back else None
        await stt.push_error(error_msg="AWSTranscribeSTTService reconnection attempt 1 failed")
        await asyncio.sleep(0.8)
        if comes_back:
            await stt.say("मेरी file?")
            await wait_for(lambda: "जी, बताइए।" in holder["tts"].spoken)
            ws.hang_up()

    task = asyncio.create_task(caller())
    summary = await asyncio.wait_for(run_call(websocket_transport(ws, plivo_serializer(monkeypatch, [])), session, deps), timeout=20)
    await task
    feeder.cancel()
    if comes_back:
        assert summary["ended"] == "caller_hung_up" and TECH_PROBLEM["hi"] not in holder["tts"].spoken
    else:
        assert summary["ended"] == "stt_error" and TECH_PROBLEM["hi"] in holder["tts"].spoken
