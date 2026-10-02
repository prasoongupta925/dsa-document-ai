# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Call tokens and provider webhook signatures.

- ``seal`` / ``unseal``: encrypted, signed, short-lived tokens (Fernet) for the
  call context we put in provider URLs (answer URL, stream URL). Names and phone
  numbers never appear in a URL in clear text, and nobody can forge a token
  without PHONE_URL_KEY.
- ``plivo_v3_signature`` / ``valid_plivo_signature``: Plivo's X-Plivo-Signature-V3
  check, ported from the plivo Python SDK 4.63.0 (plivo/utils/signature_v3.py,
  MIT licence), so the bot does not need the SDK.
- ``valid_meta_signature``: WhatsApp (Meta) X-Hub-Signature-256.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlparse, urlunparse

from cryptography.fernet import Fernet, InvalidToken


def _fernet(key: str) -> Fernet:
    digest = hashlib.sha256(b"voicebot-call-token:" + key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def seal(payload: dict, key: str) -> str:
    """Encrypt and sign a small dict for a URL path segment (URL-safe base64)."""
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _fernet(key).encrypt(raw).decode("ascii")


def unseal(token: str, key: str, ttl_s: int) -> dict | None:
    """The payload of a token sealed with ``key`` at most ``ttl_s`` seconds ago, else None."""
    if not token or not key or len(token) > 4096:
        return None
    try:
        raw = _fernet(key).decrypt(token.encode("ascii"), ttl=ttl_s)
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except (InvalidToken, ValueError, UnicodeError):
        return None


def same_secret(given: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


# ------------------------------------------------------------------ Plivo V3
def _sorted_query_string(params: dict[str, list[str]]) -> str:
    parts = []
    for key in sorted(params):
        value = params[key]
        if isinstance(value, list):
            parts.append("&".join(f"{key}={v}" for v in sorted(value)))
        else:
            parts.append(f"{key}={value}")
    return "&".join(parts)


def _sorted_params_string(params: dict) -> str:
    parts = []
    for key in sorted(params):
        value = params[key]
        if isinstance(value, list):
            parts.append("".join(f"{key}{v}" for v in sorted(value)))
        elif isinstance(value, dict):
            parts.append(f"{key}{_sorted_params_string(value)}")
        else:
            parts.append(f"{key}{value}")
    return "".join(parts)


def _plivo_base_url(uri: str, params: dict, empty_post_params: bool) -> str:
    parsed = urlparse(uri)
    base = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    merged = dict(params)
    merged.update({k: v for k, v in parse_qs(parsed.query, keep_blank_values=True).items()})
    query = _sorted_query_string(merged)
    if query or not empty_post_params:
        base += "?" + query
    if query and not empty_post_params:
        base += "."
    return base


def plivo_v3_signature(method: str, uri: str, nonce: str, auth_token: str, params: dict | None = None) -> str:
    """Base64 HMAC-SHA256 that Plivo puts in X-Plivo-Signature-V3."""
    params = params or {}
    if method.upper() == "GET":
        base = _plivo_base_url(uri, params, True)
    else:
        base = _plivo_base_url(uri, {}, len(params) == 0) + _sorted_params_string(params)
    mac = hmac.new(auth_token.encode("utf-8"), f"{base}.{nonce}".encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(mac).decode("ascii")


def valid_plivo_signature(
    method: str, uri: str, nonce: str, auth_token: str, header_value: str, params: dict | None = None
) -> bool:
    """True when one of the comma-separated signatures in the header matches."""
    if not (nonce and auth_token and header_value):
        return False
    expected = plivo_v3_signature(method, uri, nonce, auth_token, params if method.upper() == "POST" else None)
    return any(hmac.compare_digest(expected, sig.strip()) for sig in header_value.split(","))


# ------------------------------------------------------------------ Meta (WhatsApp)
def valid_meta_signature(raw_body: bytes, header_value: str | None, app_secret: str) -> bool:
    if not (header_value and app_secret):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    received = header_value.split("sha256=", 1)[-1].strip()
    return hmac.compare_digest(expected, received)
