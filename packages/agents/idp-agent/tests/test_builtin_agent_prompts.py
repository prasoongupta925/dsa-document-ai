"""Agent prompt loading: built-in agents (builtin-*) and custom agents.

S3 is a real boto3 client stubbed with botocore's Stubber (offline, fake
credentials): each test states the exact bucket and key it expects, and a
malformed id must not reach S3 at all.

Run: python -m pytest -q packages/agents/idp-agent/tests -p no:cacheprovider -o addopts=''
"""

import io
import json
import os
import sys

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

import prompts  # noqa: E402
from config import get_config  # noqa: E402

BUCKET = "idp-v2-agent-storage-test-ap-south-1"
USER, PROJECT = "user-1", "proj-1"
FAKE_AWS_ENV = {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "ap-south-1",
    "AWS_REGION": "ap-south-1",
    "AWS_EC2_METADATA_DISABLED": "true",
    "AWS_CONFIG_FILE": os.devnull,
    "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
}


def _body(data) -> dict:
    raw = (data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)).encode("utf-8")
    return {"Body": StreamingBody(io.BytesIO(raw), len(raw))}


def builtin(agent_id: str, content: str = "Call filecheck___run_file_check first.") -> dict:
    return {"agent_id": agent_id, "name": "File Checker", "description": "Checks files.", "content": content}


@pytest.fixture
def env(monkeypatch):
    for name, value in FAKE_AWS_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AGENT_STORAGE_BUCKET_NAME", BUCKET)
    get_config.cache_clear()
    yield
    get_config.cache_clear()


@pytest.fixture
def s3(env, monkeypatch):
    """Stubbed S3 client handed to prompts; `.created` counts the clients prompts asked for."""
    client = boto3.client("s3", region_name="ap-south-1")
    stubber = Stubber(client)
    stubber.created = 0

    def factory(service_name, *args, **kwargs):
        assert service_name == "s3"
        stubber.created += 1
        return client

    monkeypatch.setattr(prompts.boto3, "client", factory)
    with stubber:
        yield stubber
        stubber.assert_no_pending_responses()


def expect_get(stubber, key, data):
    stubber.add_response("get_object", _body(data), {"Bucket": BUCKET, "Key": key})


class TestBuiltinAgents:
    def test_builtin_prompt_is_read_from_the_shared_prefix(self, s3):
        expect_get(s3, "__prompts/builtin_agents/builtin-file-checker.json", builtin("builtin-file-checker"))

        content = prompts.fetch_custom_agent_prompt(USER, PROJECT, "builtin-file-checker")

        assert content == "Call filecheck___run_file_check first."

    def test_the_same_builtin_for_every_user_and_project(self, s3):
        key = "__prompts/builtin_agents/builtin-loan-assistant.json"
        for _ in range(2):
            expect_get(s3, key, builtin("builtin-loan-assistant", "मराठी आणि हिंदी"))

        assert prompts.fetch_custom_agent_prompt("alice", "p-a", "builtin-loan-assistant") == "मराठी आणि हिंदी"
        assert prompts.fetch_custom_agent_prompt("bob", "p-b", "builtin-loan-assistant") == "मराठी आणि हिंदी"

    def test_longest_builtin_id(self, s3):
        agent_id = "builtin-" + "a" * 64
        expect_get(s3, f"__prompts/builtin_agents/{agent_id}.json", builtin(agent_id))

        assert prompts.fetch_custom_agent_prompt(USER, PROJECT, agent_id)

    def test_missing_builtin_returns_none(self, s3):
        s3.add_client_error(
            "get_object",
            service_error_code="NoSuchKey",
            http_status_code=404,
            expected_params={"Bucket": BUCKET, "Key": "__prompts/builtin_agents/builtin-not-shipped.json"},
        )

        assert prompts.fetch_custom_agent_prompt(USER, PROJECT, "builtin-not-shipped") is None

    def test_s3_error_returns_none(self, s3):
        s3.add_client_error(
            "get_object",
            service_error_code="AccessDenied",
            http_status_code=403,
            expected_params={"Bucket": BUCKET, "Key": "__prompts/builtin_agents/builtin-file-checker.json"},
        )

        assert prompts.fetch_custom_agent_prompt(USER, PROJECT, "builtin-file-checker") is None

    @pytest.mark.parametrize(
        "data",
        [
            builtin("builtin-someone-else"),  # the file must name its own id
            {"name": "File Checker", "content": "x"},  # no agent_id
            {"agent_id": "builtin-file-checker", "content": 42},
            ["builtin-file-checker"],
            "{not json",
        ],
    )
    def test_malformed_builtin_file_returns_none(self, s3, data):
        expect_get(s3, "__prompts/builtin_agents/builtin-file-checker.json", data)

        assert prompts.fetch_custom_agent_prompt(USER, PROJECT, "builtin-file-checker") is None

    @pytest.mark.parametrize(
        "agent_id",
        [
            "builtin-",
            "builtin-File-Checker",
            "builtin-file_checker",
            "builtin-file.checker",
            "builtin-../chat/system_prompt",
            "builtin-x/../../other-user/p/agents/y",
            "builtin-file-checker\n",
            "builtin-file-checker ",
            "builtin-" + "a" * 65,
            "builtin-*",
        ],
    )
    def test_malformed_builtin_id_is_rejected_without_reading_s3(self, s3, agent_id):
        assert prompts.fetch_custom_agent_prompt(USER, PROJECT, agent_id) is None
        assert s3.created == 0

    def test_builtin_prompt_is_added_to_the_system_prompt(self, s3, monkeypatch):
        monkeypatch.setattr(prompts, "fetch_system_prompt", lambda: "BASE PROMPT")
        expect_get(s3, "__prompts/builtin_agents/builtin-file-checker.json", builtin("builtin-file-checker"))

        system_prompt = prompts.build_system_prompt(
            project_id=PROJECT, user_id=USER, agent_id="builtin-file-checker", language_code="en"
        )

        assert system_prompt.startswith("BASE PROMPT")
        assert "## Custom Instructions\nCall filecheck___run_file_check first." in system_prompt
        # The project language comes before the agent's instructions, and the agent may override it
        # (e.g. the Varunika Loans assistant replies in the customer's Hindi or Marathi).
        assert system_prompt.index("code: en") < system_prompt.index("Custom Instructions")
        assert "follow them instead" in system_prompt

    def test_language_rule_without_agent_has_no_exception(self, monkeypatch):
        monkeypatch.setattr(prompts, "fetch_system_prompt", lambda: "BASE PROMPT")

        system_prompt = prompts.build_system_prompt(language_code="hi")

        assert "You MUST respond in the language corresponding to code: hi." in system_prompt
        assert "follow them instead" not in system_prompt
        assert "Custom Instructions" not in system_prompt


class TestCustomAgents:
    def test_custom_agent_keeps_its_per_user_key(self, s3):
        agent_id = "0f0c5a8e-1111-4c1b-9d52-3e7c2a1b0d01"
        expect_get(s3, f"{USER}/{PROJECT}/agents/{agent_id}.json", {"name": "Mine", "content": "Be brief."})

        assert prompts.fetch_custom_agent_prompt(USER, PROJECT, agent_id) == "Be brief."

    def test_similar_prefix_is_a_custom_agent(self, s3):
        # Only the exact lower-case "builtin-" prefix names a built-in.
        expect_get(s3, f"{USER}/{PROJECT}/agents/Builtin-file-checker.json", {"content": "Mine"})

        assert prompts.fetch_custom_agent_prompt(USER, PROJECT, "Builtin-file-checker") == "Mine"

    def test_missing_custom_agent_returns_none(self, s3):
        s3.add_client_error(
            "get_object",
            service_error_code="NoSuchKey",
            http_status_code=404,
            expected_params={"Bucket": BUCKET, "Key": f"{USER}/{PROJECT}/agents/gone.json"},
        )

        assert prompts.fetch_custom_agent_prompt(USER, PROJECT, "gone") is None

    @pytest.mark.parametrize(
        "agent_id", ["", ".", "..", "../x", "a/b", "a\\b", "a*b", "a?b", "a[b]", "a\nb", "x" * 257]
    )
    def test_malformed_custom_id_is_rejected_without_reading_s3(self, s3, agent_id):
        assert prompts.fetch_custom_agent_prompt(USER, PROJECT, agent_id) is None
        assert s3.created == 0


def test_bucket_not_configured_reads_nothing(s3, monkeypatch):
    monkeypatch.setenv("AGENT_STORAGE_BUCKET_NAME", "")
    get_config.cache_clear()

    assert prompts.fetch_custom_agent_prompt(USER, PROJECT, "builtin-file-checker") is None
    assert s3.created == 0


SHIPPED_DIR = os.path.join(os.path.dirname(os.path.dirname(AGENT_DIR)), "infra", "src", "prompts", "builtin_agents")
SHIPPED = sorted(name for name in os.listdir(SHIPPED_DIR) if name.endswith(".json"))


@pytest.mark.parametrize("file_name", SHIPPED)
def test_every_shipped_builtin_loads(s3, file_name):
    """The files AgentStack uploads are accepted as they are (the id in the file matches its name)."""
    with open(os.path.join(SHIPPED_DIR, file_name), encoding="utf-8") as f:
        raw = f.read()
    agent_id = file_name[: -len(".json")]
    expect_get(s3, f"__prompts/builtin_agents/{file_name}", raw)

    content = prompts.fetch_custom_agent_prompt(USER, PROJECT, agent_id)

    assert content == json.loads(raw)["content"]
    assert len(SHIPPED) == 4
