# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Outbound "call this applicant": guard rails, provider requests (captured, never sent), HTTP route."""

from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import outbound
import security
from auth import AuthError, Principal
from config import exotel_url_key
from conftest import make_settings
from outbound import DialRefused, check_dial, dial, exotel_call_form, plivo_call_body
from prompt import IST

KEY = "k" * 40
TEAM = "+91" "9800000001"
MONDAY_11 = datetime(2026, 10, 5, 11, 0, tzinfo=IST)


@pytest.fixture(autouse=True)
def _reset_attempts():
    outbound._attempts.clear()
    yield
    outbound._attempts.clear()


def phone_settings(**overrides):
    base = dict(PUBLIC_HOST="dxxxx.cloudfront.net", PHONE_URL_KEY=KEY, PLIVO_AUTH_ID="MA1", PLIVO_AUTH_TOKEN="tok",
                PLIVO_NUMBER="+91" "2200000000", OUTBOUND_ALLOWED_TO=TEAM, EXOTEL_SID="sid1", EXOTEL_API_KEY="ek",
                EXOTEL_API_TOKEN="et", EXOTEL_EXOPHONE="+91" "2212345678")
    base.update(overrides)
    return make_settings(**base)


def test_guard_rails():
    s = phone_settings()
    check_dial(s, TEAM, MONDAY_11)
    with pytest.raises(DialRefused, match="OUTBOUND_ALLOWED_TO"):
        check_dial(s, "+91" "9811111111", MONDAY_11)
    with pytest.raises(DialRefused, match="E.164"):
        check_dial(s, "9800000001", MONDAY_11)
    with pytest.raises(DialRefused, match="window"):
        check_dial(s, TEAM, datetime(2026, 10, 5, 19, 0, tzinfo=IST))
    with pytest.raises(DialRefused, match="window"):
        check_dial(s, TEAM, datetime(2026, 10, 5, 9, 59, tzinfo=IST))
    with pytest.raises(DialRefused, match="window"):
        check_dial(s, TEAM, datetime(2026, 10, 4, 11, 0, tzinfo=IST))  # Sunday
    with pytest.raises(DialRefused, match="OUTBOUND_ALLOWED_TO is empty"):
        check_dial(phone_settings(OUTBOUND_ALLOWED_TO=""), TEAM, MONDAY_11)


def test_attempt_cap_per_number_per_day():
    s = phone_settings(OUTBOUND_MAX_ATTEMPTS_PER_DAY="2")
    check_dial(s, TEAM, MONDAY_11)
    check_dial(s, TEAM, MONDAY_11)
    with pytest.raises(DialRefused, match="maximum"):
        check_dial(s, TEAM, MONDAY_11)
    check_dial(s, TEAM, datetime(2026, 10, 6, 11, 0, tzinfo=IST))  # next day


def test_plivo_body_carries_a_sealed_context_not_the_name():
    s = phone_settings()
    ctx = outbound.call_context(s, "Sneha Kulkarni", "mr")
    body = plivo_call_body(s, TEAM, ctx)
    assert body["from"] == "+91" "2200000000" and body["to"] == TEAM and body["time_limit"] == 630 and body["ring_timeout"] == 30
    assert body["answer_url"].startswith("https://dxxxx.cloudfront.net/phone/plivo/answer?ctx=") and "Sneha" not in body["answer_url"]
    sealed = parse_qs(urlparse(body["answer_url"]).query)["ctx"][0]
    assert security.unseal(sealed, KEY, 900) == {"a": "Sneha Kulkarni", "l": "mr"}
    assert body["hangup_url"] == "https://dxxxx.cloudfront.net/phone/plivo/status"


def test_exotel_form_uses_the_mumbai_stream_url():
    s = phone_settings()
    form = exotel_call_form(s, TEAM, "CTX")
    assert form["From"] == TEAM and form["CallerId"] == "+91" "2212345678" and form["StreamType"] == "bidirectional"
    url_key = exotel_url_key(KEY)  # Exotel sees these URLs: a derived key, never PHONE_URL_KEY itself
    assert len(url_key) == 43 and KEY not in url_key
    assert form["StreamUrl"] == f"wss://dxxxx.cloudfront.net/phone/exotel/ws/{url_key}/CTX?sample-rate=16000"
    assert form["StatusCallback"] == f"https://dxxxx.cloudfront.net/phone/exotel/status/{url_key}"
    assert KEY not in form["StreamUrl"] + form["StatusCallback"]


class FakeResp:
    def __init__(self, status, data):
        self.status, self._data = status, data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self._data


class FakeHttp:
    def __init__(self, status=201, data=None):
        self.posts = []
        self.status, self.data = status, data or {"message": "call fired", "request_uuid": "req-1"}

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return FakeResp(self.status, self.data)


async def test_dial_plivo_and_exotel(monkeypatch):
    monkeypatch.setattr(outbound, "check_dial", lambda settings, to, now=None, count=True: None)
    s = phone_settings()
    http = FakeHttp()
    assert await dial(s, TEAM, "Sneha Kulkarni", "hi", "plivo", http=http) == {"provider": "plivo", "request_id": "req-1"}
    url, kwargs = http.posts[0]
    assert url == "https://api.plivo.com/v1/Account/MA1/Call/" and kwargs["json"]["to"] == TEAM
    assert kwargs["auth"].login == "MA1"
    http2 = FakeHttp(200, {"Call": {"Sid": "exo-sid-9"}})
    assert await dial(s, TEAM, "Sneha Kulkarni", "hi", "exotel", http=http2) == {"provider": "exotel", "request_id": "exo-sid-9"}
    url, kwargs = http2.posts[0]
    assert url == "https://api.in.exotel.com/v1/Accounts/sid1/Calls/connect" and kwargs["data"]["From"] == TEAM
    with pytest.raises(RuntimeError):
        await dial(s, TEAM, "Sneha", "hi", "plivo", http=FakeHttp(400, {"error": "x"}))


async def test_dial_refuses_unconfigured_providers():
    s = make_settings(OUTBOUND_ALLOWED_TO=TEAM)
    with pytest.raises(DialRefused, match="Plivo is not configured"):
        await dial(s, TEAM, "Sneha", "hi", "plivo", http=FakeHttp())
    with pytest.raises(DialRefused, match="Exotel is not configured"):
        await dial(s, TEAM, "Sneha", "hi", "", http=FakeHttp())


class FakeVerifier:
    async def verify(self, token):
        if token != "good-token":
            raise AuthError("bad")
        return Principal(sub="sub-1", username="asha.verma")


def test_http_route_needs_a_cognito_token_and_reports_refusals(monkeypatch):
    s = phone_settings()
    app = FastAPI()
    outbound.register_outbound_routes(app, s, FakeVerifier())
    client = TestClient(app)
    body = {"to": TEAM, "applicant": "Sneha Kulkarni", "language": "hi", "provider": "plivo"}
    assert client.post("/calls/outbound", json=body).status_code == 401
    assert client.post("/calls/outbound", json=body, headers={"Authorization": "Bearer nope"}).status_code == 401

    async def refused(*args, **kwargs):
        raise DialRefused("outside the calling window (10:00-19:00 IST)")

    monkeypatch.setattr(outbound, "dial", refused)
    r = client.post("/calls/outbound", json=body, headers={"Authorization": "Bearer good-token"})
    assert r.status_code == 409 and "window" in r.json()["detail"]

    async def placed(settings, to, applicant, language, provider):
        return {"provider": provider, "request_id": "req-9"}

    monkeypatch.setattr(outbound, "dial", placed)
    r = client.post("/calls/outbound", json=body, headers={"Authorization": "Bearer good-token"})
    assert r.status_code == 202 and r.json() == {"provider": "plivo", "request_id": "req-9"}
    assert client.post("/calls/outbound", content="x", headers={"Authorization": "Bearer good-token"}).status_code == 400


def test_cli_refuses_without_configuration(monkeypatch, capsys):
    from loguru import logger

    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    for name in ("PHONE_URL_KEY", "PLIVO_AUTH_ID", "OUTBOUND_ALLOWED_TO", "PUBLIC_HOST"):
        monkeypatch.delenv(name, raising=False)
    try:
        assert outbound._cli(["--to", TEAM, "--applicant", "Sneha Kulkarni"]) == 2
    finally:
        logger.remove()
    assert "refused" in capsys.readouterr().err
