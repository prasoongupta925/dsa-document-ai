"""Tests for the workflow finalizer's CRM webhook hook (index.py). No AWS calls.

update_workflow_status, the META read and the Lambda client are fakes.

Run (from this folder): python -m pytest -q
"""

import importlib.util
import json
import os
import sys

import pytest

os.environ.setdefault("BACKEND_TABLE_NAME", "test-table")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
os.environ.setdefault("AWS_REGION", "ap-south-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

HERE = os.path.dirname(os.path.abspath(__file__))
FUNCTIONS = os.path.abspath(os.path.join(HERE, "..", ".."))
if FUNCTIONS not in sys.path:
    sys.path.insert(0, FUNCTIONS)  # the shared layer: shared.ddb_client


def _load():
    spec = importlib.util.spec_from_file_location("workflow_finalizer_index", os.path.join(HERE, "index.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fin = _load()

WEBHOOK_ARN = "arn:aws:lambda:ap-south-1:111111111111:function:idp-v2-webhook-delivery"
EVENT = {
    "workflow_id": "wf_1",
    "document_id": "doc_1",
    "project_id": "proj_1",
    "file_type": "application/pdf",
}


class FakeTable:
    def __init__(self, item=None, error=None):
        self.item = item
        self.error = error
        self.gets = []

    def get_item(self, **kwargs):
        self.gets.append(kwargs)
        if self.error:
            raise self.error
        return {"Item": dict(self.item)} if self.item is not None else {}


class FakeLambda:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"StatusCode": 202}


@pytest.fixture
def env(monkeypatch):
    state = {"statuses": [], "table": FakeTable({"webhook_enabled": True, "webhook_url": "https://crm.example.com/h"})}
    state["lambda"] = FakeLambda()

    def fake_update(document_id, workflow_id, status, entity_type="DOC"):
        state["statuses"].append((document_id, workflow_id, status, entity_type))
        return {"data": {"project_id": "proj_from_workflow", "status": status}}

    monkeypatch.setenv("WEBHOOK_FUNCTION_NAME", WEBHOOK_ARN)
    monkeypatch.setattr(fin, "update_workflow_status", fake_update)
    monkeypatch.setattr(fin, "get_table", lambda: state["table"])
    monkeypatch.setattr(fin, "_lambda_client", state["lambda"])
    return state


def test_enabled_webhook_is_queued_asynchronously(env):
    result = fin.handler(dict(EVENT), None)

    assert result == {"workflow_id": "wf_1", "status": "completed"}
    assert env["statuses"] == [("doc_1", "wf_1", "completed", "DOC")]
    (get,) = env["table"].gets
    assert get["Key"] == {"PK": "PROJ#proj_1", "SK": "META"}
    assert "webhook_secret" not in get["ProjectionExpression"]
    (call,) = env["lambda"].calls
    assert call["FunctionName"] == WEBHOOK_ARN
    assert call["InvocationType"] == "Event"
    assert json.loads(call["Payload"]) == {
        "project_id": "proj_1",
        "document_id": "doc_1",
        "event": "file_check.completed",
    }


@pytest.mark.parametrize(
    "item",
    [
        {"webhook_enabled": False, "webhook_url": "https://crm.example.com/h"},
        {"webhook_url": "https://crm.example.com/h"},
        {"webhook_enabled": True},
        {},
        None,
    ],
)
def test_disabled_webhook_is_not_invoked(env, item):
    env["table"].item = item

    assert fin.handler(dict(EVENT), None)["status"] == "completed"
    assert env["lambda"].calls == []


def test_without_function_name_nothing_is_read_or_invoked(env, monkeypatch):
    monkeypatch.delenv("WEBHOOK_FUNCTION_NAME")

    assert fin.handler(dict(EVENT), None)["status"] == "completed"
    assert env["table"].gets == []
    assert env["lambda"].calls == []


def test_invoke_failure_does_not_fail_the_workflow(env, monkeypatch, capsys):
    env["lambda"].error = RuntimeError("TooManyRequestsException")

    assert fin.handler(dict(EVENT), None) == {"workflow_id": "wf_1", "status": "completed"}
    assert "Webhook not queued for project proj_1: RuntimeError" in capsys.readouterr().out


def test_meta_read_failure_does_not_fail_the_workflow(env):
    env["table"].error = RuntimeError("AccessDeniedException")

    assert fin.handler(dict(EVENT), None)["status"] == "completed"
    assert env["lambda"].calls == []


def test_project_id_falls_back_to_the_workflow_record(env):
    event = {k: v for k, v in EVENT.items() if k != "project_id"}

    fin.handler(event, None)

    assert env["table"].gets[0]["Key"] == {"PK": "PROJ#proj_from_workflow", "SK": "META"}
    assert json.loads(env["lambda"].calls[0]["Payload"])["project_id"] == "proj_from_workflow"


@pytest.mark.parametrize("updated", [{}, None, {"data": None}, {"data": "x"}])
def test_odd_workflow_record_without_project_id_skips_the_webhook(env, monkeypatch, updated):
    monkeypatch.setattr(fin, "update_workflow_status", lambda *args, **kwargs: updated)
    event = {k: v for k, v in EVENT.items() if k != "project_id"}

    assert fin.handler(event, None) == {"workflow_id": "wf_1", "status": "completed"}
    assert env["table"].gets == []
    assert env["lambda"].calls == []


def test_notify_webhook_never_raises_on_client_creation(env, monkeypatch):
    def broken_client():
        raise RuntimeError("NoRegionError")

    monkeypatch.setattr(fin, "_get_lambda_client", broken_client)

    assert fin.notify_webhook("proj_1", "doc_1") is False
