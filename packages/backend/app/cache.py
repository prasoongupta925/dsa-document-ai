"""List caching for the routers, now switched off.

The Valkey (ElastiCache Serverless) cache is gone. The backend runs on Lambda
with many containers, where an in-process cache would serve stale lists from
one container after another one changed them, and the DynamoDB/DuckDB reads
behind these lists are fast enough. The API is kept so the routers need no
change: every cached_* call reads through and invalidate() does nothing.
"""

import inspect
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar, cast

from app.ddb.projects import query_projects
from app.duckdb import query_agents, query_sessions

T = TypeVar("T")


class CacheKey:
    QUERY_PROJECTS = "query_projects"

    @staticmethod
    def session_list(user_id: str, project_id: str) -> str:
        return f"session_list:{user_id}:{project_id}"

    @staticmethod
    def agent_list(user_id: str, project_id: str) -> str:
        return f"agent_list:{user_id}:{project_id}"


def _cached(
    key_fn: Callable[..., str], expire: int
) -> Callable[[Callable[..., Coroutine[Any, Any, T]]], Callable[..., Coroutine[Any, Any, T]]]:
    """Wrap fn (sync or async) as a coroutine function that always calls it.

    key_fn and expire are accepted for the existing call sites and ignored:
    nothing is cached, so every call is a miss.
    """

    def wrapper(fn: Callable[..., Coroutine[Any, Any, T]]) -> Callable[..., Coroutine[Any, Any, T]]:
        async def inner(*args: Any, **kwargs: Any) -> T:
            if inspect.iscoroutinefunction(fn):
                return await fn(*args, **kwargs)
            return cast(T, fn(*args, **kwargs))

        return inner

    return wrapper


async def invalidate(key: str) -> None:
    """Nothing is cached, so there is nothing to drop."""


cached_query_projects = _cached(lambda: CacheKey.QUERY_PROJECTS, expire=3600)(query_projects)


def _session_list_key(user_id: str, project_id: str) -> str:
    return CacheKey.session_list(user_id, project_id)


cached_query_sessions = _cached(_session_list_key, expire=3600)(query_sessions)


def _agent_list_key(user_id: str, project_id: str) -> str:
    return CacheKey.agent_list(user_id, project_id)


cached_query_agents = _cached(_agent_list_key, expire=3600)(query_agents)
