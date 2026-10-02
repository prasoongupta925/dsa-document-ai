import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.cache import CacheKey, _cached, invalidate
from app.config import get_config
from app.duckdb import AgentListItem
from app.s3 import get_s3_client
from app.safe_ids import safe_segment

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/agents", tags=["agents"])

# Built-in agents ship with the platform: packages/infra/src/prompts/builtin_agents/
# is deployed (AgentStack) to __prompts/builtin_agents/{agent_id}.json in the
# agent bucket. Every user sees them in every project, next to their own custom
# agents, and nobody can change them: ids starting with "builtin-" are reserved.
BUILTIN_AGENT_PREFIX = "builtin-"
BUILTIN_AGENTS_S3_PREFIX = "__prompts/builtin_agents/"
BUILTIN_AGENT_ID_RE = re.compile(r"builtin-[a-z0-9-]{1,64}")  # always fullmatch
_BUILTIN_AGENT_KEY_RE = re.compile(r"__prompts/builtin_agents/(builtin-[a-z0-9-]{1,64})\.json")
BUILTIN_READ_ONLY = "Built-in agents are read-only"
BUILTIN_AGENTS_CACHE_KEY = "builtin_agents"


class AgentCreate(BaseModel):
    name: str
    content: str


class AgentUpdate(BaseModel):
    name: str
    content: str


class AgentResponse(BaseModel):
    agent_id: str
    name: str
    content: str
    created_at: str
    builtin: bool = False
    description: str | None = None


class DeleteAgentResponse(BaseModel):
    message: str


class BuiltinAgentsUnavailable(Exception):
    """No built-in agent could be read. Raised, so the empty result is not cached."""


def _get_agents_prefix(user_id: str, project_id: str) -> str:
    return f"{user_id}/{project_id}/agents/"


def _get_agent_key(user_id: str, project_id: str, agent_id: str) -> str:
    return f"{_get_agents_prefix(user_id, project_id)}{agent_id}.json"


def is_builtin_agent_id(agent_id: str) -> bool:
    """True for every id in the reserved built-in namespace, well formed or not."""
    return agent_id.startswith(BUILTIN_AGENT_PREFIX)


def _check_owner_ids(user_id: str, project_id: str) -> None:
    # Both go into S3 keys, and list_agents builds a DuckDB glob from them.
    safe_segment(user_id, "user id")
    safe_segment(project_id, "project id")


def writable_agent_id(agent_id: str) -> str:
    """Path agent id of a PUT or DELETE: built-in ids are refused with 403.

    A dependency, so the refusal comes before the request body is validated.
    """
    if is_builtin_agent_id(agent_id):
        raise HTTPException(status_code=403, detail=BUILTIN_READ_ONLY)
    return safe_segment(agent_id, "agent id")


WritableAgentId = Annotated[str, Depends(writable_agent_id)]


def _iso(value: object) -> str:
    return value.isoformat() if isinstance(value, datetime) else ""


def _parse_builtin_agent(agent_id: str, raw: bytes) -> dict | None:
    """Fields of a built-in agent file, or None unless it is well formed and names agent_id."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("agent_id") != agent_id:
        return None
    name, content, description = data.get("name"), data.get("content"), data.get("description")
    if not isinstance(name, str) or not name.strip() or not isinstance(content, str) or not content.strip():
        return None
    return {
        "name": name,
        "content": content,
        "description": description if isinstance(description, str) else None,
    }


def query_builtin_agents() -> list[AgentListItem]:
    """The built-in agents in the agent bucket, sorted by name.

    Only direct children named ``builtin-<id>.json`` count; a malformed file is
    skipped. Raises when the bucket cannot be read or holds no built-in agent,
    so that result is not cached and the next request tries again.
    """
    config = get_config()
    bucket = config.agent_storage_bucket_name
    if not bucket:
        raise BuiltinAgentsUnavailable("agent storage bucket not configured")

    s3 = get_s3_client()
    agents: list[AgentListItem] = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=BUILTIN_AGENTS_S3_PREFIX):
        for obj in page.get("Contents", []):
            match = _BUILTIN_AGENT_KEY_RE.fullmatch(obj.get("Key", ""))
            if not match:
                continue
            agent_id = match.group(1)
            try:
                response = s3.get_object(Bucket=bucket, Key=obj["Key"])
            except s3.exceptions.NoSuchKey:
                continue  # removed since the listing
            data = _parse_builtin_agent(agent_id, response["Body"].read())
            if data is None:
                logger.warning("Skipping malformed built-in agent file for %s", agent_id)
                continue
            agents.append(
                AgentListItem(
                    agent_id=agent_id,
                    name=data["name"],
                    created_at=_iso(obj.get("LastModified")),
                    builtin=True,
                    description=data["description"],
                )
            )

    if not agents:
        raise BuiltinAgentsUnavailable("no built-in agents found")
    agents.sort(key=lambda agent: (agent.name.casefold(), agent.agent_id))
    return agents


# Cached like the per-user agent list, under one key shared by every user.
cached_query_builtin_agents = _cached(lambda: BUILTIN_AGENTS_CACHE_KEY, expire=3600)(query_builtin_agents)


async def _list_builtin_agents() -> list[AgentListItem]:
    """The built-in agents, or [] when they cannot be read right now."""
    try:
        return await cached_query_builtin_agents()
    except Exception as e:  # S3 or cache failure: the custom agents are still listed
        logger.warning("Built-in agents unavailable (%s)", type(e).__name__)
        return []


def _get_builtin_agent(agent_id: str) -> AgentResponse:
    if not BUILTIN_AGENT_ID_RE.fullmatch(agent_id):
        raise HTTPException(status_code=400, detail="Invalid agent id")

    config = get_config()
    if not config.agent_storage_bucket_name:
        raise HTTPException(status_code=404, detail="Agent not found")
    s3 = get_s3_client()
    try:
        response = s3.get_object(
            Bucket=config.agent_storage_bucket_name,
            Key=f"{BUILTIN_AGENTS_S3_PREFIX}{agent_id}.json",
        )
    except s3.exceptions.NoSuchKey as e:
        raise HTTPException(status_code=404, detail="Agent not found") from e

    data = _parse_builtin_agent(agent_id, response["Body"].read())
    if data is None:
        logger.warning("Malformed built-in agent file for %s", agent_id)
        raise HTTPException(status_code=404, detail="Agent not found")
    return AgentResponse(
        agent_id=agent_id,
        name=data["name"],
        content=data["content"],
        created_at=_iso(response.get("LastModified")),
        builtin=True,
        description=data["description"],
    )


@router.get("")
async def list_agents(project_id: str, x_user_id: str = Header(alias="x-user-id")) -> list[AgentListItem]:
    """List the user's custom agents for a project, then the built-in agents.

    Built-in agents are read from the agent bucket (cached); when they cannot
    be read, the custom agents are returned on their own.
    """
    from app.cache import cached_query_agents

    _check_owner_ids(x_user_id, project_id)

    custom = [
        agent for agent in await cached_query_agents(x_user_id, project_id) if not is_builtin_agent_id(agent.agent_id)
    ]
    return custom + await _list_builtin_agents()


@router.post("")
async def create_agent(
    project_id: str, request: AgentCreate, x_user_id: str = Header(alias="x-user-id")
) -> AgentResponse:
    """Create a new agent with auto-generated UUID."""
    _check_owner_ids(x_user_id, project_id)
    config = get_config()
    s3 = get_s3_client()

    # A UUID never starts with "builtin-", so POST cannot create a built-in id.
    agent_id = str(uuid.uuid4())
    key = _get_agent_key(x_user_id, project_id, agent_id)

    now = datetime.now(UTC).isoformat()
    data = {
        "name": request.name,
        "content": request.content,
        "created_at": now,
    }

    s3.put_object(
        Bucket=config.agent_storage_bucket_name,
        Key=key,
        Body=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )

    await invalidate(CacheKey.agent_list(x_user_id, project_id))

    return AgentResponse(
        agent_id=agent_id,
        name=request.name,
        content=request.content,
        created_at=now,
    )


@router.get("/{agent_id}")
def get_agent(project_id: str, agent_id: str, x_user_id: str = Header(alias="x-user-id")) -> AgentResponse:
    """Get a specific agent by ID: the user's custom agent, or a built-in agent (read-only)."""
    _check_owner_ids(x_user_id, project_id)
    if is_builtin_agent_id(agent_id):
        return _get_builtin_agent(agent_id)
    safe_segment(agent_id, "agent id")

    config = get_config()
    s3 = get_s3_client()

    key = _get_agent_key(x_user_id, project_id, agent_id)

    try:
        response = s3.get_object(Bucket=config.agent_storage_bucket_name, Key=key)
        data = json.loads(response["Body"].read().decode("utf-8"))
        last_modified = response["LastModified"].isoformat()

        return AgentResponse(
            agent_id=agent_id,
            name=data.get("name", ""),
            content=data.get("content", ""),
            created_at=data.get("created_at", last_modified),
        )
    except s3.exceptions.NoSuchKey as e:
        raise HTTPException(status_code=404, detail="Agent not found") from e


@router.put("/{agent_id}")
async def upsert_agent(
    project_id: str, agent_id: WritableAgentId, request: AgentUpdate, x_user_id: str = Header(alias="x-user-id")
) -> AgentResponse:
    """Create or update an agent (upsert). Built-in agents are refused (403)."""
    _check_owner_ids(x_user_id, project_id)
    config = get_config()
    s3 = get_s3_client()

    key = _get_agent_key(x_user_id, project_id, agent_id)
    now = datetime.now(UTC).isoformat()

    # Try to get existing created_at
    created_at = now
    try:
        existing = s3.get_object(Bucket=config.agent_storage_bucket_name, Key=key)
        existing_data = json.loads(existing["Body"].read().decode("utf-8"))
        created_at = existing_data.get("created_at", now)
    except Exception:
        pass

    data = {
        "name": request.name,
        "content": request.content,
        "created_at": created_at,
    }

    s3.put_object(
        Bucket=config.agent_storage_bucket_name,
        Key=key,
        Body=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )

    await invalidate(CacheKey.agent_list(x_user_id, project_id))

    return AgentResponse(
        agent_id=agent_id,
        name=request.name,
        content=request.content,
        created_at=created_at,
    )


@router.delete("/{agent_id}")
async def delete_agent(
    project_id: str, agent_id: WritableAgentId, x_user_id: str = Header(alias="x-user-id")
) -> DeleteAgentResponse:
    """Delete an agent. Built-in agents are refused (403)."""
    _check_owner_ids(x_user_id, project_id)
    config = get_config()
    s3 = get_s3_client()

    key = _get_agent_key(x_user_id, project_id, agent_id)

    try:
        s3.head_object(Bucket=config.agent_storage_bucket_name, Key=key)
    except s3.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "404":
            raise HTTPException(status_code=404, detail="Agent not found") from e
        raise

    s3.delete_object(Bucket=config.agent_storage_bucket_name, Key=key)

    await invalidate(CacheKey.agent_list(x_user_id, project_id))

    return DeleteAgentResponse(message=f"Agent {agent_id} deleted")
