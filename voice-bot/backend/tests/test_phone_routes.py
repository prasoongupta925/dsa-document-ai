# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Plivo and Exotel routes with fake provider messages (signed like Plivo signs, Exotel's start events)."""

import base64
import json
import re
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import phone_routes
import security
from bot import BotDeps
from config import exotel_url_key
from conftest import make_settings
from fakes import IDP_URL, FakeIdp
from idp_client import IdpClient

HOST = "dxxxx.cloudfront.net"
KEY = "k" * 40
EXO_KEY = exotel_url_key(KEY)  # the secret in Exotel's URLs (never PHONE_URL_KEY itself)
PUBLIC = f"https://{HOST}"
CALL_UUID = "12345678-1234-1234-1234-123456789abc"
STREAM_ID = "87654321-4321-4321-4321-cba987654321"


def plivo_settings(**overrides):
    base = dict(PUBLIC_HOST=HOST, PHONE_URL_KEY=KEY, PLIVO_AUTH_ID="MAFAKEFAKEFAKEFAKE00", PLIVO_AUTH_TOKEN="fake-token",
                PLIVO_NUMBER="+91" "2200000000", TELECALLER_NUMBER="+91" "9800000009")
    base.update(overrides)
    return make_settings(**base)


def signed_headers(url, params, token="fake-token", nonce="12345678901234567890"):
    return {
        "X-Plivo-Signature-V3": security.plivo_v3_signature("POST", url, nonce, token, params),
        "X-Plivo-Signature-V3-Nonce": nonce,
        "Content-Type": "application/x-www-form-urlencoded",
    }


@pytest.fixture
def phone(monkeypatch):
    calls = []

    async def fake_run_call(transport, session, deps, on_transfer=None):
        serializer = transport._params.serializer
        calls.append({"transport": transport, "session": session, "serializer": serializer, "on_transfer": on_transfer,
                      "chunks": transport._params.audio_out_10ms_chunks})
        await transport._client._websocket.close()
        return {}

    monkeypatch.setattr(phone_routes, "run_call", fake_run_call)

    def build(settings=None):
        settings = settings or plivo_settings()
        app = FastAPI()
        deps = BotDeps(settings=settings, idp=IdpClient(IDP_URL, transport=FakeIdp().transport()))
        enabled = phone_routes.register_phone_routes(app, deps)
        return TestClient(app), calls, enabled

    return build


def plivo_start(call_id=CALL_UUID):
    return {"event": "start", "sequenceNumber": 1, "start": {"callId": call_id, "streamId": STREAM_ID, "accountId": "MAFAKE",
            "tracks": ["inbound"], "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000}}}


MEDIA = {"event": "media", "streamId": STREAM_ID, "media": {"payload": base64.b64encode(b"\xff" * 160).decode()}}


def answer(client, path="/phone/plivo/answer", form=None, headers=None):
    form = form or {"CallUUID": CALL_UUID, "From": "+91" "9800000001", "To": "+91" "2200000000", "Direction": "inbound", "ForwardedFrom": ""}
    headers = headers if headers is not None else signed_headers(PUBLIC + path, form)
    return client.post(path, content=urlencode(form), headers=headers)


def test_plivo_answer_returns_mulaw_stream_xml_with_a_sealed_token(phone):
    client, _, enabled = phone()
    assert enabled
    r = answer(client)
    assert r.status_code == 200 and "application/xml" in r.headers["content-type"]
    assert 'contentType="audio/x-mulaw;rate=8000"' in r.text and 'bidirectional="true"' in r.text and 'streamTimeout="600"' in r.text
    token = re.search(r"wss://dxxxx\.cloudfront\.net/phone/plivo/ws/([^<]+)</Stream>", r.text).group(1)
    claims = security.unseal(token, KEY, 300)
    assert claims["call"] == CALL_UUID and claims["dir"] == "inbound" and claims["a"] is None
    assert claims["f"] == "+91" "9800000001"  # inbound caller ID, sealed: used only to verify the caller
    assert "+9198" not in r.text  # no phone number in the URL


@pytest.mark.parametrize(
    "case",
    ["wrong_token", "internal_url", "unsigned", "tampered"],
)
def test_plivo_answer_refuses_bad_signatures_with_401(phone, case):
    client, _, _ = phone()
    form = {"CallUUID": CALL_UUID, "From": "+91" "9800000001", "Direction": "inbound"}
    path = "/phone/plivo/answer"
    headers = {
        "wrong_token": signed_headers(PUBLIC + path, form, token="other"),
        "internal_url": signed_headers("http://testserver" + path, form),
        "unsigned": {"Content-Type": "application/x-www-form-urlencoded"},
        "tampered": signed_headers(PUBLIC + path, {**form, "From": "+91" "9811111111"}),
    }[case]
    assert answer(client, path, form, headers).status_code == 401


def test_plivo_outbound_answer_carries_the_sealed_applicant(phone):
    client, _, _ = phone()
    ctx = security.seal({"a": "Sneha Kulkarni", "l": "mr"}, KEY)
    path = f"/phone/plivo/answer?ctx={ctx}"
    form = {"CallUUID": CALL_UUID, "Direction": "outbound", "From": "+91" "2200000000", "To": "+91" "9800000001"}
    r = answer(client, path, form)
    token = re.search(r"/phone/plivo/ws/([^<]+)</Stream>", r.text).group(1)
    claims = security.unseal(token, KEY, 300)
    assert claims == {"call": CALL_UUID, "dir": "outbound", "a": "Sneha Kulkarni", "l": "mr", "f": None}
    assert "Sneha" not in r.text


def test_plivo_stream_starts_the_bot_bound_to_its_call(phone):
    client, calls, _ = phone(plivo_settings())  # default presets: QQ out, MQ in
    token = security.seal({"call": CALL_UUID, "dir": "outbound", "a": "Sneha Kulkarni", "l": "en"}, KEY)
    with client.websocket_connect(f"/phone/plivo/ws/{token}") as ws:
        ws.send_text(json.dumps(plivo_start()))
        ws.send_text(json.dumps(MEDIA))
        with pytest.raises(Exception):
            ws.receive_text()
    call = calls[-1]
    s = call["serializer"]
    assert type(s).__name__ == "PlivoBotSerializer"
    assert (s._stream_id, s._call_id, s._plivo_sample_rate, s._api_base) == (STREAM_ID, CALL_UUID, 8000, "https://api.plivo.com")
    assert (s._output_resampler._quality, s._input_resampler._quality) == ("QQ", "MQ")
    session = call["session"]
    assert session.channel == "plivo" and session.language == "en" and session.outbound and session.applicant_hint == "Sneha Kulkarni"
    assert session.provider_call_id == CALL_UUID and session.idp_user == "voice-bot-phone"
    assert call["chunks"] == 4 and call["on_transfer"] is not None


def test_plivo_stream_refuses_other_calls_and_bad_tokens(phone):
    client, calls, _ = phone()
    token = security.seal({"call": CALL_UUID}, KEY)
    with client.websocket_connect(f"/phone/plivo/ws/{token}") as ws:
        ws.send_text(json.dumps(plivo_start("another-call")))
        ws.send_text(json.dumps(MEDIA))
        with pytest.raises(Exception):
            ws.receive_text()
    assert calls == []
    for bad in ("bad.token.x", security.seal({"call": CALL_UUID}, "x" * 40)):
        with pytest.raises(Exception):
            with client.websocket_connect(f"/phone/plivo/ws/{bad}") as ws:
                ws.receive_text()
    assert calls == []


async def test_plivo_transfer_asks_plivo_and_never_hangs_up_the_transferred_call(phone, monkeypatch):
    client, calls, _ = phone()
    token = security.seal({"call": CALL_UUID}, KEY)
    with client.websocket_connect(f"/phone/plivo/ws/{token}") as ws:
        ws.send_text(json.dumps(plivo_start()))
        ws.send_text(json.dumps(MEDIA))
        with pytest.raises(Exception):
            ws.receive_text()
    call = calls[-1]
    requested = []

    async def fake_api(settings, call_uuid, aleg_url, delay_s=0):
        requested.append((call_uuid, aleg_url))
        return status["code"]

    status = {"code": 202}

    monkeypatch.setattr(phone_routes, "plivo_transfer_api", fake_api)
    outcome = await call["on_transfer"](call["session"])
    assert outcome == {"transferred": True, "end_stream": False}
    assert call["serializer"].transferred is True
    import asyncio

    await asyncio.sleep(0)
    uuid, aleg = requested[0]
    assert uuid == CALL_UUID and aleg.startswith(f"https://{HOST}/phone/plivo/transfer/")
    # Plivo then fetches aleg_url: <Dial> to the telecaller with our number as caller id.
    path = aleg.removeprefix(PUBLIC)
    form = {"CallUUID": CALL_UUID}
    r = client.post(path, content=urlencode(form), headers=signed_headers(PUBLIC + path, form))
    assert r.status_code == 200
    assert r.text.endswith('<Response><Dial callerId="+91' '2200000000" timeout="30"><Number>+91' '9800000009</Number></Dial></Response>')
    other = {"CallUUID": "someone-else"}
    assert client.post(path, content=urlencode(other), headers=signed_headers(PUBLIC + path, other)).status_code == 401

    # Plivo refuses the transfer: the call may be hung up again, and the bot apologises.
    said = []

    async def speak(text):
        said.append(text)

    call["session"].speak = speak
    status["code"] = 400
    await call["on_transfer"](call["session"])
    for _ in range(20):
        if said:
            break
        await asyncio.sleep(0.01)
    assert call["serializer"].transferred is False and said and "telecaller" in said[0]


async def test_plivo_transfer_without_a_telecaller_line(phone):
    client, calls, _ = phone(plivo_settings(TELECALLER_NUMBER=""))
    token = security.seal({"call": CALL_UUID}, KEY)
    with client.websocket_connect(f"/phone/plivo/ws/{token}") as ws:
        ws.send_text(json.dumps(plivo_start()))
        ws.send_text(json.dumps(MEDIA))
        with pytest.raises(Exception):
            ws.receive_text()
    outcome = await calls[-1]["on_transfer"](calls[-1]["session"])
    assert outcome["transferred"] is False and "call back" in outcome["say"]


def test_plivo_status_logs_and_needs_a_signature(phone):
    client, _, _ = phone()
    form = {"CallUUID": CALL_UUID, "HangupCause": "NORMAL_CLEARING", "Duration": "95", "From": "+91" "9800000001"}
    assert client.post("/phone/plivo/status", content=urlencode(form), headers=signed_headers(PUBLIC + "/phone/plivo/status", form)).status_code == 200
    assert client.post("/phone/plivo/status", content=urlencode(form)).status_code == 401


EXOTEL_START = {"event": "start", "sequence_number": 1, "stream_sid": "S1", "start": {
    "stream_sid": "S1", "call_sid": "EXO-CALL-1", "account_sid": "acct", "from": "+91" "9800000001", "to": "+91" "2200000000",
    "custom_parameters": {}, "media_format": {"encoding": "raw", "sample_rate": "16000", "bit_rate": "256"}}}


def test_exotel_inbound_and_outbound_streams(phone):
    client, calls, _ = phone(make_settings(PUBLIC_HOST=HOST, PHONE_URL_KEY=KEY))
    with client.websocket_connect(f"/phone/exotel/ws/{EXO_KEY}?sample-rate=16000") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        ws.send_text(json.dumps(EXOTEL_START))
        with pytest.raises(Exception):
            ws.receive_text()
    call = calls[-1]
    s = call["serializer"]
    assert type(s).__name__ == "ExotelFrameSerializer" and s._exotel_sample_rate == 16000
    assert call["chunks"] == 10  # 10 x 10 ms at 16 kHz = 3,200-byte chunks (multiples of 320 bytes)
    assert call["session"].channel == "exotel" and not call["session"].outbound

    ctx = security.seal({"a": "Sneha Kulkarni", "l": "hi"}, KEY)
    with client.websocket_connect(f"/phone/exotel/ws/{EXO_KEY}/{ctx}?sample-rate=16000") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        ws.send_text(json.dumps(EXOTEL_START))
        with pytest.raises(Exception):
            ws.receive_text()
    session = calls[-1]["session"]
    assert session.outbound and session.applicant_hint == "Sneha Kulkarni" and session.provider_call_id == "EXO-CALL-1"


async def test_exotel_transfer_only_hands_back_inbound_calls(phone):
    client, calls, _ = phone(make_settings(PUBLIC_HOST=HOST, PHONE_URL_KEY=KEY))
    with client.websocket_connect(f"/phone/exotel/ws/{EXO_KEY}") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        ws.send_text(json.dumps(EXOTEL_START))
        with pytest.raises(Exception):
            ws.receive_text()
    inbound = calls[-1]
    assert await inbound["on_transfer"](inbound["session"]) == {"transferred": True, "end_stream": True}
    inbound["session"].outbound = True
    assert (await inbound["on_transfer"](inbound["session"]))["transferred"] is False


def test_exotel_refuses_wrong_keys_and_forged_context(phone):
    client, calls, _ = phone(make_settings(PUBLIC_HOST=HOST, PHONE_URL_KEY=KEY))
    # PHONE_URL_KEY itself is refused too: only the derived URL key opens an Exotel stream.
    for path in ("/phone/exotel/ws/wrong-key", f"/phone/exotel/ws/{EXO_KEY}/forged-context", f"/phone/exotel/ws/{KEY}",
                 f"/phone/exotel/ws/{KEY}/{security.seal({'a': 'Sneha Kulkarni'}, KEY)}"):
        with pytest.raises(Exception):
            with client.websocket_connect(path) as ws:
                ws.receive_text()
    assert calls == []
    assert client.post("/phone/exotel/status/wrong", data={"CallSid": "x"}).status_code == 401
    assert client.post(f"/phone/exotel/status/{KEY}", data={"CallSid": "x"}).status_code == 401
    assert client.post(f"/phone/exotel/status/{EXO_KEY}", data={"CallSid": "x", "Status": "completed"}).status_code == 200


def test_phone_routes_stay_off_without_a_real_key(phone):
    client, _, enabled = phone(make_settings(PUBLIC_HOST=HOST, PHONE_URL_KEY="short"))
    assert not enabled
    assert client.post("/phone/plivo/answer").status_code == 404


def test_plivo_routes_refuse_without_plivo_credentials(phone):
    client, _, enabled = phone(make_settings(PUBLIC_HOST=HOST, PHONE_URL_KEY=KEY))
    assert enabled
    assert answer(client).status_code == 401
