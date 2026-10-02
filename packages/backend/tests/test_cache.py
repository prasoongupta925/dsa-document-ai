"""app.cache without Valkey: the routers' cache API stays, nothing is cached.

Every cached_* call reads through to the wrapped function and invalidate()
does nothing, so a list is never served stale from another Lambda container.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import app.cache as cache
from app.cache import CacheKey, _cached, invalidate
from app.config import Config
from app.main import app

client = TestClient(app)


def test_every_call_reads_through_for_sync_and_async_functions():
    calls = []

    def sync_listing(user_id, project_id):
        calls.append(("sync", user_id, project_id))
        return len(calls)

    async def async_listing(user_id, project_id):
        calls.append(("async", user_id, project_id))
        return len(calls)

    cached_sync = _cached(CacheKey.session_list, expire=3600)(sync_listing)
    cached_async = _cached(CacheKey.agent_list, expire=3600)(async_listing)

    async def run():
        return [
            await cached_sync("user-1", "proj-1"),
            await cached_sync("user-1", "proj-1"),
            await cached_async("user-1", "proj-1"),
            await cached_async("user-1", "proj-1"),
        ]

    assert asyncio.run(run()) == [1, 2, 3, 4]
    assert calls == [("sync", "user-1", "proj-1")] * 2 + [("async", "user-1", "proj-1")] * 2


def test_a_failure_is_raised_and_the_next_call_tries_again():
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("S3 slow down")
        return ["ok"]

    cached = _cached(lambda: "flaky", expire=3600)(flaky)

    with pytest.raises(RuntimeError):
        asyncio.run(cached())
    assert asyncio.run(cached()) == ["ok"]


def test_invalidate_does_nothing():
    for key in (CacheKey.QUERY_PROJECTS, CacheKey.session_list("u", "p"), CacheKey.agent_list("u", "p")):
        assert asyncio.run(invalidate(key)) is None


def test_cache_keys_keep_their_format():
    assert CacheKey.QUERY_PROJECTS == "query_projects"
    assert CacheKey.session_list("user-1", "proj-1") == "session_list:user-1:proj-1"
    assert CacheKey.agent_list("user-1", "proj-1") == "agent_list:user-1:proj-1"


def test_no_valkey_client_or_endpoint_setting_is_left():
    assert not hasattr(cache, "_get_cache_client")
    assert "elasticache_endpoint" not in Config.model_fields


@patch("app.ddb.projects.get_table")
def test_the_project_list_is_read_from_dynamodb_on_every_request(mock_get_table):
    table = MagicMock()
    table.query.return_value = {"Items": []}
    mock_get_table.return_value = table

    assert client.get("/projects").json() == []
    assert client.get("/projects").json() == []

    assert table.query.call_count == 2
