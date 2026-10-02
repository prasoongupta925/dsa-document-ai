"""Tests for app/webhook_security.py: webhook URL checks, resolved-address checks and signing.

The delivery Lambda ships a byte-identical copy of the module; the first test
keeps the two in step.
"""

import hashlib
import hmac
import socket
from pathlib import Path

import pytest

from app import webhook_security as ws

LAMBDA_COPY = Path(__file__).resolve().parents[2] / "infra" / "src" / "functions" / "webhook" / "webhook_security.py"


def test_lambda_copy_is_identical():
    assert LAMBDA_COPY.read_bytes() == Path(ws.__file__).read_bytes(), (
        "packages/infra/src/functions/webhook/webhook_security.py must be a copy of app/webhook_security.py"
    )


# ------------------------------------------------------------------ URL checks
@pytest.mark.parametrize(
    ("url", "host", "port", "target"),
    [
        ("https://crm.example.com/hooks/idp", "crm.example.com", 443, "/hooks/idp"),
        ("https://crm.example.com/hooks/idp?token=abc&x=1", "crm.example.com", 443, "/hooks/idp?token=abc&x=1"),
        ("https://crm.example.com?x=1", "crm.example.com", 443, "/?x=1"),
        ("https://crm.example.com", "crm.example.com", 443, "/"),
        ("HTTPS://CRM.Example.COM/Hook", "crm.example.com", 443, "/Hook"),
        ("https://crm.example.com./in", "crm.example.com", 443, "/in"),
        ("https://crm.example.com:8443/in", "crm.example.com", 8443, "/in"),
        ("https://crm.example.com:443/in", "crm.example.com", 443, "/in"),
        ("https://api.callvarta.co.in/v1/idp", "api.callvarta.co.in", 443, "/v1/idp"),
        ("https://8.8.8.8/x", "8.8.8.8", 443, "/x"),
        ("https://[2606:4700:4700::1111]:8443/a", "2606:4700:4700::1111", 8443, "/a"),
    ],
)
def test_accepts_public_https_urls(url, host, port, target):
    assert ws.validate_webhook_url(url) == ws.WebhookTarget(host=host, port=port, target=target)


def test_accepts_exactly_2048_characters():
    url = "https://crm.example.com/" + "a" * (ws.MAX_URL_LENGTH - len("https://crm.example.com/"))
    assert len(url) == 2048
    assert ws.validate_webhook_url(url).host == "crm.example.com"


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://crm.example.com/hook", "must use https"),
        ("ftp://crm.example.com/", "must use https"),
        ("crm.example.com/hook", "must use https"),
        ("https://user:pass@crm.example.com/", "credentials"),
        ("https://user@crm.example.com/", "credentials"),
        ("https://@crm.example.com/", "credentials"),
        ("https://crm.example.com/#frag", "fragment"),
        ("https:///path", "must include a host"),
        ("https://crm.example.com:0/", "invalid port"),
        ("https://crm.example.com:99999/", "invalid port"),
        ("https://crm.example.com:443:80/", "invalid port"),
        ("https://crm.example.com:22/", "port must be 443"),
        ("https://crm.example.com:80/", "port must be 443"),
        ("https://crm.example.com:6379/", "port must be 443"),
        ("https://8.8.8.8:8080/x", "port must be 443"),
        ("https://crm.example.com/a b", "spaces"),
        ("https://crm.example.com/\x00", "control characters"),
        ("https://crm.example.com\\@evil.com/", "backslashes"),
        ("https://crm.éxample.com/", "ASCII"),
        ("https://[evil.com]/", "not a valid URL"),
        ("", "required"),
        (None, "required"),
    ],
)
def test_rejects_malformed_urls(url, message):
    with pytest.raises(ws.WebhookUrlError, match=message):
        ws.validate_webhook_url(url)


def test_rejects_longer_than_2048_characters():
    url = "https://crm.example.com/" + "a" * (ws.MAX_URL_LENGTH - len("https://crm.example.com/") + 1)
    with pytest.raises(ws.WebhookUrlError, match="at most 2048 characters"):
        ws.validate_webhook_url(url)


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",  # loopback
        "10.1.2.3",  # private
        "172.16.0.1",
        "192.168.1.10",
        "169.254.169.254",  # instance metadata (link-local)
        "169.254.170.2",  # ECS task metadata
        "100.64.1.1",  # carrier-grade NAT
        "0.0.0.0",
        "224.0.0.1",  # multicast
        "255.255.255.255",
        "192.0.2.10",  # documentation
        "[::1]",  # IPv6 loopback
        "[::]",
        "[fd00:ec2::254]",  # instance metadata over IPv6 (unique local)
        "[fc00::1]",
        "[fe80::1]",  # IPv6 link-local
        "[::ffff:127.0.0.1]",  # IPv4-mapped loopback
        "[::ffff:169.254.169.254]",
        "[64:ff9b::a9fe:a9fe]",  # NAT64 of 169.254.169.254
        "[2002:7f00:1::]",  # 6to4 of 127.0.0.1
        "[::127.0.0.1]",  # IPv4-compatible
        "[::ffff:0:127.0.0.1]",  # IPv4-translated (SIIT) loopback
        "[::ffff:0:a9fe:a9fe]",  # IPv4-translated 169.254.169.254
        "[::ffff:0:8.8.8.8]",  # IPv4-translated, even of a public address
        "[4000::1]",  # reserved: outside global unicast 2000::/3
        "[e000::1]",
        "[ff02::1]",  # multicast
    ],
)
def test_rejects_non_public_ip_literals(host):
    with pytest.raises(ws.WebhookUrlError, match="private, loopback, link-local or reserved"):
        ws.validate_webhook_url(f"https://{host}/hook")


def test_rejects_ipv6_zone_id():
    with pytest.raises(ws.WebhookUrlError, match="zone ID"):
        ws.validate_webhook_url("https://[fe80::1%25eth0]/hook")


def test_rejects_ipv4_mapped_public_literal():
    with pytest.raises(ws.WebhookUrlError, match="IPv4 address"):
        ws.validate_webhook_url("https://[::ffff:8.8.8.8]/hook")


@pytest.mark.parametrize("host", ["2130706433", "0x7f000001", "0x7f.1", "127.1", "017700000001", "010.0.0.1"])
def test_rejects_non_canonical_ipv4_spellings(host):
    # getaddrinfo/inet_aton read all of these as 127.0.0.1 or 8.0.0.1.
    with pytest.raises(ws.WebhookUrlError, match="dotted-decimal"):
        ws.validate_webhook_url(f"https://{host}/hook")


@pytest.mark.parametrize(
    ("host", "message"),
    [
        ("localhost", "internal name"),
        ("api.localhost", "internal name"),
        ("printer.local", "internal name"),
        ("metadata.google.internal", "internal name"),
        ("ip-10-0-0-1.ap-south-1.compute.internal", "internal name"),
        ("router.home.arpa", "internal name"),
        ("crm", "fully qualified"),
        ("metadata", "fully qualified"),
        ("-bad.example.com", "not a valid DNS name"),
        ("bad-.example.com", "not a valid DNS name"),
        ("a..example.com", "not a valid DNS name"),
        ("%31%32%37.0.0.1", "not a valid DNS name"),
        ("x" * 64 + ".example.com", "not a valid DNS name"),
    ],
)
def test_rejects_internal_and_invalid_names(host, message):
    with pytest.raises(ws.WebhookUrlError, match=message):
        ws.validate_webhook_url(f"https://{host}/hook")


def test_hex_looking_dns_name_is_a_name():
    assert ws.validate_webhook_url("https://cafe.babe/hook").host == "cafe.babe"


# ------------------------------------------------------------------ resolved addresses
def _answer(*addresses):
    def resolver(host, port, type=None):
        assert type == socket.SOCK_STREAM
        out = []
        for a in addresses:
            family = socket.AF_INET6 if ":" in a else socket.AF_INET
            sockaddr = (a, port, 0, 0) if family == socket.AF_INET6 else (a, port)
            out.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
        return out

    return resolver


def test_resolve_returns_public_addresses_ipv4_first_without_duplicates():
    resolver = _answer("2606:4700:4700::1111", "93.184.216.34", "93.184.216.34")
    assert ws.resolve_public_addresses("crm.example.com", 443, resolver=resolver) == [
        "93.184.216.34",
        "2606:4700:4700::1111",
    ]


@pytest.mark.parametrize(
    "addresses",
    [
        ("10.0.0.5",),
        ("169.254.169.254",),
        ("127.0.0.1",),
        ("::1",),
        ("fd00:ec2::254",),
        ("fe80::1%2",),
        ("::ffff:10.0.0.5",),
        ("::ffff:0:a9fe:a9fe",),  # IPv4-translated instance metadata
        ("::ffff:0:7f00:1",),  # IPv4-translated loopback
        ("4000::1",),  # reserved IPv6 that ipaddress calls global
        ("93.184.216.34", "10.0.0.5"),  # one private answer blocks the lot (DNS rebinding)
    ],
)
def test_resolve_blocks_any_non_public_answer(addresses):
    with pytest.raises(ws.WebhookAddressBlockedError, match="resolves to a private"):
        ws.resolve_public_addresses("crm.example.com", 443, resolver=_answer(*addresses))


def test_resolve_failure_is_an_os_error_not_a_block():
    def resolver(*args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    with pytest.raises(OSError) as info:
        ws.resolve_public_addresses("nx.example.com", 443, resolver=resolver)
    assert not isinstance(info.value, ws.WebhookUrlError)


def test_resolve_without_addresses_is_an_os_error():
    with pytest.raises(OSError):
        ws.resolve_public_addresses("crm.example.com", 443, resolver=_answer())


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.216.34", True),
        ("2606:4700:4700::1111", True),
        ("::ffff:93.184.216.34", True),
        ("2001:4860:4860::8888", True),
        ("169.254.169.254", False),
        ("fd00:ec2::254", False),
        # IPv4-translated (::ffff:0:0:0/96): ipaddress calls these global.
        ("::ffff:0:7f00:1", False),
        ("::ffff:0:a9fe:a9fe", False),
        ("::ffff:0:808:808", False),
        # Reserved IPv6 outside 2000::/3 that ipaddress also calls global.
        ("4000::1", False),
        ("e000::1", False),
        ("not-an-ip", False),
    ],
)
def test_is_public_address(address, public):
    assert ws.is_public_address(address) is public


# ------------------------------------------------------------------ signing
def test_signature_known_vector():
    # Computed independently: printf '%s' '1700000000.{"event":"test","delivery_id":"d-1"}' |
    #   openssl dgst -sha256 -hmac 'whsec-known-vector'
    body = b'{"event":"test","delivery_id":"d-1"}'
    assert ws.sign_payload("whsec-known-vector", body, 1700000000) == (
        "t=1700000000,v1=44e3b5e60a9bd2aca0a43d78d9bb2a5ea7913107bef41a396b45c5c01364be27"
    )


def test_signature_is_hmac_sha256_of_timestamp_dot_body():
    secret, body, t = "s3cr3t", '{"a":"₹"}'.encode(), 1759200000
    expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    assert ws.sign_payload(secret, body, t) == f"t={t},v1={expected}"


def test_verify_round_trip_and_tolerance():
    body = b'{"event":"test"}'
    header = ws.sign_payload("k", body, 1_000_000)
    assert ws.verify_signature("k", header, body, now=1_000_000 + 299)
    assert not ws.verify_signature("k", header, body, now=1_000_000 + 301)
    assert not ws.verify_signature("other", header, body, now=1_000_000)
    assert not ws.verify_signature("k", header, body + b" ", now=1_000_000)
    assert not ws.verify_signature("k", "t=1000000", body, now=1_000_000)
    assert not ws.verify_signature("k", "v1=abc", body, now=1_000_000)
    assert not ws.verify_signature("k", "", body, now=1_000_000)
    # A second v1 (for example during a secret rotation) is accepted.
    rotated = header + ",v1=" + "0" * 64
    assert ws.verify_signature("k", rotated, body, now=1_000_000)


def test_generate_secret_is_32_random_bytes_urlsafe():
    import base64

    secrets = {ws.generate_secret() for _ in range(20)}
    assert len(secrets) == 20
    for s in secrets:
        assert len(s) == 43
        assert set(s) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert len(base64.urlsafe_b64decode(s + "=")) == 32
