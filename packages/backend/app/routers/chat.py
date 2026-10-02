import json
import time
from datetime import datetime

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from app.cache import CacheKey, invalidate
from app.config import get_config
from app.duckdb import Session, get_duckdb_connection
from app.message import ContentItem, parse_content_items
from app.s3 import delete_s3_prefix, folder_presigner, get_s3_client
from app.safe_ids import safe_segment

router = APIRouter(prefix="/chat", tags=["chat"])

# SSM parameter holding the chat model catalog JSON. CDK (AgentStack) creates it
# from packages/infra/src/chat-models.json (AWS-sold models only; the first
# entry is the default). The agent validates model_id against the same catalog.
MODEL_CATALOG_SSM_KEY = "/idp-v2/chat/models"
_MODEL_CATALOG_TTL_SECONDS = 60

# Built-in fallback used when the SSM parameter is absent or unreadable. Keep it
# identical to packages/infra/src/chat-models.json (GLM-5 first = default).
_DEFAULT_MODEL_CATALOG: list[dict] = [
    {
        "value": "zai.glm-5",
        "label": "GLM-5",
        "description": "Z.ai GLM-5, default chat model (AWS-sold, in-region)",
        "contextWindow": "200K tokens",
        "inputPrice": "$1.20 / 1M",
        "outputPrice": "$3.84 / 1M",
        "metrics": {"intelligence": 8, "speed": 6, "context": 7, "cost": 6},
        "supportsReasoning": False,
    },
    {
        "value": "global.amazon.nova-2-lite-v1:0",
        "label": "Nova 2 Lite",
        "description": "Amazon first-party model (global inference)",
        "contextWindow": "1M tokens",
        "inputPrice": "$0.35 / 1M",
        "outputPrice": "$2.95 / 1M",
        "metrics": {"intelligence": 6, "speed": 9, "context": 10, "cost": 9},
        "supportsReasoning": False,
    },
    {
        "value": "deepseek.v3.2",
        "label": "DeepSeek V3.2",
        "description": "DeepSeek V3.2 (AWS-sold, in-region)",
        "contextWindow": "128K tokens",
        "inputPrice": "$0.74 / 1M",
        "outputPrice": "$2.22 / 1M",
        "metrics": {"intelligence": 7, "speed": 7, "context": 6, "cost": 8},
        "supportsReasoning": False,
    },
    {
        "value": "moonshotai.kimi-k2.5",
        "label": "Kimi K2.5",
        "description": "Moonshot AI Kimi K2.5 (AWS-sold, in-region)",
        "contextWindow": "256K tokens",
        "inputPrice": "$0.72 / 1M",
        "outputPrice": "$3.60 / 1M",
        "metrics": {"intelligence": 8, "speed": 6, "context": 8, "cost": 7},
        "supportsReasoning": False,
    },
]

# (models, fetched_at monotonic) - refreshed lazily once the TTL elapses.
_model_catalog_cache: tuple[list["ModelCatalogEntry"], float] | None = None


class ModelMetrics(BaseModel):
    intelligence: int
    speed: int
    context: int
    cost: int


class ModelCatalogEntry(BaseModel):
    """One selectable chat model. Every field the frontend selector renders is
    required (metrics especially), so a malformed SSM entry is rejected rather
    than crashing the UI."""

    value: str
    label: str
    description: str
    contextWindow: str
    inputPrice: str
    outputPrice: str
    metrics: ModelMetrics
    supportsReasoning: bool = True


class ModelCatalogResponse(BaseModel):
    models: list[ModelCatalogEntry]


# Built-in fallback, validated once at import so a typo here fails fast in tests.
_DEFAULT_CATALOG_MODELS: list[ModelCatalogEntry] = [ModelCatalogEntry(**m) for m in _DEFAULT_MODEL_CATALOG]


def _load_model_catalog() -> list[ModelCatalogEntry]:
    """Read + validate the model catalog from SSM (cached), falling back to the
    built-in default on any read/parse/validation failure so a bad SSM value
    can never break the selector."""
    global _model_catalog_cache
    now = time.monotonic()
    if _model_catalog_cache is not None and now - _model_catalog_cache[1] < _MODEL_CATALOG_TTL_SECONDS:
        return _model_catalog_cache[0]

    config = get_config()
    models = _DEFAULT_CATALOG_MODELS
    try:
        ssm = boto3.client("ssm", region_name=config.aws_region)
        raw = ssm.get_parameter(Name=MODEL_CATALOG_SSM_KEY)["Parameter"]["Value"]
        parsed = json.loads(raw)
        # Validate every entry; an invalid or empty catalog raises and we keep
        # the default.
        validated = [ModelCatalogEntry.model_validate(item) for item in parsed]
        if validated:
            models = validated
    except (BotoCoreError, ClientError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        models = _DEFAULT_CATALOG_MODELS

    _model_catalog_cache = (models, now)
    return models


@router.get("/models")
async def get_model_catalog() -> ModelCatalogResponse:
    """Return the selectable chat model catalog (SSM-backed, cached)."""
    return ModelCatalogResponse(models=_load_model_catalog())


class ChatMessage(BaseModel):
    role: str
    content: list[ContentItem]
    created_at: datetime
    updated_at: datetime


class ChatHistoryResponse(BaseModel):
    session_id: str
    messages: list[ChatMessage]


class SessionListResponse(BaseModel):
    sessions: list[Session]
    next_cursor: str | None = None


@router.get("/projects/{project_id}/sessions")
async def get_project_sessions(
    project_id: str,
    x_user_id: str = Header(alias="x-user-id"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    after: str | None = Query(default=None, description="Filter sessions created after this ISO timestamp"),
) -> SessionListResponse:
    """Get sessions for a project from S3 using DuckDB."""
    from app.cache import cached_query_sessions

    safe_segment(x_user_id, "user id")
    safe_segment(project_id, "project id")
    sessions = await cached_query_sessions(x_user_id, project_id)

    if after:
        sessions = [s for s in sessions if s.created_at > after]

    if cursor:
        cursor_index = next((i for i, s in enumerate(sessions) if s.session_id == cursor), -1)
        if cursor_index >= 0:
            sessions = sessions[cursor_index + 1 :]

    has_more = len(sessions) > limit
    if has_more:
        sessions = sessions[:limit]

    next_cursor = sessions[-1].session_id if has_more and sessions else None

    return SessionListResponse(sessions=sessions, next_cursor=next_cursor)


@router.get("/projects/{project_id}/sessions/{session_id}")
def get_chat_history(
    project_id: str, session_id: str, x_user_id: str = Header(alias="x-user-id")
) -> ChatHistoryResponse:
    """Get chat history for a session from S3 using DuckDB."""
    for value, field in ((x_user_id, "user id"), (project_id, "project id"), (session_id, "session id")):
        safe_segment(value, field)
    config = get_config()
    bucket_name = config.session_storage_bucket_name

    if not bucket_name:
        raise HTTPException(status_code=500, detail="Session storage bucket not configured")

    session_prefix = f"sessions/{x_user_id}/{project_id}/session_{session_id}/"
    s3_path = f"s3://{bucket_name}/{session_prefix}agents/*/messages/message_*.json"
    # Attachments are stored under the session's own folder (session_workers
    # attachment-upload); a reference to anything else is not signed.
    presign = folder_presigner(bucket_name, session_prefix)

    conn = get_duckdb_connection()
    try:
        result = conn.execute(
            """
            SELECT
                message_id,
                message.role as role,
                message.content as content,
                created_at,
                updated_at
            FROM read_json_auto(?)
            WHERE message.role IN ('user', 'assistant')
            ORDER BY message_id
            """,
            [s3_path],
        ).fetchall()
    except Exception:
        return ChatHistoryResponse(session_id=session_id, messages=[])

    messages = []
    for row in result:
        role, content_items, created_at, updated_at = row[1], row[2], row[3], row[4]
        parsed_content = parse_content_items(content_items, presign)

        if parsed_content:
            messages.append(
                ChatMessage(
                    role=role,
                    content=parsed_content,
                    created_at=created_at,
                    updated_at=updated_at,
                )
            )

    return ChatHistoryResponse(session_id=session_id, messages=messages)


class UpdateSessionRequest(BaseModel):
    session_name: str


class DeleteSessionResponse(BaseModel):
    deleted_count: int


@router.patch("/projects/{project_id}/sessions/{session_id}")
async def update_session(
    project_id: str,
    session_id: str,
    request: UpdateSessionRequest,
    user_id: str = Header(alias="x-user-id"),
) -> Session:
    """Update a session's name."""
    for value, field in ((user_id, "user id"), (project_id, "project id"), (session_id, "session id")):
        safe_segment(value, field)
    config = get_config()
    bucket_name = config.session_storage_bucket_name

    if not bucket_name:
        raise HTTPException(status_code=500, detail="Session storage bucket not configured")

    s3 = get_s3_client()
    key = f"sessions/{user_id}/{project_id}/session_{session_id}/session.json"

    try:
        response = s3.get_object(Bucket=bucket_name, Key=key)
    except s3.exceptions.NoSuchKey:
        raise HTTPException(status_code=404, detail="Session not found") from None

    session_data = json.loads(response["Body"].read().decode("utf-8"))

    session_data["session_name"] = request.session_name

    s3.put_object(
        Bucket=bucket_name,
        Key=key,
        Body=json.dumps(session_data),
        ContentType="application/json",
    )

    await invalidate(CacheKey.session_list(user_id, project_id))

    return Session(
        session_id=session_data["session_id"],
        session_type=session_data["session_type"],
        created_at=session_data["created_at"],
        updated_at=session_data["updated_at"],
        session_name=session_data["session_name"],
    )


@router.delete("/projects/{project_id}/sessions/{session_id}")
async def delete_session(
    project_id: str, session_id: str, user_id: str = Header(alias="x-user-id")
) -> DeleteSessionResponse:
    """Delete a session from S3."""
    for value, field in ((user_id, "user id"), (project_id, "project id"), (session_id, "session id")):
        safe_segment(value, field)
    config = get_config()
    bucket_name = config.session_storage_bucket_name

    if not bucket_name:
        raise HTTPException(status_code=500, detail="Session storage bucket not configured")

    prefix = f"sessions/{user_id}/{project_id}/session_{session_id}/"
    deleted_count = delete_s3_prefix(bucket_name, prefix)

    await invalidate(CacheKey.session_list(user_id, project_id))

    return DeleteSessionResponse(deleted_count=deleted_count)
