import contextlib
import json

import boto3
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from app.config import get_config
from app.ddb import query_documents
from app.ddb.workflows import query_workflows

router = APIRouter(prefix="/projects/{project_id}/graph", tags=["graph"])

_lambda_client = None


def get_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        from botocore.config import Config as BotoConfig

        config = get_config()
        _lambda_client = boto3.client(
            "lambda",
            region_name=config.aws_region,
            config=BotoConfig(read_timeout=900),
        )
    return _lambda_client


def invoke_graph_service(action: str, params: dict) -> dict:
    config = get_config()
    if not config.graph_service_function_name:
        raise HTTPException(status_code=404, detail="Graph service not configured")

    client = get_lambda_client()
    resp = client.invoke(
        FunctionName=config.graph_service_function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps({"action": action, "params": params}),
    )
    payload = json.loads(resp["Payload"].read())
    if resp.get("FunctionError") or payload.get("statusCode") != 200:
        raise HTTPException(
            status_code=500,
            detail=payload.get("error", "Graph service error"),
        )
    return payload


class GraphNode(BaseModel):
    id: str
    name: str
    label: str
    properties: dict


class GraphEdge(BaseModel):
    source: str
    target: str
    label: str
    properties: dict | None = None


class TagCloudItem(BaseModel):
    id: str
    name: str
    connections: int


class RebuildGraphResponse(BaseModel):
    status: str
    document_count: int


def _rebuild_graph_background(project_id: str) -> None:
    """Background task: clear project graph, then rebuild from S3 analysis data."""
    documents = query_documents(project_id)

    # 1. Clear existing graph per workflow
    for doc in documents:
        workflows = query_workflows(doc.data.document_id)
        for wf in workflows:
            wf_id = wf.SK.replace("WF#", "")
            with contextlib.suppress(Exception):
                invoke_graph_service(
                    "delete_by_workflow",
                    {"project_id": project_id, "workflow_id": wf_id},
                )

    # 2. Rebuild each completed document
    for doc in documents:
        workflows = query_workflows(doc.data.document_id)
        if not workflows:
            continue
        completed = [w for w in workflows if w.data.status in ("completed", "failed")]
        if not completed:
            continue
        wf = max(completed, key=lambda w: w.created_at)
        try:
            _invoke_graph_builder_and_send(project_id, doc.data.document_id, wf)
        except Exception as e:
            print(f"Failed to rebuild graph for {doc.data.document_id}: {e}")


@router.post("/rebuild")
def rebuild_graph(
    project_id: str,
    background_tasks: BackgroundTasks,
) -> RebuildGraphResponse:
    """Rebuild knowledge graph from existing S3 analysis data."""
    documents = query_documents(project_id)
    if not documents:
        raise HTTPException(status_code=404, detail="No documents found")

    background_tasks.add_task(_rebuild_graph_background, project_id)

    return RebuildGraphResponse(
        status="rebuilding",
        document_count=len(documents),
    )


def _invoke_graph_builder_and_send(project_id: str, document_id: str, wf) -> None:
    """Invoke graph-builder synchronously, then send batches to graph-service."""
    client = get_lambda_client()
    config = get_config()
    wf_id = wf.SK.replace("WF#", "")

    # 1. Invoke graph-builder synchronously
    payload = {
        "workflow_id": wf_id,
        "document_id": document_id,
        "project_id": project_id,
        "file_uri": wf.data.file_uri,
        "file_type": wf.data.file_type or "",
        "segment_count": wf.data.total_segments or 0,
        "language": wf.data.language or "en",
    }
    resp = client.invoke(
        FunctionName="idp-v2-graph-builder",
        InvocationType="RequestResponse",
        Payload=json.dumps(payload),
    )
    result = json.loads(resp["Payload"].read())
    if resp.get("FunctionError"):
        print(f"graph-builder error for {document_id}: {result}")
        return

    graph_batches = result.get("graph_batches", [])
    s3_bucket = result.get("s3_bucket", "")
    if not graph_batches or not s3_bucket:
        print(f"No graph batches for {document_id}")
        return

    # 2. Read S3 work files and send to graph-service
    s3 = boto3.client("s3", region_name=config.aws_region)
    for batch_info in graph_batches:
        action = batch_info["action"]
        item_key = batch_info["item_key"]
        s3_key = batch_info["s3_key"]
        batch_size = batch_info.get("batch_size", 100)
        extra_params = batch_info.get("extra_params", {})

        obj = s3.get_object(Bucket=s3_bucket, Key=s3_key)
        items = json.loads(obj["Body"].read())

        for i in range(0, len(items), batch_size):
            batch = items[i : i + batch_size]
            invoke_graph_service(action, {**extra_params, item_key: batch})

    print(f"Rebuild complete for {document_id}: {len(graph_batches)} batches")


def _rebuild_document_graph_background(project_id: str, document_id: str) -> None:
    """Background task: clear graph for a document, rebuild from S3 analysis data."""
    workflows = query_workflows(document_id)

    # 1. Clear existing graph per workflow
    for wf in workflows:
        wf_id = wf.SK.replace("WF#", "")
        with contextlib.suppress(Exception):
            invoke_graph_service(
                "delete_by_workflow",
                {"project_id": project_id, "workflow_id": wf_id},
            )

    # 2. Pick latest completed workflow and rebuild
    completed = [w for w in workflows if w.data.status in ("completed", "failed")]
    if not completed:
        return
    wf = max(completed, key=lambda w: w.created_at)
    _invoke_graph_builder_and_send(project_id, document_id, wf)


@router.post("/documents/{document_id}/rebuild")
def rebuild_document_graph(
    project_id: str,
    document_id: str,
    background_tasks: BackgroundTasks,
) -> RebuildGraphResponse:
    """Rebuild knowledge graph for a single document from existing S3 analysis data."""
    workflows = query_workflows(document_id)
    completed = [w for w in workflows if w.data.status in ("completed", "failed")]
    if not completed:
        raise HTTPException(status_code=404, detail="No completed workflow found")

    background_tasks.add_task(_rebuild_document_graph_background, project_id, document_id)

    return RebuildGraphResponse(
        status="rebuilding",
        document_count=1,
    )


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    tagcloud: list[TagCloudItem] | None = None
    total_segments: int | None = None
    total_entities: int | None = None
    mode: str | None = None
    focus_page: int | None = None
    from_page: int | None = None
    to_page: int | None = None


@router.get("")
def get_project_graph(
    project_id: str,
    search: str | None = Query(default=None),
    shared_only: bool = Query(default=False),
) -> GraphResponse:
    """Get project-level entity graph for visualization."""
    params: dict = {"project_id": project_id}
    if search:
        params["search"] = search
    if shared_only:
        params["shared_only"] = True
    result = invoke_graph_service(
        "get_entity_graph",
        params,
    )
    return GraphResponse(
        nodes=[GraphNode(**n) for n in result.get("nodes", [])],
        edges=[GraphEdge(**e) for e in result.get("edges", [])],
        tagcloud=[TagCloudItem(**t) for t in result.get("tagcloud", [])] if result.get("tagcloud") else None,
        total_entities=result.get("total_entities"),
    )


@router.get("/documents/{document_id}")
def get_document_graph(
    project_id: str,
    document_id: str,
    from_page: int | None = Query(default=None),
    to_page: int | None = Query(default=None),
    page: int | None = Query(default=None),
    search: str | None = Query(default=None),
) -> GraphResponse:
    """Get document-level graph (segments + entities) for visualization."""
    params: dict = {
        "project_id": project_id,
        "document_id": document_id,
    }
    if from_page is not None and to_page is not None:
        params["from_page"] = from_page
        params["to_page"] = to_page
    if page is not None:
        params["page"] = page
    if search:
        params["search"] = search

    result = invoke_graph_service("get_document_graph", params)
    return GraphResponse(
        nodes=[GraphNode(**n) for n in result.get("nodes", [])],
        edges=[GraphEdge(**e) for e in result.get("edges", [])],
        total_segments=result.get("total_segments"),
        mode=result.get("mode"),
        focus_page=result.get("focus_page"),
        from_page=result.get("from_page"),
        to_page=result.get("to_page"),
    )


class TagCloudResponse(BaseModel):
    tags: list[TagCloudItem]


@router.get("/documents/{document_id}/tagcloud")
def get_document_tagcloud(
    project_id: str,
    document_id: str,
) -> TagCloudResponse:
    """Get lightweight tag cloud data for a document (entity names + connection counts)."""
    result = invoke_graph_service(
        "get_document_tagcloud",
        {"project_id": project_id, "document_id": document_id},
    )
    return TagCloudResponse(
        tags=[TagCloudItem(**t) for t in result.get("tags", [])],
    )


@router.get("/documents/{document_id}/expand/{entity_type}")
def expand_entity_cluster(
    project_id: str,
    document_id: str,
    entity_type: str,
) -> GraphResponse:
    """Expand a clustered entity type into individual entities."""
    result = invoke_graph_service(
        "expand_entity_cluster",
        {
            "project_id": project_id,
            "document_id": document_id,
            "entity_type": entity_type,
        },
    )
    return GraphResponse(
        nodes=[GraphNode(**n) for n in result.get("nodes", [])],
        edges=[GraphEdge(**e) for e in result.get("edges", [])],
    )


@router.get("/documents/{document_id}/expand-all")
def expand_all_clusters(
    project_id: str,
    document_id: str,
) -> GraphResponse:
    """Expand all clustered entity types into individual entities at once."""
    result = invoke_graph_service(
        "expand_all_clusters",
        {
            "project_id": project_id,
            "document_id": document_id,
        },
    )
    return GraphResponse(
        nodes=[GraphNode(**n) for n in result.get("nodes", [])],
        edges=[GraphEdge(**e) for e in result.get("edges", [])],
    )


class EntityDetail(BaseModel):
    id: str
    name: str
    description: str | None = None
    aliases: list[str] | None = None
    neighbors: list[GraphNode] = []


@router.get("/entities/{entity_name}")
def get_entity_detail(
    project_id: str,
    entity_name: str,
    depth: int = Query(default=2, le=5),
) -> EntityDetail:
    """Get entity details with neighbors."""
    # Search for entity by name matching via graph traversal
    search_result = invoke_graph_service(
        "search_graph",
        {"project_id": project_id, "query": entity_name, "entity_limit": 1, "segment_limit": 0},
    )
    entities = search_result.get("entities", [])
    if not entities:
        raise HTTPException(status_code=404, detail="Entity not found")

    entity = entities[0]

    # Get neighbors via traversal
    traverse_result = invoke_graph_service(
        "traverse",
        {"start_id": entity["id"], "depth": depth, "limit": 50},
    )
    neighbors = [
        GraphNode(
            id=n.get("id", ""),
            name=n.get("props", {}).get("name", n.get("id", "")),
            label=(n.get("labels", ["unknown"])[0] if n.get("labels") else "unknown").lower(),
            properties=n.get("props", {}),
        )
        for n in traverse_result.get("nodes", [])
    ]

    return EntityDetail(
        id=entity["id"],
        name=entity["name"],
        description=entity.get("description"),
        aliases=entity.get("aliases"),
        neighbors=neighbors,
    )
