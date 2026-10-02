# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Structured logs (one JSON object per line) with no PAN, Aadhaar, phone, e-mail or tokens.

- Our own code never logs transcripts, tool arguments or tool results: it logs
  events with ids, counts and status codes only.
- Pipecat logs conversation text (TTS input, LLM context, function arguments) at
  DEBUG/TRACE. Those levels are dropped for third-party modules unless
  LOG_THIRD_PARTY_DEBUG=true, and every message is passed through ``redact``.
- Uvicorn's access log is replaced: it would print the WebSocket query string,
  which carries the Cognito token. Uvicorn still logs each WebSocket handshake
  with its path: the secrets in phone URLs (the Exotel key and call context,
  Plivo's sealed stream/transfer tokens, ?ctx=) are masked like everything else.
- On the server (deploy/), journald keeps these logs at most 7 days.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

from loguru import logger

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Secrets in phone URL paths and queries (phone_routes.py, outbound.py).
    (re.compile(r"(/phone/exotel/(?:ws|status)/)[^\s?\"']+"), r"\1[KEY]"),
    (re.compile(r"(/phone/plivo/(?:ws|transfer)/)[^\s?/\"']+"), r"\1[TOKEN]"),
    (re.compile(r"([?&]ctx=)[^&\s\"']+"), r"\1[REDACTED]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"), "[TOKEN]"),
    (re.compile(r"(?i)\b(bearer|token|signature|secret|password|auth_token)(\s*[=:]\s*)[^\s,&'\"]+"), r"\1\2[REDACTED]"),
    (re.compile(r"\b[A-Za-z]{5}\d{4}[A-Za-z]\b"), "[PII:PAN]"),
    # 12 digits, optionally grouped 4-4-4 with one kind of separator; not part of a longer id (UUIDs stay intact)
    (re.compile(r"(?<![\w-])\d{4}([ -]?)\d{4}\1\d{4}(?![\w-])"), "[PII:AADHAAR]"),
    (re.compile(r"(?<![\w+])(\+?91[ -]?)?[6-9]\d{9}(?!\d)"), "[PII:PHONE]"),
    (re.compile(r"(?<![\w+])\+?\d{11,15}(?!\d)"), "[PII:PHONE]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[PII:EMAIL]"),
]


def redact(text: str) -> str:
    """Mask identifiers that must never reach a log line."""
    if not text:
        return text
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


_OWN_MODULES = (
    "main",
    "bot",
    "tools",
    "idp_client",
    "recording",
    "phone_routes",
    "whatsapp_routes",
    "outbound",
    "auth",
    "llm",
    "speech",
    "serializers",
    "config",
    "security",
    "reminders",
    "prompt",
    "logging_setup",
)


def _is_own(name: str | None) -> bool:
    return bool(name) and name.split(".")[0] in _OWN_MODULES


def _make_filter(level_no: int, third_party_debug: bool):
    def _filter(record) -> bool:
        if record["level"].no >= max(level_no, logging.INFO):
            return True
        if record["level"].no < level_no:
            return False
        # Below INFO: our modules follow LOG_LEVEL; third-party DEBUG carries conversation text.
        return _is_own(record["name"]) or third_party_debug

    return _filter


def _patch(record) -> None:
    record["message"] = redact(record["message"])
    extra = record["extra"]
    for key, value in list(extra.items()):
        if isinstance(value, str):
            extra[key] = redact(value)
    if record["exception"] is not None:
        # Keep the type and the place, drop the message (it may quote a payload).
        exc_type, _, tb = record["exception"]
        record["extra"]["exc_type"] = getattr(exc_type, "__name__", str(exc_type))
        if tb is not None:
            while tb.tb_next is not None:
                tb = tb.tb_next
            record["extra"]["exc_at"] = f"{tb.tb_frame.f_code.co_filename.rsplit('/', 1)[-1]}:{tb.tb_lineno}"


def _json_sink(stream):
    def sink(message) -> None:
        record = message.record
        payload = {
            "ts": record["time"].astimezone(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record["level"].name,
            "logger": record["name"],
            "msg": record["message"],
        }
        for key, value in record["extra"].items():
            if key not in payload:
                payload[key] = value if isinstance(value, (int, float, bool)) or value is None else str(value)
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        stream.flush()

    return sink


class _InterceptHandler(logging.Handler):
    """Route stdlib logging (uvicorn, botocore, aiortc) into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.patch(lambda r: r.update(name=record.name)).opt(exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logging(level: str = "INFO", as_json: bool = True, stream=None) -> None:
    stream = stream or sys.stderr
    level_no = logger.level(level.upper()).no
    third_party_debug = os.environ.get("LOG_THIRD_PARTY_DEBUG", "").lower() == "true"
    logger.remove()
    logger.configure(patcher=_patch)
    if as_json:
        logger.add(_json_sink(stream), level=0, filter=_make_filter(level_no, third_party_debug), backtrace=False, diagnose=False)
    else:
        logger.add(
            stream,
            level=0,
            filter=_make_filter(level_no, third_party_debug),
            backtrace=False,
            diagnose=False,
            format="{time:HH:mm:ss.SSS} {level: <7} {name}: {message} {extra}",
        )
    logging.basicConfig(handlers=[_InterceptHandler()], level=logging.INFO, force=True)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = [_InterceptHandler()]
        logging.getLogger(name).propagate = False
    # The access log prints query strings (the browser token): never enable it.
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("botocore", "boto3", "urllib3", "aiobotocore", "aioice", "aiortc", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
