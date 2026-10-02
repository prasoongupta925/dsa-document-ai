"""Graph-disabled mode of the graph builder (index.py). No AWS calls.

The lean build has no graph database and runs this Lambda with
GRAPH_DISABLED=true. It must return at once, without any Bedrock (entity
normalization), LanceDB (graph keywords), S3, DynamoDB or GraphService call,
and with the keys the next states read: SendGraphBatches (ItemsPath
$.graph_batches, ItemSelector $.s3_bucket), FinalizeGraph (workflow_id and the
counts) and the backend graph rebuild (graph_batches, s3_bucket).

Run (from this folder): python -m pytest -q
"""

import importlib.util
import os
import socket
import sys

import pytest

os.environ.setdefault("BACKEND_TABLE_NAME", "test-table")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
os.environ.setdefault("AWS_REGION", "ap-south-1")
os.environ.pop("GRAPH_DISABLED", None)

HERE = os.path.dirname(os.path.abspath(__file__))
FUNCTIONS = os.path.abspath(os.path.join(HERE, "..", ".."))
for _path in (HERE, FUNCTIONS):  # normalizer, and the shared layer (shared.ddb_client)
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gb = _load("graph_builder_index", os.path.join(HERE, "index.py"))
fin = _load("graph_builder_finalizer_index", os.path.join(HERE, "..", "graph-builder-finalizer", "index.py"))

FILE_URI = "s3://docs-bucket/projects/proj_1/documents/doc_1/statement.pdf"
# BuildKnowledgeGraph input: the workflow state after ProcessSegmentsInParallel
EVENT = {
    "workflow_id": "wf_1",
    "document_id": "doc_1",
    "project_id": "proj_1",
    "file_uri": FILE_URI,
    "file_type": "application/pdf",
    "segment_count": 20,
    "language": "en",
    "document_prompt": "",
    "segment_ids": list(range(20)),
    "is_reanalysis": False,
}
DISABLED_RESULT = {
    "workflow_id": "wf_1",
    "document_id": "doc_1",
    "project_id": "proj_1",
    "file_uri": FILE_URI,
    "file_type": "application/pdf",
    "segment_count": 20,
    "s3_bucket": "",
    "graph_batches": [],
    "entity_count": 0,
    "relationship_count": 0,
}


def _fail(*_args, **_kwargs):
    raise AssertionError("graph disabled: no AWS, Bedrock or LanceDB call allowed")


@pytest.fixture
def no_calls(monkeypatch):
    """GraphService and LanceDB configured as in the lean stack; every way out fails the test."""
    monkeypatch.setattr(gb, "GRAPH_SERVICE_FUNCTION_NAME", "idp-v2-graph-service")
    monkeypatch.setattr(gb, "LANCEDB_FUNCTION_NAME", "idp-v2-lancedb-service")
    for name in (
        "get_lambda_client",
        "invoke_graph_service",
        "invoke_lancedb",
        "normalize_entities",
        "get_all_segment_analyses",
        "get_s3_client",
        "get_document",
        "get_project_language",
        "record_step_start",
        "record_step_error",
    ):
        monkeypatch.setattr(gb, name, _fail)
    monkeypatch.setattr(gb.boto3, "client", _fail)
    monkeypatch.setattr(gb.boto3, "resource", _fail)
    monkeypatch.setattr(socket, "create_connection", _fail)
    return monkeypatch


@pytest.mark.parametrize("flag", ["true", "TRUE", " True ", "1", "yes", "on"])
def test_disabled_returns_empty_batches_without_any_call(no_calls, flag):
    no_calls.setenv("GRAPH_DISABLED", flag)
    assert gb.handler(dict(EVENT), None) == DISABLED_RESULT


def test_no_graph_service_also_returns_empty_batches(no_calls):
    """Before, this skip returned no graph_batches and SendGraphBatches failed the document."""
    no_calls.delenv("GRAPH_DISABLED", raising=False)
    no_calls.setattr(gb, "GRAPH_SERVICE_FUNCTION_NAME", "")
    assert gb.handler(dict(EVENT), None) == DISABLED_RESULT


def test_disabled_logs_one_short_line_after_the_event(no_calls, capsys):
    no_calls.setenv("GRAPH_DISABLED", "true")
    gb.handler(dict(EVENT), None)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2 and lines[0].startswith("Event: ")
    assert lines[1] == "Graph disabled, skipping graph builder"


def test_disabled_chain_finalizes_the_step_completed_without_a_reason(no_calls):
    """BuildKnowledgeGraph -> SendGraphBatches (zero items, ResultPath null) -> FinalizeGraph."""
    no_calls.setenv("GRAPH_DISABLED", "true")
    built = gb.handler(dict(EVENT), None)
    assert built["graph_batches"] == []  # the Map runs no SendGraphBatch

    recorded = []
    no_calls.setattr(fin, "GRAPH_SERVICE_FUNCTION_NAME", "idp-v2-graph-service")
    no_calls.setattr(
        fin,
        "invoke_graph_service",
        lambda action, params: {"statusCode": 200, "graph_disabled": True, "clustered": False, "cluster_count": 0},
    )
    no_calls.setattr(fin, "record_step_complete", lambda *args, **kwargs: recorded.append((args, kwargs)))

    finalized = fin.handler(built, None)

    assert recorded == [(("wf_1", "graph_builder"), {"entity_count": 0, "relationship_count": 0})]
    assert finalized == {
        "workflow_id": "wf_1",
        "document_id": "doc_1",
        "project_id": "proj_1",
        "file_uri": FILE_URI,
        "file_type": "application/pdf",
        "segment_count": 20,
    }


def test_enabled_path_still_builds_the_graph(monkeypatch):
    """Flag off and GraphService set: the full build runs (every call faked, no network)."""
    calls = []
    puts = []

    class FakeS3:
        def put_object(self, **kwargs):
            puts.append((kwargs["Bucket"], kwargs["Key"]))

    monkeypatch.setenv("GRAPH_DISABLED", "false")
    monkeypatch.setattr(gb, "GRAPH_SERVICE_FUNCTION_NAME", "idp-v2-graph-service")
    monkeypatch.setattr(gb, "LANCEDB_FUNCTION_NAME", "idp-v2-lancedb-service")
    monkeypatch.setattr(gb, "record_step_start", lambda *args, **kwargs: calls.append("record_step_start"))
    monkeypatch.setattr(gb, "get_document", lambda *args: {"name": "statement.pdf"})
    monkeypatch.setattr(gb, "get_project_language", _fail)
    monkeypatch.setattr(
        gb, "invoke_graph_service", lambda action, params: calls.append(action) or {"statusCode": 200}
    )
    monkeypatch.setattr(
        gb,
        "get_all_segment_analyses",
        lambda uri, count: [
            {
                "segment_index": 0,
                "ai_analysis": [{"analysis_query": "Monthly salary credit"}],
                "graph_entities": [
                    {"name": "Acme Pvt Ltd", "mentioned_in": [{"segment_index": 0, "qa_index": 0, "context": "employer"}]}
                ],
            }
        ],
    )
    monkeypatch.setattr(gb, "invoke_lancedb", lambda action, params: calls.append(action) or {"keywords": []})
    monkeypatch.setattr(gb, "normalize_entities", lambda entities, existing: calls.append("normalize") or entities)
    monkeypatch.setattr(gb, "get_s3_client", lambda: FakeS3())
    monkeypatch.setattr(socket, "create_connection", _fail)

    result = gb.handler(dict(EVENT), None)

    assert calls == [
        "record_step_start",
        "add_segment_links",
        "get_graph_keywords",
        "normalize",
        "add_graph_keywords",
    ]
    assert [batch["action"] for batch in result["graph_batches"]] == ["add_analyses", "add_entities"]
    assert result["s3_bucket"] == "docs-bucket"
    assert puts == [
        ("docs-bucket", "projects/proj_1/documents/doc_1/graph_work/analyses.json"),
        ("docs-bucket", "projects/proj_1/documents/doc_1/graph_work/entities.json"),
    ]
    assert (result["entity_count"], result["relationship_count"]) == (1, 0)
