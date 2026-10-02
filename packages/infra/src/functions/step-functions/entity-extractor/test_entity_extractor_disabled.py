"""Graph-disabled mode of the entity extractor (index.py). No AWS calls.

The lean build has no graph database and runs this Lambda with
GRAPH_DISABLED=true. The per-page ExtractEntities state (its output is
discarded) and the QA regenerator's async re-extraction must then return at
once, without reading S3, calling the model or writing graph_entities (only the
graph builder reads them). Test mode, run by hand for prompt tuning, still
extracts.

Run (from this folder): python -m pytest -q
"""

import copy
import importlib.util
import os
import socket
import sys

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
os.environ.setdefault("AWS_REGION", "ap-south-1")
os.environ.pop("GRAPH_DISABLED", None)

HERE = os.path.dirname(os.path.abspath(__file__))
FUNCTIONS = os.path.abspath(os.path.join(HERE, "..", ".."))
for _path in (HERE, FUNCTIONS):  # extractor, and the shared layer (shared.s3_analysis)
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _load():
    spec = importlib.util.spec_from_file_location("entity_extractor_index", os.path.join(HERE, "index.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ee = _load()

FILE_URI = "s3://docs-bucket/projects/proj_1/documents/doc_1/statement.pdf"
# ExtractEntities input: the AnalyzeSegment output
EVENT = {
    "workflow_id": "wf_1",
    "document_id": "doc_1",
    "project_id": "proj_1",
    "segment_index": 3,
    "file_uri": FILE_URI,
    "file_type": "application/pdf",
    "language": "en",
    "status": "analyzed",
    "analysis_count": 1,
}
SEGMENT = {"segment_index": 3, "ai_analysis": [{"content": "Salary credited by Acme Pvt Ltd"}]}
ENTITIES = [{"name": "Acme Pvt Ltd", "mentioned_in": [{"segment_index": 0, "qa_index": 0, "context": "employer"}]}]


def _fail(*_args, **_kwargs):
    raise AssertionError("graph disabled: no S3 read, model call or S3 write allowed")


@pytest.fixture
def no_calls(monkeypatch):
    for name in ("get_segment_analysis", "extract_entities", "update_segment_analysis"):
        monkeypatch.setattr(ee, name, _fail)
    monkeypatch.setattr(socket, "create_connection", _fail)
    return monkeypatch


@pytest.mark.parametrize("flag", ["true", "TRUE", " True ", "1", "yes", "on"])
def test_disabled_skips_without_any_call(no_calls, flag):
    no_calls.setenv("GRAPH_DISABLED", flag)
    assert ee.handler(dict(EVENT), None) == {
        "workflow_id": "wf_1",
        "segment_index": 3,
        "status": "skipped",
        "entity_count": 0,
    }


def test_disabled_skips_the_qa_regenerator_call(no_calls):
    """qa-regenerator invokes it asynchronously with only these four keys."""
    no_calls.setenv("GRAPH_DISABLED", "true")
    event = {"workflow_id": "wf_1", "file_uri": FILE_URI, "segment_index": 7, "language": "hi"}
    assert ee.handler(event, None)["status"] == "skipped"


def test_disabled_skip_keeps_a_dict_segment_index_normalized(no_calls):
    no_calls.setenv("GRAPH_DISABLED", "true")
    result = ee.handler({**EVENT, "segment_index": {"segment_index": 5}}, None)
    assert result["segment_index"] == 5


def test_test_mode_still_extracts_when_disabled(monkeypatch):
    monkeypatch.setenv("GRAPH_DISABLED", "true")
    monkeypatch.setattr(ee, "get_segment_analysis", lambda uri, index: copy.deepcopy(SEGMENT))
    monkeypatch.setattr(ee, "extract_entities", lambda data, index, language: copy.deepcopy(ENTITIES))
    monkeypatch.setattr(ee, "update_segment_analysis", _fail)

    result = ee.handler({**EVENT, "mode": "test"}, None)

    assert (result["status"], result["entity_count"]) == ("test", 1)
    assert result["entities"][0]["mentioned_in"][0]["segment_index"] == 3


@pytest.mark.parametrize("flag", [None, "", "false", "0"])
def test_enabled_extracts_and_saves_graph_entities(monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv("GRAPH_DISABLED", raising=False)
    else:
        monkeypatch.setenv("GRAPH_DISABLED", flag)
    saved = []
    monkeypatch.setattr(ee, "get_segment_analysis", lambda uri, index: copy.deepcopy(SEGMENT))
    monkeypatch.setattr(ee, "extract_entities", lambda data, index, language: copy.deepcopy(ENTITIES))
    monkeypatch.setattr(
        ee, "update_segment_analysis", lambda uri, index, **fields: saved.append((uri, index, fields))
    )

    result = ee.handler(dict(EVENT), None)

    assert result == {"workflow_id": "wf_1", "segment_index": 3, "status": "completed", "entity_count": 1}
    assert [(uri, index, list(fields)) for uri, index, fields in saved] == [(FILE_URI, 3, ["graph_entities"])]
