# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""WhatsApp Business Calling (customer-initiated calls are free in India).

Switched on when WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_APP_SECRET
and WHATSAPP_WEBHOOK_VERIFICATION_TOKEN are set (Meta app with Calling enabled;
webhook field "calls" subscribed to https://<voice host>/whatsapp).

  GET  /whatsapp   Meta's verification handshake (hub.verify_token must match)
  POST /whatsapp   call events. The X-Hub-Signature-256 HMAC (app secret) is checked
                   on the raw body before anything is parsed; only "calls"
                   connect/terminate events go to Pipecat's WhatsAppClient, which
                   answers with WebRTC (aiortc). Everything else gets 200 and is ignored.

The call audio runs over WebRTC between Meta and this server (UDP: the server needs a
public IP and an open UDP range: deploy/deploy.sh --whatsapp on). The caller's WhatsApp number is never
logged; it is used only to verify the caller against the mobile on the file (else
the bot asks for the date of birth). Files are found by the name the caller gives.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from loguru import logger

from bot import BotDeps, new_call_id, run_call, webrtc_transport
from security import same_secret, valid_meta_signature
from tools import CallSession


def call_events(payload: dict) -> dict | None:
    """The webhook payload reduced to its WhatsApp "calls" connect/terminate events, or None."""
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return None
    entries = []
    for entry in payload.get("entry") or []:
        changes = []
        for change in (entry or {}).get("changes") or []:
            value = (change or {}).get("value") or {}
            calls = [c for c in value.get("calls") or [] if isinstance(c, dict) and c.get("event") in ("connect", "terminate")]
            if change.get("field") == "calls" and calls:
                changes.append({**change, "value": {**value, "calls": calls}})
        if changes:
            entries.append({"id": str(entry.get("id", "")), "changes": changes})
    return {"object": "whatsapp_business_account", "entry": entries} if entries else None


class WhatsAppCalls:
    """Holds the Pipecat WhatsAppClient (created on first use: it needs aiortc)."""

    def __init__(self, deps: BotDeps, client_factory=None):
        self.deps = deps
        self._client = None
        self._http = None
        self._client_factory = client_factory
        self.calls: set[asyncio.Task] = set()

    async def client(self):
        if self._client is not None:
            return self._client
        settings = self.deps.settings
        if self._client_factory is not None:
            self._client = self._client_factory()
            return self._client
        import aiohttp
        from pipecat.transports.smallwebrtc.connection import IceServer
        from pipecat.transports.whatsapp.client import WhatsAppClient

        self._http = aiohttp.ClientSession()
        self._client = WhatsAppClient(
            whatsapp_token=settings.whatsapp_token,
            phone_number_id=settings.whatsapp_phone_number_id,
            session=self._http,
            ice_servers=[IceServer(urls=url) for url in settings.whatsapp_stun_urls],
            whatsapp_secret=settings.whatsapp_app_secret,
        )
        return self._client

    async def start_call(self, connection, caller_number: str | None = None) -> None:
        if not self.deps.slots.take():
            logger.bind(event="call_refused_busy").warning("too many calls: WhatsApp call dropped")
            try:
                await connection.disconnect()
            except Exception:  # noqa: BLE001
                pass
            return
        session = CallSession(
            call_id=new_call_id(),
            channel="whatsapp",
            language=self.deps.settings.default_language,
            idp_user="voice-bot-whatsapp",
            caller_number=caller_number,
        )

        async def _run():
            try:
                await run_call(webrtc_transport(connection), session, self.deps)
            except Exception as e:  # noqa: BLE001
                logger.bind(event="whatsapp_call_failed", call_id=session.call_id, error=type(e).__name__).warning("whatsapp call failed")
            finally:
                self.deps.slots.give()

        task = asyncio.create_task(_run())
        self.calls.add(task)
        task.add_done_callback(self.calls.discard)

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.terminate_all_calls()
            except Exception:  # noqa: BLE001
                pass
        if self._http is not None:
            await self._http.close()


def register_whatsapp_routes(app: FastAPI, deps: BotDeps, client_factory=None) -> WhatsAppCalls | None:
    settings = deps.settings
    if not settings.whatsapp_enabled:
        logger.bind(event="whatsapp_off").info("WhatsApp calling off: Meta credentials not set")
        return None
    calls = WhatsAppCalls(deps, client_factory)

    @app.get("/whatsapp")
    async def verify(request: Request):
        q = request.query_params
        if q.get("hub.mode") == "subscribe" and same_secret(q.get("hub.verify_token", ""), settings.whatsapp_verify_token):
            challenge = q.get("hub.challenge", "")
            if challenge.isdigit():
                return PlainTextResponse(challenge)
        return Response(status_code=401)

    @app.post("/whatsapp")
    async def webhook(request: Request):
        raw = await request.body()
        signature = request.headers.get("x-hub-signature-256")
        if not valid_meta_signature(raw, signature, settings.whatsapp_app_secret):
            logger.bind(event="whatsapp_denied").warning("bad X-Hub-Signature-256")
            return Response(status_code=401)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return Response(status_code=400)
        events = call_events(payload)
        if events is None:
            return JSONResponse({"status": "ignored"})
        try:
            from pipecat.transports.whatsapp.api import WhatsAppWebhookRequest

            request_model = WhatsAppWebhookRequest.model_validate(events)
            connects = [c for e in request_model.entry for ch in e.changes for c in ch.value.calls if c.event == "connect"]
            caller = connects[0].from_ if connects else None
            client = await calls.client()
            if connects and deps.slots.active >= deps.slots.limit:
                # Busy: decline before answering, rather than answer and drop.
                for c in connects:
                    await client._whatsapp_api.reject_call_to_whatsapp(c.id)
                logger.bind(event="call_refused_busy").warning("too many calls: WhatsApp call declined")
                return JSONResponse({"status": "busy"})

            async def on_connection(connection):
                await calls.start_call(connection, caller_number=caller)

            await client.handle_webhook_request(request_model, on_connection, raw_body=raw, sha256_signature=signature)
        except ImportError:
            logger.bind(event="whatsapp_unavailable").error("WhatsApp calling needs aiortc (requirements.txt)")
            return Response(status_code=503)
        except Exception as e:  # noqa: BLE001
            logger.bind(event="whatsapp_event_failed", error=type(e).__name__).warning("WhatsApp call event failed")
            return Response(status_code=500)
        return JSONResponse({"status": "ok"})

    app.state.whatsapp_calls = calls
    logger.bind(event="whatsapp_on").info("WhatsApp calling on")
    return calls
