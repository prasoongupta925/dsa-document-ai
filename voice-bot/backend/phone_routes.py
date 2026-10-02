# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Phone calls through Plivo or Exotel (Indian numbers), as WebSocket media streams.

Switched on when PHONE_URL_KEY (32+ random characters) and PUBLIC_HOST (the
voice CloudFront domain) are set; Plivo also needs PLIVO_AUTH_ID/TOKEN.
Built and tested with fake provider messages; never run against a live account.

Plivo (number -> XML Application):
  POST /phone/plivo/answer          answer URL: checks X-Plivo-Signature-V3, returns <Stream> XML
  WS   /phone/plivo/ws/{token}      the call audio; the token is sealed by /answer and bound to the CallUUID
  POST /phone/plivo/status          hangup URL: logs cause and duration only
  POST /phone/plivo/transfer/{tok}  aleg_url of a transfer: <Dial> to TELECALLER_NUMBER
Exotel (ExoPhone -> App Bazaar flow -> Voicebot applet, or the Calls/connect API):
  WS   /phone/exotel/ws/{key}                       inbound: wss://HOST/phone/exotel/ws/<key>?sample-rate=16000
  WS   /phone/exotel/ws/{key}/{ctx}                 outbound (sealed call context from outbound.py)
  POST /phone/exotel/status/{key}                   StatusCallback: logs status and duration only
  <key> = config.exotel_url_key(PHONE_URL_KEY), not PHONE_URL_KEY itself: Exotel users see these URLs,
  and PHONE_URL_KEY seals the call tokens (deploy/secrets.sh exotel-urls prints them).

Pipecat 1.3.0 gotchas handled here (measured locally by the research pass):
1. ExotelFrameSerializer(stream_sid, call_sid, params): the online docs' keyword names raise TypeError.
2. Exotel needs >= 100 ms chunks in multiples of 320 bytes: 16 kHz on both sides
   (?sample-rate=16000, exotel_sample_rate=16000) and audio_out_10ms_chunks=10 -> 3,200-byte chunks.
3. The stock Plivo resampler (soxr VHQ) holds back ~52 ms each way, plus ~100 ms at the start: the bot's
   voice uses QQ (0 ms; Polly renders 8 kHz for Plivo calls, so QQ has nothing above 4 kHz to alias:
   77.7 dB SNR vs 79.0 dB for VHQ, measured) and the caller's audio MQ (~26 ms; QQ's images would be
   only 18 dB down for Transcribe). PLIVO_RESAMPLER / PLIVO_INPUT_RESAMPLER override.
4. parse_telephony_websocket drops Plivo's extraHeaders: call context travels in a sealed token in the path.
5. Plivo signs the public URL (not the load balancer's): rebuilt from PUBLIC_HOST.
6. Form bodies are parsed with the stdlib (blank values kept: Plivo signs them too).
Denied requests get 401, not 403: CloudFront's SPA error rule turns 403/404 into index.html.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import parse_qs
from xml.sax.saxutils import escape, quoteattr

import aiohttp
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response
from loguru import logger
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.exotel import ExotelFrameSerializer

from bot import BotDeps, new_call_id, run_call, websocket_transport
from config import exotel_url_key
from prompt import short_lang
from security import same_secret, seal, unseal, valid_plivo_signature
from serializers import PlivoBotSerializer
from tools import CallSession

EXOTEL_RATE = 16000
STREAM_TOKEN_TTL_S = 300
CONTEXT_TTL_S = 900
TRANSFER_TTL_S = 600
TRANSFER_DELAY_S = 6.0  # let the bot finish "connecting you to our telecaller"
_UUID = re.compile(r"^[0-9A-Za-z-]{8,64}$")


async def form_params(request: Request) -> dict[str, str]:
    if request.method != "POST":
        return dict(request.query_params)
    body = (await request.body()).decode("utf-8", "replace")
    return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}


def public_url(request: Request, public_host: str) -> str:
    query = request.url.query
    return f"https://{public_host}{request.url.path}" + (f"?{query}" if query else "")


def plivo_request_ok(request: Request, params: dict, settings) -> bool:
    if not settings.plivo_validate_signature:
        return True
    return valid_plivo_signature(
        request.method,
        public_url(request, settings.public_host),
        request.headers.get("X-Plivo-Signature-V3-Nonce", ""),
        settings.plivo_auth_token,
        request.headers.get("X-Plivo-Signature-V3", ""),
        params if request.method == "POST" else None,
    )


def stream_xml(ws_url: str, max_s: int) -> str:
    # contentType is required: Plivo defaults to L16, Pipecat 1.3.0's Plivo serializer speaks mu-law only.
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'<Stream bidirectional="true" keepCallAlive="true" streamTimeout="{int(max_s)}" '
        f'contentType="audio/x-mulaw;rate=8000">{escape(ws_url)}</Stream></Response>'
    )


def dial_xml(number: str, caller_id: str) -> str:
    caller = f" callerId={quoteattr(caller_id)}" if caller_id else ""
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Dial{caller} timeout="30"><Number>{escape(number)}</Number></Dial></Response>'


async def transfer_or_apologise(settings, session, serializer, aleg_url: str) -> int:
    """Wait for the bot's line, ask Plivo to transfer; on failure undo the no-hang-up flag and apologise."""
    status = await plivo_transfer_api(settings, session.provider_call_id, aleg_url)
    if status in (200, 201, 202, 204):
        return status
    serializer.transferred = False
    session.transfer_requested = False
    session.ended_reason = None
    if session.speak is not None:
        from prompt import TRANSFER_FAILED

        await session.speak(TRANSFER_FAILED.get(session.language, TRANSFER_FAILED["hi"]))
    return status


async def plivo_transfer_api(settings, call_uuid: str, aleg_url: str, delay_s: float = TRANSFER_DELAY_S) -> int:
    """Ask Plivo to move the caller's leg to aleg_url (our <Dial> XML). Returns the HTTP status."""
    await asyncio.sleep(delay_s)
    url = f"{settings.plivo_api_base}/v1/Account/{settings.plivo_auth_id}/Call/{call_uuid}/"
    body = {"legs": "aleg", "aleg_url": aleg_url, "aleg_method": "POST"}
    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(
                url, json=body, auth=aiohttp.BasicAuth(settings.plivo_auth_id, settings.plivo_auth_token),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                status = resp.status
    except Exception as e:  # noqa: BLE001
        logger.bind(event="plivo_transfer", error=type(e).__name__).warning("plivo transfer failed")
        return 0
    logger.bind(event="plivo_transfer", status=status).info("plivo transfer requested")
    return status


def register_phone_routes(app: FastAPI, deps: BotDeps) -> bool:
    settings = deps.settings
    if not settings.phone_enabled:
        logger.bind(event="phone_off").info("phone routes off: set PHONE_URL_KEY (32+ chars) and PUBLIC_HOST")
        return False
    key = settings.phone_url_key  # seals call tokens; never in a URL
    exotel_key = exotel_url_key(key)  # the secret in Exotel's URLs
    transfer_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------- Plivo
    @app.api_route("/phone/plivo/answer", methods=["GET", "POST"])
    async def plivo_answer(request: Request):
        if not settings.plivo_enabled:
            return Response(status_code=401)
        params = await form_params(request)
        if not plivo_request_ok(request, params, settings):
            logger.bind(event="plivo_denied", route="answer").warning("bad Plivo signature")
            return Response(status_code=401)
        call_uuid = params.get("CallUUID", "")
        if not _UUID.match(call_uuid):
            return Response(status_code=400)
        ctx = unseal(request.query_params.get("ctx", ""), key, CONTEXT_TTL_S) or {}
        direction = params.get("Direction", "")[:16]
        token = seal(
            {
                "call": call_uuid,
                "dir": direction,
                "a": ctx.get("a"),
                "l": ctx.get("l"),
                # Inbound caller ID, used only to verify the caller against the file's mobile (sealed, never logged).
                "f": params.get("From", "")[:20] if direction != "outbound" and not ctx else None,
            },
            key,
        )
        ws_url = f"wss://{settings.public_host}/phone/plivo/ws/{token}"
        logger.bind(event="plivo_answer", call=call_uuid, outbound=bool(ctx)).info("plivo call answered")
        return Response(content=stream_xml(ws_url, settings.call_max_s), media_type="application/xml")

    @app.api_route("/phone/plivo/status", methods=["GET", "POST"])
    async def plivo_status(request: Request):
        if not settings.plivo_enabled:
            return Response(status_code=401)
        params = await form_params(request)
        if not plivo_request_ok(request, params, settings):
            return Response(status_code=401)
        logger.bind(
            event="plivo_status", call=params.get("CallUUID", "")[:64], cause=params.get("HangupCause", "")[:64],
            seconds=params.get("Duration", "")[:8],
        ).info("plivo call status")
        return Response(status_code=200)

    @app.api_route("/phone/plivo/transfer/{token}", methods=["GET", "POST"])
    async def plivo_transfer_xml(request: Request, token: str):
        if not settings.plivo_enabled:
            return Response(status_code=401)
        params = await form_params(request)
        claims = unseal(token, key, TRANSFER_TTL_S)
        if (
            not claims
            or not plivo_request_ok(request, params, settings)
            or not settings.telecaller_number
            or (params.get("CallUUID") and params.get("CallUUID") != claims.get("call"))
        ):
            return Response(status_code=401)
        return Response(content=dial_xml(settings.telecaller_number, settings.plivo_number), media_type="application/xml")

    @app.websocket("/phone/plivo/ws/{token}")
    async def plivo_ws(websocket: WebSocket, token: str):
        claims = unseal(token, key, STREAM_TOKEN_TTL_S)
        if not claims or not settings.plivo_enabled:
            await websocket.close(code=4001)
            return
        await websocket.accept()
        try:
            transport_type, call_data = await asyncio.wait_for(parse_telephony_websocket(websocket), timeout=10)
        except Exception:  # noqa: BLE001 - timeout, closed socket or garbage
            await _close(websocket)
            return
        if transport_type != "plivo" or call_data.get("call_id") != claims.get("call") or not call_data.get("stream_id"):
            logger.bind(event="plivo_denied", route="ws").warning("stream does not match its token")
            await _close(websocket, 4001)
            return
        serializer = PlivoBotSerializer(
            stream_id=call_data["stream_id"],
            call_id=call_data["call_id"],
            auth_id=settings.plivo_auth_id,
            auth_token=settings.plivo_auth_token,
            resampler_quality=settings.plivo_resampler,
            input_resampler_quality=settings.plivo_input_resampler,
            api_base=settings.plivo_api_base,
        )
        session = CallSession(
            call_id=new_call_id(),
            channel="plivo",
            language=short_lang(claims.get("l") or settings.default_language),
            idp_user="voice-bot-phone",
            applicant_hint=claims.get("a"),
            provider_call_id=call_data["call_id"],
            outbound=claims.get("dir") == "outbound" or bool(claims.get("a")),
            caller_number=claims.get("f"),
        )

        async def transfer(s: CallSession) -> dict:
            if not settings.telecaller_number:
                return {"transferred": False, "say": "No telecaller line is set up. Say a telecaller will call back."}
            serializer.transferred = True  # the bot ending must not hang up the transferred call
            aleg = f"https://{settings.public_host}/phone/plivo/transfer/{seal({'call': s.provider_call_id}, key)}"
            task = asyncio.create_task(transfer_or_apologise(settings, s, serializer, aleg))
            transfer_tasks.add(task)
            task.add_done_callback(transfer_tasks.discard)
            return {"transferred": True, "end_stream": False}

        await _run_with_slot(deps, websocket, lambda: run_call(websocket_transport(websocket, serializer), session, deps, on_transfer=transfer))

    # ------------------------------------------------------------- Exotel
    async def exotel_call(websocket: WebSocket, url_key: str, ctx: str | None):
        if not same_secret(url_key, exotel_key):
            await websocket.close(code=4001)
            return
        claims = unseal(ctx, key, CONTEXT_TTL_S) if ctx else {}
        if ctx and not claims:
            await websocket.close(code=4001)
            return
        await websocket.accept()
        try:
            transport_type, call_data = await asyncio.wait_for(parse_telephony_websocket(websocket), timeout=10)
        except Exception:  # noqa: BLE001 - timeout, closed socket or garbage
            await _close(websocket)
            return
        if transport_type != "exotel" or not call_data.get("stream_id"):
            await _close(websocket, 4001)
            return
        serializer = ExotelFrameSerializer(
            stream_sid=call_data["stream_id"],
            call_sid=call_data.get("call_id"),
            params=ExotelFrameSerializer.InputParams(exotel_sample_rate=EXOTEL_RATE),
        )
        session = CallSession(
            call_id=new_call_id(),
            channel="exotel",
            language=short_lang((claims or {}).get("l") or settings.default_language),
            idp_user="voice-bot-phone",
            applicant_hint=(claims or {}).get("a"),
            provider_call_id=call_data.get("call_id"),
            outbound=bool(claims),
            caller_number=None if claims else (call_data.get("from") or None),
        )

        async def transfer(s: CallSession) -> dict:
            if s.outbound:  # Calls/connect has no flow to hand the call to
                return {"transferred": False, "say": "This call cannot be transferred. Say a telecaller will call back."}
            # Inbound: closing the stream returns the call to the App Bazaar flow (next applet: Connect).
            return {"transferred": True, "end_stream": True}

        await _run_with_slot(
            deps, websocket,
            lambda: run_call(websocket_transport(websocket, serializer, audio_out_10ms_chunks=10), session, deps, on_transfer=transfer),
        )

    @app.websocket("/phone/exotel/ws/{url_key}")
    async def exotel_ws_inbound(websocket: WebSocket, url_key: str):
        await exotel_call(websocket, url_key, None)

    @app.websocket("/phone/exotel/ws/{url_key}/{ctx}")
    async def exotel_ws_outbound(websocket: WebSocket, url_key: str, ctx: str):
        await exotel_call(websocket, url_key, ctx)

    @app.api_route("/phone/exotel/status/{url_key}", methods=["GET", "POST"])
    async def exotel_status(request: Request, url_key: str):
        if not same_secret(url_key, exotel_key):
            return Response(status_code=401)
        params = await form_params(request)
        logger.bind(
            event="exotel_status", call=params.get("CallSid", "")[:64], status=params.get("Status", "")[:32],
            seconds=params.get("ConversationDuration", params.get("Duration", ""))[:8],
        ).info("exotel call status")
        return Response(status_code=200)

    logger.bind(event="phone_on", plivo=settings.plivo_enabled, exotel=settings.exotel_enabled).info("phone routes on")
    return True


async def _run_with_slot(deps: BotDeps, websocket: WebSocket, start) -> None:
    """Run the call if a slot is free; otherwise close the stream (the provider ends the call)."""
    if not deps.slots.take():
        logger.bind(event="call_refused_busy", active=deps.slots.active).warning("too many calls: phone call refused")
        await _close(websocket, 1013)
        return
    try:
        await start()
    finally:
        deps.slots.give()


async def _close(websocket: WebSocket, code: int = 1011) -> None:
    try:
        await websocket.close(code=code)
    except Exception:  # noqa: BLE001
        pass
