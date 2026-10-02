"""Voice agent: Nova Sonic only, Hindi voices, Hinglish prompt, no customer data in logs.

Offline: the Nova Sonic SDK class is swapped for a recorder and the WebSocket
runs against a fake voice model, so nothing calls AWS.

Run: python -m pytest -q packages/agents/bidi-agent/tests -p no:cacheprovider -o addopts=''
"""

import asyncio
import importlib
import logging
import os
import re
import sys

import pytest
import strands.experimental.bidi.models as bidi_models
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from strands.experimental.bidi.types.events import BidiTranscriptStreamEvent
from strands.types._events import ToolUseStreamEvent

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# idp-agent has top-level modules with the same names: import ours, then take
# them out of sys.modules so a combined pytest run keeps each package's own.
LOCAL_MODULES = ("main", "config", "agents", "agentcore_mcp_client")


def _is_local(name: str) -> bool:
    return name.split(".")[0] in LOCAL_MODULES


def _import_bidi_agent():
    shadowed = {name: sys.modules.pop(name) for name in [n for n in sys.modules if _is_local(n)]}
    sys.path.insert(0, AGENT_DIR)
    try:
        return importlib.import_module("main"), importlib.import_module("config")
    finally:
        sys.path.remove(AGENT_DIR)
        for name in [n for n in sys.modules if _is_local(n)]:
            del sys.modules[name]
        sys.modules.update(shadowed)


main, config = _import_bidi_agent()

PAN = "ABCDE1234F"
S3_PROMPT = "You are a helpful female AI voice assistant.\nLanguage Mirroring:\nDo not mix languages in your response."


class NovaRecorder:
    """Stands in for BidiNovaSonicModel and keeps its constructor arguments."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name in ("VOICE_MODEL_REGION", "AGENT_STORAGE_BUCKET_NAME", "SESSION_STORAGE_BUCKET_NAME", "MCP_GATEWAY_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    config.get_config.cache_clear()
    yield
    config.get_config.cache_clear()


@pytest.fixture
def nova(monkeypatch):
    # Set in the module dict: getattr would run the lazy SDK import.
    monkeypatch.setitem(vars(bidi_models), "BidiNovaSonicModel", NovaRecorder)


class TestModel:
    @pytest.mark.parametrize("model_type", ["gemini", "openai", "anthropic", "", None])
    def test_only_nova_sonic_is_accepted(self, model_type, nova):
        with pytest.raises(ValueError, match="Only nova_sonic"):
            config.create_bidi_model(model_type=model_type, voice="tiffany")

    @pytest.mark.parametrize(
        ("voice", "language", "expected"),
        [
            ("kiara", None, "kiara"),
            ("arjun", "Hindi", "arjun"),
            ("matthew", "Hindi", "matthew"),
            ("tiffany", None, "tiffany"),
            (None, "Hindi", "kiara"),
            ("Kore", "Hindi", "kiara"),  # Gemini voice left in an old browser config
            ("alloy", None, "tiffany"),  # OpenAI voice
            (None, "French", "tiffany"),
            (["kiara"], None, "tiffany"),  # malformed config message
        ],
    )
    def test_voice(self, voice, language, expected, nova):
        assert config.resolve_voice(voice, language) == expected
        model = config.create_bidi_model("nova_sonic", voice=voice, language=language)
        assert model.kwargs["model_id"] == "amazon.nova-2-sonic-v1:0"
        assert model.kwargs["provider_config"] == {"audio": {"voice": expected}}

    def test_region_is_the_voice_model_region(self, monkeypatch, nova):
        monkeypatch.setenv("VOICE_MODEL_REGION", "ap-northeast-1")
        config.get_config.cache_clear()
        assert config.create_bidi_model("nova_sonic").kwargs["client_config"] == {"region": "ap-northeast-1"}

    def test_real_sdk_model_takes_the_config(self):
        pytest.importorskip("aws_sdk_bedrock_runtime")
        model = config.create_bidi_model("nova_sonic", voice="kiara")
        assert type(model).__name__ == "BidiNovaSonicModel"
        assert model.model_id == "amazon.nova-2-sonic-v1:0"
        assert model.config["audio"]["voice"] == "kiara"


class TestPrompt:
    def test_hinglish_is_allowed_and_korean_is_gone(self, monkeypatch):
        monkeypatch.setattr(main, "fetch_voice_system_prompt", lambda: None)
        prompt = main.build_system_prompt("Asia/Kolkata", voice="kiara")
        assert "Default to Hindi" in prompt
        assert "Hinglish" in prompt
        assert "do not mix" not in prompt.lower()
        assert "korean" not in prompt.lower()

    def test_language_rules_follow_the_stored_prompt(self, monkeypatch):
        # The S3 prompt is seeded once and still says not to mix languages.
        monkeypatch.setattr(main, "fetch_voice_system_prompt", lambda: S3_PROMPT)
        prompt = main.build_system_prompt("Asia/Kolkata", voice="arjun")
        assert prompt.index("replace any earlier language instructions") > prompt.index("Do not mix languages")
        assert prompt.index("Your voice is male.") > prompt.index("female AI voice assistant")

    @pytest.mark.parametrize(
        ("voice", "gender"), [("kiara", "female"), ("tiffany", "female"), ("arjun", "male"), ("matthew", "male")]
    )
    def test_matches_the_voice_gender(self, voice, gender, monkeypatch):
        monkeypatch.setattr(main, "fetch_voice_system_prompt", lambda: None)
        assert f"Your voice is {gender}." in main.build_system_prompt("UTC", voice=voice)

    @pytest.mark.parametrize("timezone", ["Asia/Seoul", "Asia/Tokyo", "Asia/Shanghai"])
    def test_no_default_to_languages_nova_sonic_cannot_speak(self, timezone, monkeypatch):
        monkeypatch.setattr(main, "fetch_voice_system_prompt", lambda: None)
        assert "Default to" not in main.build_system_prompt(timezone)


class FakeMcp:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def call_tool_sync(self, name, arguments, tool_use_id):
        self.calls.append((name, dict(arguments)))
        if self.error:
            raise self.error
        return self.result


SEARCH_RESULT = {"toolUseId": "t-1", "status": "success", "content": [{"text": f"PAN {PAN}, a/c 123456789012"}]}


def use_mcp(monkeypatch, mcp, name="search___summarize"):
    monkeypatch.setattr(main, "mcp_client", mcp)
    monkeypatch.setattr(main, "mcp_injectable_names", {name})


class TestToolLogs:
    def run(self, monkeypatch, mcp):
        use_mcp(monkeypatch, mcp)
        tool_use = {"name": "search___summarize", "toolUseId": "t-1", "input": {"query": f"loan for PAN {PAN}"}}
        return asyncio.run(main.execute_tool(tool_use, {"user_id": "user-1", "project_id": "proj-1"}))

    def test_name_status_and_duration_only(self, monkeypatch, caplog):
        mcp = FakeMcp(result=SEARCH_RESULT)
        with caplog.at_level(logging.DEBUG):
            result = self.run(monkeypatch, mcp)
        assert result == SEARCH_RESULT
        query = {"query": f"loan for PAN {PAN}", "user_id": "user-1", "project_id": "proj-1"}
        assert mcp.calls == [("search___summarize", query)]
        assert PAN not in caplog.text and "123456789012" not in caplog.text
        assert re.search(r"Tool search___summarize: status=success, duration_ms=\d+", caplog.text)

    def test_failure_logs_the_error_type_only(self, monkeypatch, caplog):
        mcp = FakeMcp(error=ValueError(f"bad query: loan for PAN {PAN}"))
        with caplog.at_level(logging.DEBUG):
            result = self.run(monkeypatch, mcp)
        assert result["status"] == "error"
        assert PAN not in caplog.text
        assert "MCP tool execution failed: search___summarize (ValueError)" in caplog.text
        assert "Tool search___summarize: status=error" in caplog.text


class FakeVoiceModel:
    """Replays `events` as the voice model's output and records what it is sent."""

    def __init__(self, events):
        self.events, self.sent, self.system_prompt = events, [], None

    async def start(self, system_prompt=None, tools=None, **kwargs):
        self.system_prompt = system_prompt

    async def send(self, event):
        self.sent.append(event)

    async def receive(self):
        for event in self.events:
            yield event

    async def stop(self):
        pass


def session_config(**overrides):
    return {
        "model_type": "nova_sonic",
        "voice": "",
        "system_prompt": "",
        "browser_time_zone": "Asia/Kolkata",
        "session_id": "s-1",
        "project_id": "proj-1",
        "user_id": "user-1",
        **overrides,
    }


class TestWebSocket:
    def test_other_voice_models_are_refused(self, monkeypatch):
        savers = []
        monkeypatch.setattr(main, "TranscriptSaver", lambda **kwargs: savers.append(kwargs))
        with TestClient(main.app).websocket_connect("/ws") as ws:
            ws.send_json(session_config(model_type="gemini", api_key="AIza-test", voice="Kore"))
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
        assert closed.value.code == 1011
        assert "Only nova_sonic" in closed.value.reason
        assert savers == []  # refused before anything is stored

    def test_hindi_session_without_pan_in_logs(self, monkeypatch, caplog):
        speech = f"Mera PAN {PAN} hai, loan status batao"
        tool_use = {"toolUseId": "t-1", "name": "search___summarize", "input": {"query": f"PAN {PAN}"}}
        model = FakeVoiceModel(
            [
                BidiTranscriptStreamEvent(delta={"text": speech}, text=speech, role="user", is_final=False),
                ToolUseStreamEvent(delta={}, current_tool_use=tool_use),
            ]
        )
        created = []
        monkeypatch.setattr(main, "create_bidi_model", lambda **kwargs: created.append(kwargs) or model)
        monkeypatch.setattr(main, "fetch_voice_system_prompt", lambda: S3_PROMPT)
        use_mcp(monkeypatch, FakeMcp(result=SEARCH_RESULT))

        with caplog.at_level(logging.DEBUG), TestClient(main.app).websocket_connect("/ws") as ws:
            ws.send_json(session_config(api_key="sk-ignored"))
            messages = [ws.receive_json() for _ in range(3)]
            ws.send_json({"type": "stop"})

        assert created == [{"model_type": "nova_sonic", "voice": "kiara"}]  # Hindi default, no API key
        assert "Your voice is female." in model.system_prompt and "Hinglish" in model.system_prompt
        assert [m["type"] for m in messages] == ["transcript", "tool_use", "tool_result"]
        assert messages[0]["text"] == speech  # the browser still gets the words
        assert messages[2]["status"] == "success"
        assert [e["tool_result"]["status"] for e in model.sent] == ["success"]
        assert f"Transcript: role=user, is_final=False, chars={len(speech)}" in caplog.text
        assert PAN not in caplog.text and "123456789012" not in caplog.text
