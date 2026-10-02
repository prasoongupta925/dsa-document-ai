# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import hashlib
import hmac
import time

import pytest

import security

KEY = "k" * 40

# Signatures computed with the plivo Python SDK 4.63.0 (plivo.utils.signature_v3.get_signature_v3).
PLIVO_VECTORS = [
    (
        "POST",
        "https://dxxxx.cloudfront.net/phone/plivo/answer",
        {"CallUUID": "12345678-1234-1234-1234-123456789abc", "From": "+91" "2200000000", "To": "+91" "9800000001",
         "Direction": "inbound", "ForwardedFrom": ""},
        "12345678901234567890",
        "fake-token",
        "LLnUns4/x4YgO8jrrPbZUou2WYXpVE4J82enUeoHFR8=",
    ),
    (
        "POST",
        "https://dxxxx.cloudfront.net/phone/plivo/answer?ctx=abc.def_ghi-",
        {"CallUUID": "c1", "Direction": "outbound"},
        "nonce-2",
        "tok2",
        "t5IHTMIaERILYoM5mk+PLpO67wth3GRbCNLJvJag8QI=",
    ),
    ("POST", "https://dxxxx.cloudfront.net/phone/plivo/status", {}, "n3", "tok3", "/mHT0eitBOCa9n/+cPgLhYB+MH0baWlA0gfuUiqvxR0="),
    (
        "GET",
        "https://dxxxx.cloudfront.net/phone/plivo/answer?CallUUID=c9&From=%2B9122&b=2&a=1",
        {},
        "n4",
        "tok4",
        "/7w1h5QvPhjeRq/XSwlXhP/PrDMGJ+GAzP9xKEiABlo=",
    ),
]


@pytest.mark.parametrize("method,url,params,nonce,token,expected", PLIVO_VECTORS)
def test_plivo_v3_signature_matches_the_sdk(method, url, params, nonce, token, expected):
    assert security.plivo_v3_signature(method, url, nonce, token, params if method == "POST" else None) == expected
    assert security.valid_plivo_signature(method, url, nonce, token, expected, params)
    # Plivo may send several comma-separated signatures (rotated tokens).
    assert security.valid_plivo_signature(method, url, nonce, token, f"bogus=,{expected}", params)


def test_plivo_signature_rejects_changes():
    method, url, params, nonce, token, good = PLIVO_VECTORS[0]
    assert not security.valid_plivo_signature(method, url, nonce, "other-token", good, params)
    assert not security.valid_plivo_signature(method, url.replace("dxxxx", "internal-nlb"), nonce, token, good, params)
    assert not security.valid_plivo_signature(method, url, nonce, token, good, {**params, "To": "+91" "9811111111"})
    assert not security.valid_plivo_signature(method, url, "other-nonce", token, good, params)
    assert not security.valid_plivo_signature(method, url, nonce, token, "", params)
    assert not security.valid_plivo_signature(method, url, "", token, good, params)


def test_sealed_tokens_round_trip_and_hide_the_payload():
    token = security.seal({"call": "abc", "a": "Sneha Kulkarni"}, KEY)
    assert "Sneha" not in token and "abc" not in token
    assert security.unseal(token, KEY, ttl_s=60) == {"call": "abc", "a": "Sneha Kulkarni"}
    assert all(c.isalnum() or c in "-_=" for c in token)  # safe in a URL path


def test_sealed_tokens_refuse_tampering_other_keys_and_age(monkeypatch):
    token = security.seal({"call": "abc"}, KEY)
    assert security.unseal(token, "x" * 40, ttl_s=60) is None
    assert security.unseal(token[:-4] + "AAAA", KEY, ttl_s=60) is None
    assert security.unseal("", KEY, ttl_s=60) is None
    assert security.unseal("not-a-token", KEY, ttl_s=60) is None
    old = time.time() - 3600
    monkeypatch.setattr(time, "time", lambda: old)
    stale = security.seal({"call": "abc"}, KEY)
    monkeypatch.undo()
    assert security.unseal(stale, KEY, ttl_s=300) is None


def test_meta_signature():
    body = b'{"object":"whatsapp_business_account"}'
    sig = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    assert security.valid_meta_signature(body, sig, "app-secret")
    assert not security.valid_meta_signature(body + b" ", sig, "app-secret")
    assert not security.valid_meta_signature(body, sig, "other")
    assert not security.valid_meta_signature(body, None, "app-secret")
    assert not security.valid_meta_signature(body, sig, "")


def test_same_secret():
    assert security.same_secret("abc", "abc")
    assert not security.same_secret("abc", "abd")
    assert not security.same_secret("", "")
