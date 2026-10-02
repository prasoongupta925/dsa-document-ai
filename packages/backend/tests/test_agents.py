"""Agents API with built-in agents.

Built-in agents live in the agent bucket under __prompts/builtin_agents/ and are
listed for every user in every project after the user's own agents. They can
be read (GET) but never changed: PUT and DELETE on a built-in id give 403, and
POST only ever creates UUID ids. S3 is faked in memory (or stubbed with
botocore's Stubber on a real client); nothing reaches AWS.
"""

import json
import re
import uuid
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.response import StreamingBody
from botocore.stub import Stubber
from fastapi.testclient import TestClient

import app.routers.agents as agents_router
from app.config import Config
from app.duckdb import AgentListItem
from app.main import app

client = TestClient(app)

AGENT_BUCKET = "idp-v2-agent-storage-test-ap-south-1"
USER = {"x-user-id": "user-1"}
MODIFIED = datetime(2026, 9, 30, 9, 30, tzinfo=UTC)
PREFIX = "__prompts/builtin_agents/"
READ_ONLY = {"detail": "Built-in agents are read-only"}


def builtin_file(agent_id, name, description="What it does.", content="Instructions."):
    return json.dumps(
        {"agent_id": agent_id, "name": name, "description": description, "content": content},
        ensure_ascii=False,
    )


BUILTINS = {
    f"{PREFIX}builtin-file-checker.json": builtin_file(
        "builtin-file-checker", "File Checker", "Runs the file check.", "Call the file check tool."
    ),
    f"{PREFIX}builtin-call-qa-reviewer.json": builtin_file(
        "builtin-call-qa-reviewer", "Call QA Reviewer", "Scores calls.", "Score the call."
    ),
}


class NoSuchKey(ClientError):
    def __init__(self, operation="GetObject"):
        super().__init__({"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}}, operation)


class FakeS3:
    """One in-memory bucket with the boto3 call shapes the router uses; records every call."""

    def __init__(self, objects=None, list_error=None, page_size=2):
        self.objects = {
            key: value.encode() if isinstance(value, str) else value for key, value in (objects or {}).items()
        }
        self.list_error = list_error
        self.page_size = page_size
        self.calls = []
        self.exceptions = SimpleNamespace(NoSuchKey=NoSuchKey, ClientError=ClientError)

    def get_paginator(self, operation):
        assert operation == "list_objects_v2"
        return self

    def paginate(self, Bucket, Prefix):
        self.calls.append(("list", Bucket, Prefix))
        if self.list_error:
            raise self.list_error
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        for start in range(0, len(keys), self.page_size):
            yield {"Contents": [{"Key": key, "LastModified": MODIFIED} for key in keys[start : start + self.page_size]]}

    def get_object(self, Bucket, Key):
        self.calls.append(("get", Bucket, Key))
        if Key not in self.objects:
            raise NoSuchKey()
        return {"Body": BytesIO(self.objects[Key]), "LastModified": MODIFIED}

    def head_object(self, Bucket, Key):
        self.calls.append(("head", Bucket, Key))
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.calls.append(("put", Bucket, Key))
        self.objects[Key] = Body
        return {}

    def delete_object(self, Bucket, Key):
        self.calls.append(("delete", Bucket, Key))
        self.objects.pop(Key, None)
        return {}

    def keys_called(self, kind):
        return [key for call, _, key in self.calls if call == kind]


def custom_agent(agent_id, name, created_at="2026-09-29T10:00:00+00:00"):
    return AgentListItem(agent_id=agent_id, name=name, created_at=created_at)


@pytest.fixture
def config():
    cfg = Config(aws_region="ap-south-1", agent_storage_bucket_name=AGENT_BUCKET)
    with patch("app.routers.agents.get_config", return_value=cfg):
        yield cfg


def use_s3(fake):
    return patch("app.routers.agents.get_s3_client", return_value=fake)


@pytest.fixture
def custom_agents():
    """The user's own agents, as the cached DuckDB listing returns them."""
    agents = [custom_agent("0f0c5a8e-1111-4c1b-9d52-3e7c2a1b0d01", "My Credit Checker")]
    with patch("app.cache.cached_query_agents", new=AsyncMock(return_value=agents)) as listing:
        yield listing


# ---------------------------------------------------------------------------
# GET /projects/{id}/agents
# ---------------------------------------------------------------------------


class TestListAgents:
    def test_custom_agents_then_builtins_with_the_builtin_flag(self, config, custom_agents):
        fake = FakeS3(BUILTINS)
        with use_s3(fake):
            response = client.get("/projects/proj-1/agents", headers=USER)

        assert response.status_code == 200
        body = response.json()
        assert body[0] == {
            "agent_id": "0f0c5a8e-1111-4c1b-9d52-3e7c2a1b0d01",
            "name": "My Credit Checker",
            "created_at": "2026-09-29T10:00:00+00:00",
            "builtin": False,
            "description": None,
        }
        # Built-ins after the custom agents, sorted by name.
        assert body[1:] == [
            {
                "agent_id": "builtin-call-qa-reviewer",
                "name": "Call QA Reviewer",
                "created_at": MODIFIED.isoformat(),
                "builtin": True,
                "description": "Scores calls.",
            },
            {
                "agent_id": "builtin-file-checker",
                "name": "File Checker",
                "created_at": MODIFIED.isoformat(),
                "builtin": True,
                "description": "Runs the file check.",
            },
        ]
        custom_agents.assert_awaited_once_with("user-1", "proj-1")
        assert fake.calls[0] == ("list", AGENT_BUCKET, PREFIX)
        # The list never carries prompt content.
        assert all("content" not in item for item in body)

    def test_only_well_formed_direct_children_are_listed(self, config, custom_agents):
        objects = {
            **BUILTINS,
            # A custom agent saved with x-user-id "__prompts" and project "builtin_agents".
            f"{PREFIX}agents/builtin-evil.json": builtin_file("builtin-evil", "Evil"),
            f"{PREFIX}builtin-Bad_Id.json": builtin_file("builtin-Bad_Id", "Bad id"),
            f"{PREFIX}builtin-no-json.txt": "not an agent",
            f"{PREFIX}README.md": "# notes",
            f"{PREFIX}builtin-broken.json": "{not json",
            f"{PREFIX}builtin-wrong-id.json": builtin_file("builtin-other", "Names another id"),
            f"{PREFIX}builtin-no-content.json": builtin_file("builtin-no-content", "Empty", content=" "),
            f"{PREFIX}builtin-no-name.json": json.dumps({"agent_id": "builtin-no-name", "content": "x"}),
            f"{PREFIX}builtin-list.json": json.dumps(["builtin-list"]),
        }
        fake = FakeS3(objects)
        with use_s3(fake):
            response = client.get("/projects/proj-1/agents", headers=USER)

        assert response.status_code == 200
        builtin_ids = [item["agent_id"] for item in response.json() if item["builtin"]]
        assert builtin_ids == ["builtin-call-qa-reviewer", "builtin-file-checker"]
        read = fake.keys_called("get")
        assert f"{PREFIX}agents/builtin-evil.json" not in read
        assert f"{PREFIX}builtin-Bad_Id.json" not in read
        assert f"{PREFIX}README.md" not in read

    def test_custom_agent_with_a_builtin_id_is_not_listed(self, config):
        # Written before the prefix was reserved; the runtime would load the built-in instead.
        legacy = [
            custom_agent("builtin-file-checker", "My shadow copy"),
            custom_agent("0f0c5a8e-2222-4c1b-9d52-3e7c2a1b0d02", "Mine"),
        ]
        with (
            patch("app.cache.cached_query_agents", new=AsyncMock(return_value=legacy)),
            use_s3(FakeS3(BUILTINS)),
        ):
            response = client.get("/projects/proj-1/agents", headers=USER)

        items = response.json()
        assert [(item["agent_id"], item["builtin"]) for item in items] == [
            ("0f0c5a8e-2222-4c1b-9d52-3e7c2a1b0d02", False),
            ("builtin-call-qa-reviewer", True),
            ("builtin-file-checker", True),
        ]
        assert "My shadow copy" not in [item["name"] for item in items]

    @pytest.mark.parametrize(
        "list_error",
        [
            ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "ListObjectsV2"),
            ClientError({"Error": {"Code": "NoSuchBucket", "Message": "gone"}}, "ListObjectsV2"),
            EndpointConnectionError(endpoint_url="https://s3.ap-south-1.amazonaws.com"),
        ],
    )
    def test_bucket_unavailable_returns_custom_agents_only(self, config, custom_agents, list_error):
        with use_s3(FakeS3(BUILTINS, list_error=list_error)):
            response = client.get("/projects/proj-1/agents", headers=USER)

        assert response.status_code == 200
        assert [item["agent_id"] for item in response.json()] == ["0f0c5a8e-1111-4c1b-9d52-3e7c2a1b0d01"]

    def test_unreadable_builtin_file_returns_custom_agents_only(self, config, custom_agents):
        class BrokenGet(FakeS3):
            def get_object(self, Bucket, Key):
                raise ClientError({"Error": {"Code": "SlowDown", "Message": "slow"}}, "GetObject")

        with use_s3(BrokenGet(BUILTINS)):
            response = client.get("/projects/proj-1/agents", headers=USER)

        assert response.status_code == 200
        assert [item["builtin"] for item in response.json()] == [False]

    def test_no_builtins_in_the_bucket_returns_custom_agents_only(self, config, custom_agents):
        with use_s3(FakeS3({})):
            response = client.get("/projects/proj-1/agents", headers=USER)

        assert [item["builtin"] for item in response.json()] == [False]

    def test_bucket_not_configured_returns_custom_agents_only(self, custom_agents):
        s3 = MagicMock()
        with (
            patch("app.routers.agents.get_config", return_value=Config(agent_storage_bucket_name="")),
            use_s3(s3),
        ):
            response = client.get("/projects/proj-1/agents", headers=USER)

        assert response.status_code == 200
        assert [item["builtin"] for item in response.json()] == [False]
        s3.get_paginator.assert_not_called()

    def test_no_custom_agents_lists_the_builtins(self, config):
        with (
            patch("app.cache.cached_query_agents", new=AsyncMock(return_value=[])),
            use_s3(FakeS3(BUILTINS)),
        ):
            response = client.get("/projects/any-project/agents", headers={"x-user-id": "someone-else"})

        assert [item["agent_id"] for item in response.json()] == ["builtin-call-qa-reviewer", "builtin-file-checker"]

    def test_builtins_are_read_on_every_request_so_a_failure_only_hits_that_one(self, config):
        # app.cache no longer caches (no Valkey): each request lists the bucket.
        fake = FakeS3(BUILTINS)

        with (
            patch("app.cache.cached_query_agents", new=AsyncMock(return_value=[])),
            use_s3(fake),
        ):
            fake.list_error = ClientError({"Error": {"Code": "SlowDown", "Message": "slow"}}, "ListObjectsV2")
            assert client.get("/projects/proj-1/agents", headers=USER).json() == []

            fake.list_error = None
            first = client.get("/projects/proj-1/agents", headers=USER).json()
            listed = len(fake.keys_called("list"))
            second = client.get("/projects/proj-2/agents", headers={"x-user-id": "user-2"}).json()

        assert [item["agent_id"] for item in first] == ["builtin-call-qa-reviewer", "builtin-file-checker"]
        assert first[0]["builtin"] is True
        assert second == first
        assert len(fake.keys_called("list")) == listed + 1  # read again, not served from a cache

    @pytest.mark.parametrize(
        ("url", "user"),
        [
            ("/projects/proj-1/agents", "*"),
            ("/projects/proj-1/agents", "user-1/../user-2"),
            ("/projects/pr*/agents", "user-1"),
            ("/projects/p%3F/agents", "user-1"),
        ],
    )
    def test_unsafe_user_or_project_id_is_400(self, url, user):
        with patch("app.cache.cached_query_agents", new=AsyncMock()) as listing:
            response = client.get(url, headers={"x-user-id": user})

        assert response.status_code == 400
        listing.assert_not_awaited()


# ---------------------------------------------------------------------------
# GET /projects/{id}/agents/{agent_id}
# ---------------------------------------------------------------------------


class TestGetAgent:
    def test_builtin_agent_returns_its_content_read_only(self, config):
        fake = FakeS3(BUILTINS)
        with use_s3(fake):
            response = client.get("/projects/proj-1/agents/builtin-file-checker", headers=USER)

        assert response.status_code == 200
        assert response.json() == {
            "agent_id": "builtin-file-checker",
            "name": "File Checker",
            "content": "Call the file check tool.",
            "created_at": MODIFIED.isoformat(),
            "builtin": True,
            "description": "Runs the file check.",
        }
        # The same shared object for every user and project.
        assert fake.calls == [("get", AGENT_BUCKET, f"{PREFIX}builtin-file-checker.json")]

    def test_unknown_builtin_agent_is_404(self, config):
        with use_s3(FakeS3(BUILTINS)):
            response = client.get("/projects/proj-1/agents/builtin-not-shipped", headers=USER)

        assert response.status_code == 404
        assert response.json() == {"detail": "Agent not found"}

    @pytest.mark.parametrize(
        "body",
        [
            "{not json",
            builtin_file("builtin-someone-else", "Wrong id"),
            json.dumps({"agent_id": "builtin-file-checker", "name": "File Checker"}),
        ],
    )
    def test_malformed_builtin_file_is_404(self, config, body):
        with use_s3(FakeS3({f"{PREFIX}builtin-file-checker.json": body})):
            response = client.get("/projects/proj-1/agents/builtin-file-checker", headers=USER)

        assert response.status_code == 404

    @pytest.mark.parametrize(
        "agent_id",
        [
            "builtin-",
            "builtin-File-Checker",
            "builtin-file_checker",
            "builtin-file.checker",
            "builtin-%2E%2E",
            "builtin-a%0A",
            "builtin-" + "a" * 65,
            "builtin-%2A",
        ],
    )
    def test_malformed_builtin_id_is_400_without_reading_s3(self, config, agent_id):
        s3 = MagicMock()
        with use_s3(s3):
            response = client.get(f"/projects/proj-1/agents/{agent_id}", headers=USER)

        assert response.status_code == 400
        assert response.json() == {"detail": "Invalid agent id"}
        s3.get_object.assert_not_called()

    def test_longest_builtin_id_is_accepted(self, config):
        agent_id = "builtin-" + "a" * 64
        with use_s3(FakeS3({f"{PREFIX}{agent_id}.json": builtin_file(agent_id, "Long")})):
            response = client.get(f"/projects/proj-1/agents/{agent_id}", headers=USER)

        assert response.status_code == 200
        assert response.json()["builtin"] is True

    def test_custom_agent_is_read_from_the_users_folder(self, config):
        key = "user-1/proj-1/agents/0f0c5a8e-1111-4c1b-9d52-3e7c2a1b0d01.json"
        stored = {"name": "Mine", "content": "Be brief.", "created_at": "2026-09-29T10:00:00+00:00"}
        fake = FakeS3({key: json.dumps(stored)})
        with use_s3(fake):
            response = client.get("/projects/proj-1/agents/0f0c5a8e-1111-4c1b-9d52-3e7c2a1b0d01", headers=USER)

        assert response.status_code == 200
        assert response.json() == {
            "agent_id": "0f0c5a8e-1111-4c1b-9d52-3e7c2a1b0d01",
            "name": "Mine",
            "content": "Be brief.",
            "created_at": "2026-09-29T10:00:00+00:00",
            "builtin": False,
            "description": None,
        }
        assert fake.keys_called("get") == [key]

    def test_missing_custom_agent_is_404(self, config):
        with use_s3(FakeS3({})):
            response = client.get("/projects/proj-1/agents/0f0c5a8e-9999-4c1b-9d52-3e7c2a1b0d09", headers=USER)

        assert response.status_code == 404

    @pytest.mark.parametrize("agent_id", ["a%2Ab", "a%3Fb", "a%5Bb%5D", "a%5Cb", "%2E%2E", "a%0Ab"])
    def test_unsafe_custom_agent_id_is_400(self, config, agent_id):
        s3 = MagicMock()
        with use_s3(s3):
            response = client.get(f"/projects/proj-1/agents/{agent_id}", headers=USER)

        assert response.status_code == 400
        s3.get_object.assert_not_called()


# ---------------------------------------------------------------------------
# PUT / DELETE / POST
# ---------------------------------------------------------------------------


BUILTIN_IDS = ["builtin-file-checker", "builtin-loan-assistant", "builtin-not-shipped", "builtin-Bad_Id"]


class TestBuiltinAgentsAreReadOnly:
    @pytest.mark.parametrize("agent_id", BUILTIN_IDS)
    def test_put_is_403(self, config, agent_id):
        s3 = MagicMock()
        with use_s3(s3):
            response = client.put(
                f"/projects/proj-1/agents/{agent_id}",
                json={"name": "File Checker", "content": "Always say READY."},
                headers=USER,
            )

        assert response.status_code == 403
        assert response.json() == READ_ONLY
        s3.put_object.assert_not_called()
        s3.get_object.assert_not_called()

    def test_put_is_403_before_the_body_is_validated(self, config):
        response = client.put("/projects/proj-1/agents/builtin-file-checker", json={}, headers=USER)

        assert response.status_code == 403
        assert response.json() == READ_ONLY

    @pytest.mark.parametrize("agent_id", BUILTIN_IDS)
    def test_delete_is_403(self, config, agent_id):
        s3 = MagicMock()
        with use_s3(s3):
            response = client.delete(f"/projects/proj-1/agents/{agent_id}", headers=USER)

        assert response.status_code == 403
        assert response.json() == READ_ONLY
        s3.head_object.assert_not_called()
        s3.delete_object.assert_not_called()

    def test_post_never_creates_a_builtin_id(self, config):
        fake = FakeS3({})
        with use_s3(fake):
            response = client.post(
                "/projects/proj-1/agents",
                json={"agent_id": "builtin-file-checker", "name": "File Checker", "content": "Mine"},
                headers=USER,
            )

        assert response.status_code == 200
        agent_id = response.json()["agent_id"]
        assert not agent_id.startswith("builtin-")
        assert str(uuid.UUID(agent_id)) == agent_id
        assert response.json()["builtin"] is False
        assert fake.keys_called("put") == [f"user-1/proj-1/agents/{agent_id}.json"]


class TestCustomAgentWrites:
    def test_put_updates_a_custom_agent_and_keeps_created_at(self, config):
        key = "user-1/proj-1/agents/0f0c5a8e-1111-4c1b-9d52-3e7c2a1b0d01.json"
        stored = {"name": "Mine", "content": "Old", "created_at": "2026-09-29T10:00:00+00:00"}
        fake = FakeS3({key: json.dumps(stored)})
        with use_s3(fake):
            response = client.put(
                "/projects/proj-1/agents/0f0c5a8e-1111-4c1b-9d52-3e7c2a1b0d01",
                json={"name": "Mine", "content": "New"},
                headers=USER,
            )

        assert response.status_code == 200
        assert response.json()["created_at"] == "2026-09-29T10:00:00+00:00"
        assert json.loads(fake.objects[key])["content"] == "New"

    def test_delete_removes_a_custom_agent(self, config):
        key = "user-1/proj-1/agents/0f0c5a8e-1111-4c1b-9d52-3e7c2a1b0d01.json"
        fake = FakeS3({key: json.dumps({"name": "Mine", "content": "x"})})
        with use_s3(fake):
            response = client.delete("/projects/proj-1/agents/0f0c5a8e-1111-4c1b-9d52-3e7c2a1b0d01", headers=USER)

        assert response.status_code == 200
        assert key not in fake.objects

    def test_delete_missing_custom_agent_is_404(self, config):
        with use_s3(FakeS3({})):
            response = client.delete("/projects/proj-1/agents/0f0c5a8e-9999-4c1b-9d52-3e7c2a1b0d09", headers=USER)

        assert response.status_code == 404

    @pytest.mark.parametrize("method", ["put", "delete"])
    def test_unsafe_custom_agent_id_is_400(self, config, method):
        s3 = MagicMock()
        kwargs = {"json": {"name": "x", "content": "y"}} if method == "put" else {}
        with use_s3(s3):
            response = getattr(client, method)("/projects/proj-1/agents/a%2Ab", headers=USER, **kwargs)

        assert response.status_code == 400
        s3.put_object.assert_not_called()
        s3.delete_object.assert_not_called()


# ---------------------------------------------------------------------------
# Real boto3 client (botocore Stubber): the calls are valid S3 API requests
# ---------------------------------------------------------------------------


def _streaming(text):
    raw = text.encode("utf-8")
    return StreamingBody(BytesIO(raw), len(raw))


class TestWithBotocoreStubber:
    def test_list_and_get_builtin_with_a_real_client(self, config, custom_agents):
        s3 = boto3.client("s3", region_name="ap-south-1")
        key = f"{PREFIX}builtin-file-checker.json"
        body = builtin_file("builtin-file-checker", "File Checker", "Runs the file check.", "मराठी ठीक आहे")
        with Stubber(s3) as stub, use_s3(s3):
            stub.add_response(
                "list_objects_v2",
                {
                    "IsTruncated": False,
                    "KeyCount": 2,
                    "Contents": [
                        {"Key": f"{PREFIX}agents/builtin-evil.json", "LastModified": MODIFIED, "Size": 10},
                        {"Key": key, "LastModified": MODIFIED, "Size": len(body)},
                    ],
                },
                {"Bucket": AGENT_BUCKET, "Prefix": PREFIX},
            )
            stub.add_response(
                "get_object",
                {"Body": _streaming(body), "LastModified": MODIFIED},
                {"Bucket": AGENT_BUCKET, "Key": key},
            )
            stub.add_response(
                "get_object",
                {"Body": _streaming(body), "LastModified": MODIFIED},
                {"Bucket": AGENT_BUCKET, "Key": key},
            )
            listed = client.get("/projects/proj-1/agents", headers=USER)
            detail = client.get("/projects/proj-1/agents/builtin-file-checker", headers=USER)
            stub.assert_no_pending_responses()

        assert [item["agent_id"] for item in listed.json()][-1] == "builtin-file-checker"
        assert detail.status_code == 200
        assert detail.json()["content"] == "मराठी ठीक आहे"

    def test_missing_builtin_with_a_real_client_is_404(self, config):
        s3 = boto3.client("s3", region_name="ap-south-1")
        with Stubber(s3) as stub, use_s3(s3):
            stub.add_client_error(
                "get_object",
                service_error_code="NoSuchKey",
                http_status_code=404,
                expected_params={"Bucket": AGENT_BUCKET, "Key": f"{PREFIX}builtin-gone.json"},
            )
            response = client.get("/projects/proj-1/agents/builtin-gone", headers=USER)
            stub.assert_no_pending_responses()

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# The built-in agent files shipped in packages/infra/src/prompts/builtin_agents
# ---------------------------------------------------------------------------


REPO = Path(__file__).resolve().parents[3]
SHIPPED_DIR = REPO / "packages/infra/src/prompts/builtin_agents"
SHIPPED_IDS = {
    "builtin-call-qa-reviewer",
    "builtin-document-reminder",
    "builtin-file-checker",
    "builtin-loan-assistant",
}
# AgentCore Gateway target name (McpStack gatewayTargetName) -> tool schema.
GATEWAY_TOOL_SCHEMAS = {
    "search": "packages/lambda/search-mcp/schema.json",
    "qa": "packages/lambda/qa-mcp/schema.json",
    "data": "packages/lambda/data-mcp/schema.json",
    "filecheck": "packages/lambda/file-check-mcp/schema.json",
}


def shipped_files():
    return sorted(SHIPPED_DIR.glob("*.json"))


def shipped(agent_id):
    return json.loads((SHIPPED_DIR / f"{agent_id}.json").read_text(encoding="utf-8"))


class TestShippedBuiltinAgents:
    def test_the_four_builtins_are_shipped(self):
        assert {path.stem for path in shipped_files()} == SHIPPED_IDS

    @pytest.mark.parametrize("path", shipped_files(), ids=lambda path: path.stem)
    def test_file_is_well_formed(self, path):
        raw = path.read_bytes()
        data = json.loads(raw)
        assert set(data) == {"agent_id", "name", "description", "content"}
        assert data["agent_id"] == path.stem
        parsed = agents_router._parse_builtin_agent(path.stem, raw)
        assert parsed is not None
        assert agents_router.BUILTIN_AGENT_ID_RE.fullmatch(path.stem)
        assert 0 < len(data["description"]) <= 200

    @pytest.mark.parametrize("path", shipped_files(), ids=lambda path: path.stem)
    def test_every_tool_named_in_a_prompt_exists_in_the_gateway(self, path):
        tools = {
            f"{target}___{tool['name']}"
            for target, schema in GATEWAY_TOOL_SCHEMAS.items()
            for tool in json.loads((REPO / schema).read_text(encoding="utf-8"))
        }
        named = set(re.findall(r"\b[a-z]+___[a-z_]+\b", shipped(path.stem)["content"]))
        assert named <= tools, named - tools

    def test_list_serves_the_shipped_files(self, config):
        objects = {f"{PREFIX}{path.name}": path.read_bytes() for path in shipped_files()}
        with (
            patch("app.cache.cached_query_agents", new=AsyncMock(return_value=[])),
            use_s3(FakeS3(objects)),
        ):
            response = client.get("/projects/proj-1/agents", headers=USER)

        assert [(item["agent_id"], item["name"], item["builtin"]) for item in response.json()] == [
            ("builtin-call-qa-reviewer", "Call QA Reviewer", True),
            ("builtin-document-reminder", "Document Reminder Writer", True),
            ("builtin-file-checker", "File Checker", True),
            ("builtin-loan-assistant", "Loan Assistant", True),
        ]

    def test_file_checker_leaves_the_verdict_to_the_tool(self):
        content = shipped("builtin-file-checker")["content"]
        assert "filecheck___run_file_check" in content
        assert "Only the tool decides a verdict" in content
        assert "`missing_items`, verbatim and in the same order" in content

    def test_reminder_lists_only_the_tools_missing_items(self):
        content = shipped("builtin-document-reminder")["content"]
        assert "filecheck___run_file_check" in content
        # Same wording rules as the web app's "Draft reminder"; never adds, drops or invents a document.
        assert "never add, drop or invent" in content
        assert 'the same rules as the web app\'s "Draft reminder"' in content
        for template in ("T1", "T2", "T3", "T5", "T6", "T7"):
            for lang in ("EN", "HI", "MR"):
                assert f"- {template}-{lang}: " in content  # SMS version
        assert "{{" not in content  # every template variable is a named placeholder

    def test_call_qa_marks_compliance_items(self):
        content = shipped("builtin-call-qa-reviewer")["content"]
        assert content.count("(confirm with compliance)") >= 5
        for hard_fail in ("H1", "H2", "H3", "H4", "H5"):
            assert f"| {hard_fail} |" in content

    def test_varunika_loans_is_grounded_in_its_facts_and_guardrails(self):
        content = shipped("builtin-loan-assistant")["content"]
        facts = content.split('<FACTS version="2026-09-27" source="https://www.varunikaloans.example">\n', 1)[1]
        facts = json.loads(facts.rsplit("\n</FACTS>", 1)[0])
        assert {loan["title"] for loan in facts["loans"]} == {
            "Personal Loan",
            "Business Loan",
            "Home Loan",
            "Loan Against Property",
            "Education Loan",
            "Car Loan",
        }
        assert '"I authorize Varunika Loans to contact me via Call/SMS/WhatsApp."' in content
        assert "Never promise approval, a sanction, a rate, an amount, a timeline" in content
        assert "Never ask for or accept PAN, Aadhaar, bank account or card numbers, OTPs, PINs, passwords" in content
        assert "Hand-off to a human advisor" in content
        # Customer-facing: it must not read the workspace's (other customers') files.
        assert "never reveal anything from them" in content
