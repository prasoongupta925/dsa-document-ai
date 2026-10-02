# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""WhatsApp Business Calling webhook: verification, signature first, only call events forwarded.
The WebRTC media path needs aiortc and Meta: it is replaced by fakes here."""

import asyncio
import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import whatsapp_routes
from bot import BotDeps
from conftest import make_settings
from fakes import IDP_URL, FakeIdp
from idp_client import IdpClient
from whatsapp_routes import call_events, register_whatsapp_routes

SECRET = "app-secret-123"
SDP = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"


def wa_settings(**overrides):
    base = dict(WHATSAPP_TOKEN="EAAG-fake", WHATSAPP_PHONE_NUMBER_ID="1234567890", WHATSAPP_APP_SECRET=SECRET,
                WHATSAPP_WEBHOOK_VERIFICATION_TOKEN="verify-me")
    base.update(overrides)
    return make_settings(**base)


def connect_payload(event="connect"):
    call = {"id": "wacid.ABC", "from": "91" "9800000001", "to": "91" "2200000000", "event": event, "timestamp": "1759300000",
            "direction": "USER_INITIATED"}
    value = {"messaging_product": "whatsapp", "metadata": {"display_phone_number": "91" "2200000000", "phone_number_id": "1234567890"},
             "calls": [call]}
    if event == "connect":
        call["session"] = {"sdp": SDP, "sdp_type": "offer"}
        value["contacts"] = [{"profile": {"name": "Sneha"}, "wa_id": "91" "9800000001"}]
    else:
        call.update(status="COMPLETED", duration=42)
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA1", "changes": [{"field": "calls", "value": value}]}]}


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


class FakeApi:
    def __init__(self):
        self.rejected = []

    async def reject_call_to_whatsapp(self, call_id):
        self.rejected.append(call_id)
        return {"success": True}


class FakeWhatsAppClient:
    def __init__(self):
        self.requests = []
        self._whatsapp_api = FakeApi()

    async def handle_webhook_request(self, request, connection_callback=None, raw_body=None, sha256_signature=None):
        self.requests.append((request, raw_body, sha256_signature))
        for entry in request.entry:
            for change in entry.changes:
                for call in change.value.calls:
                    if call.event == "connect" and connection_callback:
                        await connection_callback(object())
        return True

    async def terminate_all_calls(self):
        pass


@pytest.fixture
def wa(monkeypatch):
    started = []

    async def fake_run_call(transport, session, deps, on_transfer=None):
        started.append(session)
        return {}

    monkeypatch.setattr(whatsapp_routes, "run_call", fake_run_call)
    monkeypatch.setattr(whatsapp_routes, "webrtc_transport", lambda connection: ("webrtc", connection))
    fake_client = FakeWhatsAppClient()

    def build(settings=None):
        app = FastAPI()
        deps = BotDeps(settings=settings or wa_settings(), idp=IdpClient(IDP_URL, transport=FakeIdp().transport()))
        calls = register_whatsapp_routes(app, deps, client_factory=lambda: fake_client)
        return TestClient(app), calls, fake_client, started

    return build


def test_verification_handshake(wa):
    client, *_ = wa()
    ok = client.get("/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "1158201444"})
    assert ok.status_code == 200 and ok.text == "1158201444"
    assert client.get("/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"}).status_code == 401
    assert client.get("/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "<x>"}).status_code == 401


def test_signature_is_checked_before_parsing(wa):
    client, _, fake_client, started = wa()
    body = json.dumps(connect_payload()).encode()
    assert client.post("/whatsapp", content=body).status_code == 401
    assert client.post("/whatsapp", content=body, headers={"X-Hub-Signature-256": "sha256=00"}).status_code == 401
    assert fake_client.requests == [] and started == []


async def test_connect_event_starts_a_whatsapp_call(wa):
    client, calls, fake_client, started = wa()
    body = json.dumps(connect_payload()).encode()
    r = client.post("/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body), "Content-Type": "application/json"})
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    request, raw, sig = fake_client.requests[0]
    assert raw == body and sig == sign(body)
    assert request.entry[0].changes[0].value.calls[0].id == "wacid.ABC"
    for _ in range(50):
        if started:
            break
        await asyncio.sleep(0.01)
    assert started and started[0].channel == "whatsapp" and started[0].idp_user == "voice-bot-whatsapp"
    assert started[0].applicant_hint is None  # the WhatsApp number is never used to find a file


def test_terminate_event_is_forwarded_and_other_events_ignored(wa):
    client, _, fake_client, started = wa()
    body = json.dumps(connect_payload("terminate")).encode()
    assert client.post("/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body)}).status_code == 200
    assert fake_client.requests and not started
    status_update = {"object": "whatsapp_business_account", "entry": [{"id": "W", "changes": [{"field": "messages", "value": {"statuses": []}}]}]}
    body = json.dumps(status_update).encode()
    r = client.post("/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body)})
    assert r.status_code == 200 and r.json() == {"status": "ignored"}
    assert len(fake_client.requests) == 1


def test_call_events_filter():
    payload = connect_payload()
    payload["entry"][0]["changes"].append({"field": "messages", "value": {"messages": [{"text": "hi"}]}})
    reduced = call_events(payload)
    assert len(reduced["entry"][0]["changes"]) == 1 and reduced["entry"][0]["changes"][0]["field"] == "calls"
    assert call_events({"object": "page"}) is None
    assert call_events({"object": "whatsapp_business_account", "entry": []}) is None


def test_off_without_meta_credentials(wa):
    client, calls, *_ = wa(make_settings())
    assert calls is None and client.get("/whatsapp").status_code == 404


def test_busy_server_declines_instead_of_answering(wa):
    client, calls, fake_client, started = wa()
    calls.deps.slots.active = calls.deps.slots.limit
    body = json.dumps(connect_payload()).encode()
    r = client.post("/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body)})
    assert r.json() == {"status": "busy"} and fake_client._whatsapp_api.rejected == ["wacid.ABC"]
    assert fake_client.requests == [] and started == []
