# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Cognito ID-token check for the browser WebSocket and the outbound-call API.

The bot accepts the same logins as the IDP web app: tokens issued by the IDP's
user pool (UserIdentity...) in ap-south-1, for one of the allowed app clients.
Checked: RS256 signature against the pool's JWKS, issuer, audience (app client),
expiry, and token_use == "id". Only ``sub`` and the username are kept; the
e-mail address in the token is never logged.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import jwt
from loguru import logger

SUBPROTOCOL = "voicebot.v1"
_AUTH_PREFIX = "auth."


class AuthError(Exception):
    """The token is missing, malformed, expired or not from the allowed pool/client."""


@dataclass(frozen=True)
class Principal:
    sub: str
    username: str


class CognitoVerifier:
    def __init__(self, region: str, user_pool_id: str, client_ids: tuple[str, ...], jwks_client=None, leeway_s: int = 30):
        self._issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self._client_ids = tuple(client_ids)
        self._leeway = leeway_s
        # PyJWKClient caches the key set for 5 minutes; it fetches with a blocking call,
        # so verification runs in a worker thread.
        self._jwks = jwks_client or jwt.PyJWKClient(
            f"{self._issuer}/.well-known/jwks.json", cache_jwk_set=True, lifespan=300, timeout=5
        )

    def verify_sync(self, token: str) -> Principal:
        if not token or token.count(".") != 2 or len(token) > 8192:
            raise AuthError("missing or malformed token")
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=list(self._client_ids),
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "token_use"]},
            )
        except jwt.PyJWTError as e:
            raise AuthError(type(e).__name__) from None
        if claims.get("token_use") != "id":
            raise AuthError("not an ID token")
        username = str(claims.get("cognito:username") or claims.get("username") or claims["sub"])
        return Principal(sub=str(claims["sub"]), username=username)

    async def verify(self, token: str) -> Principal:
        return await asyncio.to_thread(self.verify_sync, token)


def token_from_websocket(query_params, headers) -> tuple[str, str | None]:
    """(token, subprotocol to echo). Subprotocol form: Sec-WebSocket-Protocol: voicebot.v1, auth.<jwt>.

    The query form (?token=...) is kept for the sample's frontend; prefer the subprotocol:
    query strings end up in load-balancer and proxy logs.
    """
    offered = [p.strip() for p in (headers.get("sec-websocket-protocol") or "").split(",") if p.strip()]
    for proto in offered:
        if proto.startswith(_AUTH_PREFIX):
            return proto[len(_AUTH_PREFIX):], (SUBPROTOCOL if SUBPROTOCOL in offered else None)
    echo = SUBPROTOCOL if SUBPROTOCOL in offered else None
    return (query_params.get("token") or ""), echo


def token_from_authorization(header_value: str | None) -> str:
    if not header_value or not header_value.lower().startswith("bearer "):
        return ""
    return header_value[7:].strip()


async def authenticate(verifier: CognitoVerifier | None, token: str) -> Principal:
    if verifier is None:
        raise AuthError("Cognito is not configured")
    principal = await verifier.verify(token)
    logger.bind(event="auth_ok", sub=principal.sub).info("cognito token accepted")
    return principal
