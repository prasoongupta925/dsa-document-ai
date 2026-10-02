# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Outbound "call this applicant": the provider rings the customer, the bot talks when they answer.

HTTP:  POST /calls/outbound   Authorization: Bearer <Cognito ID token of the IDP user pool>
       {"to": "+9198XXXXXXXX", "applicant": "Sneha Kulkarni", "language": "hi", "provider": "plivo"}
CLI:   python outbound.py --to +9198XXXXXXXX --applicant "Sneha Kulkarni" [--language hi] [--provider exotel]

Guard rails (checked before any provider API call):
- the number must be on OUTBOUND_ALLOWED_TO (the demo's team phones with signed consent);
- only inside CALL_WINDOW on CALL_DAYS, India time (default 10:00-19:00, Mon-Sat);
- at most OUTBOUND_MAX_ATTEMPTS_PER_DAY calls per number per day (per server process);
- the provider's credentials must be configured.
The applicant's name and language travel to the call only inside a sealed
(encrypted, 15-minute) token in the answer/stream URL.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import sys
from datetime import datetime

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from auth import AuthError, CognitoVerifier, authenticate, token_from_authorization
from config import Settings, exotel_url_key
from prompt import IST, short_lang
from security import seal

_E164_IN = re.compile(r"^\+91\d{10}$")
_attempts: dict[str, int] = {}


class DialRefused(Exception):
    """The call is not allowed (guard) or cannot be placed (configuration)."""


def _number_key(number: str, when: datetime) -> str:
    return hashlib.sha256(f"{number}|{when:%Y-%m-%d}".encode()).hexdigest()[:24]


def check_dial(settings: Settings, to: str, now: datetime | None = None, count: bool = True) -> None:
    now = (now or datetime.now(IST)).astimezone(IST)
    if not _E164_IN.match(to or ""):
        raise DialRefused("the number must be an Indian number in E.164 form, e.g. +9198XXXXXXXX")
    if not settings.outbound_allowed_to:
        raise DialRefused("OUTBOUND_ALLOWED_TO is empty: no number may be called")
    if to not in settings.outbound_allowed_to:
        raise DialRefused("the number is not on OUTBOUND_ALLOWED_TO")
    start, end = settings.call_window
    if now.weekday() not in settings.call_days or not (start <= now.time() < end):
        raise DialRefused(f"outside the calling window ({start:%H:%M}-{end:%H:%M} IST)")
    key = _number_key(to, now)
    if _attempts.get(key, 0) >= settings.outbound_max_attempts_per_day:
        raise DialRefused("this number has had the maximum number of calls today")
    if count:
        _attempts[key] = _attempts.get(key, 0) + 1


def call_context(settings: Settings, applicant: str, language: str) -> str:
    applicant = re.sub(r"[^\w .'-]", "", applicant or "", flags=re.UNICODE).strip()[:80]
    return seal({"a": applicant or None, "l": short_lang(language)}, settings.phone_url_key)


def plivo_call_body(settings: Settings, to: str, ctx: str) -> dict:
    host = settings.public_host
    return {
        "from": settings.plivo_number,
        "to": to,
        "answer_url": f"https://{host}/phone/plivo/answer?ctx={ctx}",
        "answer_method": "POST",
        "hangup_url": f"https://{host}/phone/plivo/status",
        "hangup_method": "POST",
        "time_limit": settings.call_max_s + 30,
        "ring_timeout": 30,
    }


def exotel_call_form(settings: Settings, to: str, ctx: str) -> dict:
    host, key = settings.public_host, exotel_url_key(settings.phone_url_key)  # never PHONE_URL_KEY itself
    return {
        "From": to,
        "CallerId": settings.exotel_exophone,
        "StreamUrl": f"wss://{host}/phone/exotel/ws/{key}/{ctx}?sample-rate=16000",
        "StreamType": "bidirectional",
        "TimeLimit": str(settings.call_max_s + 30),
        "StatusCallback": f"https://{host}/phone/exotel/status/{key}",
    }


async def dial(settings: Settings, to: str, applicant: str, language: str = "hi", provider: str = "", http=None) -> dict:
    """Place the call. Returns {"provider", "request_id"}; raises DialRefused or RuntimeError."""
    provider = provider or ("plivo" if settings.plivo_outbound_enabled else "exotel")
    if provider == "plivo" and not settings.plivo_outbound_enabled:
        raise DialRefused("Plivo is not configured (PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN, PLIVO_NUMBER, PHONE_URL_KEY, PUBLIC_HOST)")
    if provider == "exotel" and not settings.exotel_outbound_enabled:
        raise DialRefused("Exotel is not configured (EXOTEL_SID, EXOTEL_API_KEY, EXOTEL_API_TOKEN, EXOTEL_EXOPHONE, PHONE_URL_KEY, PUBLIC_HOST)")
    if provider not in ("plivo", "exotel"):
        raise DialRefused("provider must be plivo or exotel")
    check_dial(settings, to)
    ctx = call_context(settings, applicant, language)
    owns_http = http is None
    http = http or aiohttp.ClientSession()
    try:
        if provider == "plivo":
            url = f"{settings.plivo_api_base}/v1/Account/{settings.plivo_auth_id}/Call/"
            async with http.post(
                url, json=plivo_call_body(settings, to, ctx),
                auth=aiohttp.BasicAuth(settings.plivo_auth_id, settings.plivo_auth_token),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                status = resp.status
                data = await resp.json(content_type=None) if status < 500 else {}
            if status not in (200, 201, 202):
                raise RuntimeError(f"Plivo refused the call: HTTP {status}")
            request_id = (data or {}).get("request_uuid", "")
        else:
            url = f"{settings.exotel_api_base}/v1/Accounts/{settings.exotel_sid}/Calls/connect"
            async with http.post(
                url, data=exotel_call_form(settings, to, ctx),
                auth=aiohttp.BasicAuth(settings.exotel_api_key, settings.exotel_api_token),
                headers={"Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                status = resp.status
                data = await resp.json(content_type=None) if status < 500 else {}
            if status >= 300:
                raise RuntimeError(f"Exotel refused the call: HTTP {status}")
            request_id = ((data or {}).get("Call") or {}).get("Sid", "")
    finally:
        if owns_http:
            await http.close()
    logger.bind(event="outbound_call", provider=provider, request_id=request_id).info("outbound call placed")
    return {"provider": provider, "request_id": request_id}


def register_outbound_routes(app: FastAPI, settings: Settings, verifier: CognitoVerifier | None) -> None:
    @app.post("/calls/outbound")
    async def outbound(request: Request):
        try:
            principal = await authenticate(verifier, token_from_authorization(request.headers.get("authorization")))
        except AuthError:
            return JSONResponse({"detail": "sign in first"}, status_code=401)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"detail": "JSON body required"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"detail": "JSON object required"}, status_code=400)
        try:
            result = await dial(
                settings, str(body.get("to", "")), str(body.get("applicant", "")),
                str(body.get("language", "hi")), str(body.get("provider", "")),
            )
        except DialRefused as e:
            logger.bind(event="outbound_refused", sub=principal.sub).info("outbound call refused")
            return JSONResponse({"detail": str(e)}, status_code=409)
        except Exception as e:  # noqa: BLE001
            logger.bind(event="outbound_failed", error=type(e).__name__).warning("outbound call failed")
            return JSONResponse({"detail": "the provider did not accept the call"}, status_code=502)
        return JSONResponse(result, status_code=202)


def _cli(argv: list[str] | None = None) -> int:
    from config import load_settings
    from logging_setup import setup_logging

    parser = argparse.ArgumentParser(description="Ring an applicant; the voice bot talks when they answer.")
    parser.add_argument("--to", required=True, help="+91 number in E.164 form (must be on OUTBOUND_ALLOWED_TO)")
    parser.add_argument("--applicant", required=True, help="applicant's full name, e.g. 'Sneha Kulkarni'")
    parser.add_argument("--language", default="hi", choices=["hi", "en", "mr"])
    parser.add_argument("--provider", default="", choices=["", "plivo", "exotel"])
    args = parser.parse_args(argv)
    settings = load_settings()
    setup_logging(settings.log_level, as_json=False)
    try:
        result = asyncio.run(dial(settings, args.to, args.applicant, args.language, args.provider))
    except DialRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"failed: {e}", file=sys.stderr)
        return 1
    print(f"{result['provider']} call placed: {result['request_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
