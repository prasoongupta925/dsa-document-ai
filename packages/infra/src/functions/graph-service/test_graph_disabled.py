"""Graph-disabled mode of the graph-service Lambda (index.py). No AWS calls.

The lean build has no Neptune: the Lambda runs with GRAPH_DISABLED=true and
must answer every action with statusCode 200 and the empty result its callers
read (graph-builder, graph-batch-sender, graph-builder-finalizer,
reanalysis-prep, qa-regenerator, the backend graph router, search-mcp),
without opening a connection.

Run (from this folder): python -m pytest -q
"""

import importlib.util
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
    spec = importlib.util.spec_from_file_location("graph_service_index", os.path.join(HERE, "index.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gs = _load()

PROJECT = {"project_id": "proj_1"}
WORKFLOW = {"project_id": "proj_1", "workflow_id": "wf_1"}

# action -> (params as a caller sends them, the keys it must get back besides
# statusCode/success/graph_disabled)
EXPECTED = {
    # Write (graph-builder, graph-batch-sender, qa-regenerator, backend rebuild)
    "add_segment_links": (
        {**WORKFLOW, "document_id": "doc_1", "file_name": "a.pdf", "file_type": "application/pdf",
         "segment_count": 700, "start_index": 500, "end_index": 700},
        {"document_id": "doc_1", "segment_range": "500-700"},
    ),
    "add_analyses": (
        {**WORKFLOW, "document_id": "doc_1",
         "analyses": [{"segment_index": 0, "qa_index": 0, "question": "Q"}]},
        {"created": 0},
    ),
    "add_entities": (
        {**PROJECT, "entities": [{"name": "Ramesh Kumar", "mentioned_in": [{"segment_index": 0}]}]},
        {"created": 0},
    ),
    "add_relationships": (
        {**PROJECT, "relationships": [{"source": "a", "target": "b"}]},
        {"created": 0},
    ),
    "build_clusters": (
        {**PROJECT, "document_id": "doc_1"},
        {"clustered": False, "entity_count": 0, "cluster_count": 0},
    ),
    "link_documents": ({"document_id_1": "doc_1", "document_id_2": "doc_2", "reason": "r"}, {}),
    "unlink_documents": ({"document_id_1": "doc_1", "document_id_2": "doc_2"}, {}),
    "get_linked_documents": ({**PROJECT, "document_id": "doc_1"}, {"links": []}),
    "delete_analysis": ({**PROJECT, "analysis_id": "wf_1_0003_01"}, {"analysis_id": "wf_1_0003_01"}),
    "delete_by_workflow": (WORKFLOW, {}),
    "clear_all": ({"batch_size": 500}, {"deleted_edges": 0, "deleted_nodes": 0}),
    # search-mcp graph_keyword
    "raw_query": (
        {"query": "UNWIND $eids AS eid MATCH (e:Entity {`~id`: eid}) RETURN e", "parameters": {"eids": ["x"]}},
        {"results": []},
    ),
    # Read (search-mcp graph_traverse, backend graph router)
    "search_graph": (
        {**PROJECT, "query": "loan", "document_id": "doc_1", "segment_limit": 30, "qa_ids": ["wf_1_0001_00"]},
        {"entities": [], "segments": []},
    ),
    "traverse": ({"start_id": "e1", "depth": 2, "limit": 50}, {"nodes": []}),
    "find_related_segments": ({"entity_ids": ["e1"], "limit": 20}, {"segments": []}),
    "get_entity_graph": (
        {**PROJECT, "search": "kumar"},
        {"nodes": [], "edges": [], "tagcloud": [], "total_entities": 0},
    ),
    "get_document_graph": (
        {**PROJECT, "document_id": "doc_1"},
        {"nodes": [], "edges": [], "clustered": False, "total_segments": 0},
    ),
    "expand_entity_cluster": (
        {**PROJECT, "document_id": "doc_1", "entity_type": "PERSON"},
        {"nodes": [], "edges": [], "entity_type": "PERSON"},
    ),
    "expand_all_clusters": ({**PROJECT, "document_id": "doc_1"}, {"nodes": [], "edges": []}),
    "get_document_tagcloud": ({**PROJECT, "document_id": "doc_1"}, {"tags": []}),
}


def _fail(*_args, **_kwargs):
    raise AssertionError("graph disabled: no query, connection or AWS session allowed")


@pytest.fixture
def no_network(monkeypatch):
    """Graph disabled by default (no NEPTUNE_ENDPOINT); every way out fails the test."""
    monkeypatch.delenv("GRAPH_DISABLED", raising=False)
    monkeypatch.setattr(gs, "NEPTUNE_ENDPOINT", "")
    monkeypatch.setattr(gs, "run_query", _fail)
    monkeypatch.setattr(gs, "get_session", _fail)
    monkeypatch.setattr(urllib.request, "urlopen", _fail)
    monkeypatch.setattr(socket, "create_connection", _fail)
    return monkeypatch


def _call(action, params):
    return gs.handler({"action": action, "params": params}, None)


def test_every_action_has_an_expected_empty_shape():
    assert set(EXPECTED) == set(gs.ACTIONS)


@pytest.mark.parametrize("action", sorted(EXPECTED))
def test_disabled_action_returns_200_and_empty_shape(no_network, action):
    params, extra = EXPECTED[action]
    assert _call(action, params) == {"statusCode": 200, "success": True, "graph_disabled": True, **extra}


@pytest.mark.parametrize("flag", ["true", "TRUE", " True ", "1", "yes", "on"])
def test_graph_disabled_flag_wins_over_a_configured_endpoint(no_network, flag):
    no_network.setattr(gs, "NEPTUNE_ENDPOINT", "idp-v2-neptune.cluster-x.ap-south-1.neptune.amazonaws.com")
    no_network.setenv("GRAPH_DISABLED", flag)
    result = _call("search_graph", {**PROJECT, "qa_ids": ["wf_1_0001_00"]})
    assert result == {"statusCode": 200, "success": True, "graph_disabled": True, "entities": [], "segments": []}


def test_empty_endpoint_disables_even_when_flag_is_false(no_network):
    no_network.setenv("GRAPH_DISABLED", "false")
    assert _call("get_document_tagcloud", {**PROJECT, "document_id": "doc_1"})["graph_disabled"] is True


@pytest.mark.parametrize(
    "params, extra",
    [
        ({**PROJECT, "document_id": "doc_1", "search": "kumar"}, {"mode": "search"}),
        ({**PROJECT, "document_id": "doc_1", "page": 4}, {"mode": "page", "focus_page": 4}),
        (
            {**PROJECT, "document_id": "doc_1", "from_page": 0, "to_page": 50},
            {"mode": "range", "from_page": 0, "to_page": 50},
        ),
    ],
)
def test_disabled_document_graph_keeps_the_requested_mode(no_network, params, extra):
    """The backend graph router passes mode/focus_page/from_page/to_page through."""
    result = _call("get_document_graph", params)
    assert result == {
        "statusCode": 200, "success": True, "graph_disabled": True,
        "nodes": [], "edges": [], "clustered": False, "total_segments": 0, **extra,
    }


@pytest.mark.parametrize("event", [{"action": "delete_analysis"}, {"action": "delete_analysis", "params": None}])
def test_disabled_tolerates_missing_params(no_network, event):
    assert gs.handler(event, None) == {
        "statusCode": 200, "success": True, "graph_disabled": True, "analysis_id": "",
    }


@pytest.mark.parametrize("action", ["drop_everything", None, ""])
def test_unknown_action_is_still_400_when_disabled(no_network, action):
    assert gs.handler({"action": action, "params": {}}, None) == {
        "statusCode": 400,
        "error": f"Unknown action: {action}",
    }


def test_disabled_logs_one_short_line_without_the_payload(no_network, capsys):
    params, _ = EXPECTED["add_entities"]
    _call("add_entities", params)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert "add_entities" in lines[0] and len(lines[0]) < 80
    assert "Ramesh" not in lines[0]


def test_enabled_path_still_queries_neptune(monkeypatch):
    """Endpoint set and flag off: the real action runs (run_query faked, no network)."""
    calls = []

    def fake_run_query(query, parameters=None, _retries=5):
        calls.append((query, parameters))
        return []

    monkeypatch.delenv("GRAPH_DISABLED", raising=False)
    monkeypatch.setattr(gs, "NEPTUNE_ENDPOINT", "idp-v2-neptune.cluster-x.ap-south-1.neptune.amazonaws.com")
    monkeypatch.setattr(gs, "run_query", fake_run_query)
    monkeypatch.setattr(urllib.request, "urlopen", _fail)

    result = _call("get_linked_documents", {**PROJECT, "document_id": "doc_1"})

    assert result == {"statusCode": 200, "success": True, "links": []}
    assert len(calls) == 1 and calls[0][1] == {"did": "doc_1"}


def test_run_query_without_endpoint_never_connects(monkeypatch):
    """Belt and braces: the query helper itself refuses to connect with no endpoint."""
    monkeypatch.setattr(gs, "NEPTUNE_ENDPOINT", "")
    monkeypatch.setattr(urllib.request, "urlopen", _fail)
    with pytest.raises(RuntimeError, match="NEPTUNE_ENDPOINT"):
        gs.run_query("MATCH (n) RETURN n")
