# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""DSA Document AI - Voice: the all-India loan-file voice assistant (FastAPI + Pipecat 1.3.0).

Routes
  GET  /health                   liveness (no secrets, says which channels are on)
  WS   /ws                       browser call (mobile or desktop). Cognito ID token of the IDP user pool,
                                 as subprotocols ["voicebot.v1", "auth.<idToken>"] (preferred) or ?token=.
                                 Query: language=hindi|english|marathi (or hi|en|mr).
  POST /calls/outbound           ring an applicant (Plivo/Exotel), Cognito Bearer token
  /phone/...                     Plivo and Exotel media streams (phone_routes.py)
  /whatsapp                      WhatsApp Business Calling webhook (whatsapp_routes.py)

All AWS services are used in ap-south-1 only (Transcribe, Bedrock, Polly, the IDP API).
Configuration: environment variables, see .env.example.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from auth import AuthError, CognitoVerifier, authenticate, token_from_websocket
from bot import BotDeps, CallSlots, new_call_id, run_call, websocket_transport
from config import ConfigError, Settings, load_settings
from idp_client import IdpClient
from logging_setup import setup_logging
from outbound import register_outbound_routes
from phone_routes import register_phone_routes
from prompt import short_lang
from recording import sweep_stale_recordings
from serializers import BrowserJsonSerializer
from tools import CallSession
from whatsapp_routes import register_whatsapp_routes

VERSION = "2026.10.01"


def create_app(settings: Settings | None = None, deps: BotDeps | None = None, verifier: CognitoVerifier | None = None) -> FastAPI:
    settings = settings or load_settings()
    idp = deps.idp if deps else IdpClient(settings.idp_api_url, settings.region, settings.idp_caller_id, settings.idp_timeout_s)
    deps = deps or BotDeps(settings=settings, idp=idp, slots=CallSlots(settings.max_concurrent_calls))
    if verifier is None and settings.auth_enabled:
        verifier = CognitoVerifier(settings.region, settings.cognito_user_pool_id, settings.cognito_client_ids)
    slots = deps.slots

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for note in settings.warnings:
            logger.bind(event="config_note").warning(note)
        sweep_stale_recordings(settings.recordings_dir)

        async def sweeper():
            while True:
                await asyncio.sleep(600)
                sweep_stale_recordings(settings.recordings_dir)

        sweep_task = asyncio.create_task(sweeper())
        logger.bind(event="startup", version=VERSION, models=",".join(settings.model_chain), region=settings.region).info("voice bot ready")
        try:
            yield
        finally:
            sweep_task.cancel()
            calls = getattr(app.state, "whatsapp_calls", None)
            if calls is not None:
                await calls.aclose()
            await idp.aclose()

    app = FastAPI(title="DSA Document AI - Voice", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )
    app.state.settings = settings
    app.state.deps = deps
    app.state.slots = slots

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "version": VERSION,
            "active_calls": slots.active,
            "max_calls": slots.limit,
            "channels": {
                "browser": settings.auth_enabled,
                "plivo": settings.plivo_enabled,
                "exotel": settings.exotel_enabled,
                "whatsapp": settings.whatsapp_enabled,
                "outbound_plivo": settings.plivo_outbound_enabled,
                "outbound_exotel": settings.exotel_outbound_enabled,
            },
            "idp": bool(settings.idp_api_url),
            "recording": settings.recording_enabled and bool(settings.idp_api_url),
        }

    @app.websocket("/ws")
    async def browser_call(websocket: WebSocket):
        origin = websocket.headers.get("origin", "")
        if settings.allowed_origins and origin not in settings.allowed_origins:
            await websocket.close(code=4003)
            return
        token, subprotocol = token_from_websocket(websocket.query_params, websocket.headers)
        # Accept first so the browser gets a close code it can show (4001 = sign in again).
        await websocket.accept(subprotocol=subprotocol)
        try:
            principal = await authenticate(verifier, token)
        except AuthError as e:
            logger.bind(event="auth_denied", reason=str(e)[:40]).info("browser call refused")
            await websocket.close(code=4001, reason="Sign in again")
            return
        pipeline = websocket.query_params.get("pipeline", "transcribe-polly")
        if pipeline not in ("", "transcribe-polly", "mumbai"):
            await websocket.close(code=1008, reason="Unsupported pipeline")
            return
        if not slots.take():
            await websocket.close(code=1013, reason="Busy, try again")
            return
        try:
            session = CallSession(
                call_id=new_call_id(),
                channel="browser",
                language=short_lang(websocket.query_params.get("language") or settings.default_language),
                idp_user=principal.username[:128],
            )
            await run_call(websocket_transport(websocket, BrowserJsonSerializer()), session, deps)
        except Exception as e:  # noqa: BLE001
            logger.bind(event="call_crashed", error=type(e).__name__).error("browser call failed")
            try:
                await websocket.close(code=1011)
            except Exception:  # noqa: BLE001
                pass
        finally:
            slots.give()

    register_outbound_routes(app, settings, verifier)
    register_phone_routes(app, deps)
    register_whatsapp_routes(app, deps)
    return app


def main() -> int:
    load_dotenv(override=False)
    try:
        settings = load_settings()
    except ConfigError as e:
        setup_logging("INFO", as_json=True)
        logger.bind(event="config_error").error(str(e))
        return 2
    setup_logging(settings.log_level, settings.log_json)
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        access_log=False,  # it would print the browser token from the query string
        log_config=None,
        ws_max_size=1 << 20,
        proxy_headers=False,  # nothing here needs the client address; never trust X-Forwarded-*
    )
    asyncio.run(uvicorn.Server(config).serve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
