# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""The FastAPI app: /health and the browser WebSocket's sign-in rules."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import main
from auth import AuthError, Principal
from bot import BotDeps
from conftest import make_settings
from fakes import IDP_URL, FakeIdp
from idp_client import IdpClient


class FakeVerifier:
    async def verify(self, token):
        if token != "good-token":
            raise AuthError("bad")
        return Principal(sub="sub-1", username="asha.verma")


@pytest.fixture
def app_client(monkeypatch):
    started = []

    async def fake_run_call(transport, session, deps, on_transfer=None):
        started.append((session, type(transport._params.serializer).__name__))
        await transport._client._websocket.close()
        return {}

    monkeypatch.setattr(main, "run_call", fake_run_call)

    def build(**overrides):
        settings = make_settings(**overrides)
        deps = BotDeps(settings=settings, idp=IdpClient(IDP_URL, transport=FakeIdp().transport()))
        app = main.create_app(settings, deps=deps, verifier=FakeVerifier())
        return TestClient(app), started, app

    return build


def test_health_reports_channels_without_secrets(app_client):
    client, _, _ = app_client(PLIVO_AUTH_TOKEN="super-secret")
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["channels"]["browser"] is True and body["channels"]["plivo"] is False
    assert body["idp"] is True and body["recording"] is True and body["active_calls"] == 0 and body["max_calls"] == 8
    assert "super-secret" not in str(body)


def test_browser_call_needs_a_valid_token(app_client):
    client, started, _ = app_client()
    with client.websocket_connect("/ws?language=hindi") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 4001 and started == []
    with client.websocket_connect("/ws?token=bad-token") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 4001


def test_browser_call_with_the_subprotocol_token(app_client):
    client, started, _ = app_client()
    with client.websocket_connect("/ws?language=marathi", subprotocols=["voicebot.v1", "auth.good-token"]) as ws:
        assert ws.accepted_subprotocol == "voicebot.v1"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    session, serializer = started[0]
    assert session.channel == "browser" and session.language == "mr" and session.idp_user == "asha.verma"
    assert serializer == "BrowserJsonSerializer"


def test_query_token_still_works_and_old_pipelines_are_refused(app_client):
    client, started, _ = app_client()
    with client.websocket_connect("/ws?token=good-token&pipeline=transcribe-polly&language=english") as ws:
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert started[-1][0].language == "en"
    with client.websocket_connect("/ws?token=good-token&pipeline=novasonic") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 1008


def test_busy_and_origin_rules(app_client, monkeypatch):
    client, started, app = app_client(ALLOWED_ORIGINS="https://voice.example.com")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=good-token", headers={"origin": "https://evil.example.com"}) as ws:
            ws.receive_text()
    app.state.slots.active = app.state.slots.limit
    with client.websocket_connect("/ws?token=good-token", headers={"origin": "https://voice.example.com"}) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 1013 and started == []


def test_config_error_exits_with_code_2(monkeypatch):
    from loguru import logger

    monkeypatch.setenv("AWS_REGION", "us-east-1")
    try:
        assert main.main() == 2
    finally:
        logger.remove()
