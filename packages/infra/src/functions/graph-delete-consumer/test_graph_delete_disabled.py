"""Graph-disabled mode of the graph-delete-consumer Lambda (index.py). No AWS calls.

The lean build has no Neptune: the backend and the retention sweeper still
queue graph deletes, and the consumer (GRAPH_DISABLED=true) must ack every SQS
record without a query and without re-queueing.

Run (from this folder): python -m pytest -q
"""

import importlib.util
import json
import os
import socket
import urllib.request

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
os.environ.setdefault("AWS_REGION", "ap-south-1")
os.environ.pop("NEPTUNE_ENDPOINT", None)
os.environ.pop("GRAPH_DISABLED", None)

HERE = os.path.dirname(os.path.abspath(__file__))


def _load():
    spec = importlib.util.spec_from_file_location("graph_delete_consumer_index", os.path.join(HERE, "index.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gdc = _load()

ENDPOINT = "idp-v2-neptune.cluster-x.ap-south-1.neptune.amazonaws.com"


def _record(body):
    return {"messageId": "m-1", "body": body if isinstance(body, str) else json.dumps(body)}


EVENT = {
    "Records": [
        _record({"project_id": "proj_1", "workflow_id": "wf_1"}),
        _record({"project_id": "proj_1", "workflow_id": "wf_2", "phase": "orphan_cleanup", "batch_size": 500}),
        _record("not json"),  # a malformed message is acked too, not retried to the DLQ
    ]
}


def _fail(*_args, **_kwargs):
    raise AssertionError("graph disabled: no query, re-queue or connection allowed")


@pytest.fixture
def no_network(monkeypatch):
    """Graph disabled by default (no NEPTUNE_ENDPOINT); every way out fails the test."""
    monkeypatch.delenv("GRAPH_DISABLED", raising=False)
    monkeypatch.setattr(gdc, "NEPTUNE_ENDPOINT", "")
    monkeypatch.setattr(gdc, "run_query", _fail)
    monkeypatch.setattr(gdc, "send_to_queue", _fail)
    monkeypatch.setattr(gdc, "get_sqs_client", _fail)
    monkeypatch.setattr(gdc, "get_session", _fail)
    monkeypatch.setattr(urllib.request, "urlopen", _fail)
    monkeypatch.setattr(socket, "create_connection", _fail)
    return monkeypatch


def test_disabled_acks_every_record_without_work(no_network):
    assert gdc.handler(EVENT, None) == {"batchItemFailures": []}


@pytest.mark.parametrize("flag", ["true", "TRUE", "1"])
def test_graph_disabled_flag_wins_over_a_configured_endpoint(no_network, flag):
    no_network.setattr(gdc, "NEPTUNE_ENDPOINT", ENDPOINT)
    no_network.setenv("GRAPH_DISABLED", flag)
    assert gdc.handler(EVENT, None) == {"batchItemFailures": []}


@pytest.mark.parametrize("event", [{}, {"Records": []}, {"Records": None}])
def test_disabled_handles_empty_events(no_network, event):
    assert gdc.handler(event, None) == {"batchItemFailures": []}


def test_disabled_logs_one_short_line(no_network, capsys):
    gdc.handler(EVENT, None)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert "3" in lines[0] and len(lines[0]) < 80
    assert "proj_1" not in lines[0]


def test_enabled_path_still_deletes_and_advances(monkeypatch):
    """Endpoint set and flag off: the real phase logic runs (query and SQS faked)."""
    queries, sent = [], []

    def fake_run_query(query, parameters=None):
        queries.append(parameters)
        return [{"deleted": 3}]

    monkeypatch.delenv("GRAPH_DISABLED", raising=False)
    monkeypatch.setattr(gdc, "NEPTUNE_ENDPOINT", ENDPOINT)
    monkeypatch.setattr(gdc, "run_query", fake_run_query)
    monkeypatch.setattr(gdc, "send_to_queue", sent.append)
    monkeypatch.setattr(urllib.request, "urlopen", _fail)

    gdc.handler({"Records": [_record({"project_id": "proj_1", "workflow_id": "wf_1"})]}, None)

    assert queries == [{"pid": "proj_1", "wid": "wf_1", "batch": 500}]
    assert sent == [{"project_id": "proj_1", "workflow_id": "wf_1", "phase": "segments", "batch_size": 500}]
