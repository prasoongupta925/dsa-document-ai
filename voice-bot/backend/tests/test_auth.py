# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Cognito ID tokens of the IDP user pool: signed locally with a test RSA key and a fake JWKS."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from auth import SUBPROTOCOL, AuthError, CognitoVerifier, token_from_authorization, token_from_websocket

POOL = "ap-south-1_EXAMPLE01"
ISSUER = f"https://cognito-idp.ap-south-1.amazonaws.com/{POOL}"
CLIENT = "1exampleclientid0abcdefgh2"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJwks:
    """Stands in for jwt.PyJWKClient: returns our public key for kid 'k1' only."""

    def get_signing_key_from_jwt(self, token):
        header = jwt.get_unverified_header(token)
        if header.get("kid") != "k1":
            raise jwt.PyJWKClientError("Unable to find a signing key that matches")

        class _Key:
            key = KEY.public_key()

        return _Key()


def make_token(key=KEY, kid="k1", alg="RS256", **claims):
    now = int(time.time())
    body = {
        "sub": "0f1c-user",
        "aud": CLIENT,
        "iss": ISSUER,
        "token_use": "id",
        "cognito:username": "asha.verma",
        "email": "asha@example.com",
        "iat": now,
        "exp": now + 3600,
        "auth_time": now,
    }
    body.update(claims)
    body = {k: v for k, v in body.items() if v is not None}
    return jwt.encode(body, key, algorithm=alg, headers={"kid": kid})


@pytest.fixture
def verifier():
    return CognitoVerifier("ap-south-1", POOL, (CLIENT, "voice-client"), jwks_client=FakeJwks())


async def test_valid_id_token_is_accepted(verifier):
    principal = await verifier.verify(make_token())
    assert principal.sub == "0f1c-user" and principal.username == "asha.verma"
    assert "email" not in principal.__dict__


async def test_second_allowed_client_is_accepted(verifier):
    assert (await verifier.verify(make_token(aud="voice-client"))).sub == "0f1c-user"


@pytest.mark.parametrize(
    "token_kwargs",
    [
        {"aud": "some-other-client"},
        {"iss": "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_Other"},
        {"iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Pool"},
        {"exp": int(time.time()) - 120},
        {"token_use": "access"},
        {"token_use": None},
        {"key": OTHER_KEY},
        {"kid": "unknown"},
    ],
)
async def test_bad_tokens_are_refused(verifier, token_kwargs):
    kwargs = dict(token_kwargs)
    key = kwargs.pop("key", KEY)
    kid = kwargs.pop("kid", "k1")
    with pytest.raises(AuthError):
        await verifier.verify(make_token(key=key, kid=kid, **kwargs))


async def test_unsigned_and_malformed_tokens_are_refused(verifier):
    unsigned = jwt.encode({"sub": "x", "aud": CLIENT, "iss": ISSUER, "token_use": "id", "exp": int(time.time()) + 60}, None, algorithm="none")
    for token in (unsigned, "", "abc", "a.b", "x" * 9000):
        with pytest.raises(AuthError):
            await verifier.verify(token)
    hs = jwt.encode({"sub": "x"}, "s" * 40, algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(AuthError):
        await verifier.verify(hs)


def test_token_from_websocket_prefers_the_subprotocol():
    headers = {"sec-websocket-protocol": f"{SUBPROTOCOL}, auth.TOKEN123"}
    assert token_from_websocket({"token": "from-query"}, headers) == ("TOKEN123", SUBPROTOCOL)
    assert token_from_websocket({"token": "from-query"}, {}) == ("from-query", None)
    assert token_from_websocket({}, {"sec-websocket-protocol": SUBPROTOCOL}) == ("", SUBPROTOCOL)


def test_token_from_authorization():
    assert token_from_authorization("Bearer abc.def.ghi") == "abc.def.ghi"
    assert token_from_authorization("bearer  xyz ") == "xyz"
    assert token_from_authorization("Basic dXNlcjpwYXNz") == ""
    assert token_from_authorization(None) == ""
