"""Webhook URL checks, request signing and signing secrets (standard library only).

This file ships twice and the two copies must stay byte-identical (the backend
and the Lambda test suites both compare them):

- packages/backend/app/webhook_security.py: checks the URL on
  PUT /projects/{id}/integrations/webhook and generates the signing secret;
- packages/infra/src/functions/webhook/webhook_security.py: the delivery Lambda
  checks the URL again, checks every resolved address and signs the request.

The backend image and the Lambda asset are packaged from different folders, so
the module is copied instead of imported across packages.

URL rules (validate_webhook_url): https only, port 443 (the default) or 8443,
at most 2048 characters, ASCII only, no credentials, no fragment. The host is a
public DNS name (two labels or more, not localhost / *.local / *.internal) or a
public IP literal: loopback, private, link-local (169.254.0.0/16, with the
instance metadata address 169.254.169.254), unique-local (fd00:ec2::254),
shared (100.64.0.0/10), multicast, reserved and IPv4-in-IPv6 transition
addresses (IPv4-compatible, IPv4-translated, NAT64, 6to4, Teredo) are refused,
IPv6 must be global unicast (2000::/3), and non-canonical IPv4 spellings such as
2130706433 or 0x7f.1 are refused too.

At send time resolve_public_addresses resolves the host once and refuses the
delivery when any address is not public; the sender then connects to those
addresses only, so the DNS answer cannot change between the check and the
connection.

Signature header: t=<unix seconds>,v1=<hex HMAC-SHA256(key=secret as UTF-8,
message=f"{t}." + raw request body)>. A receiver recomputes it over the raw body
bytes, compares in constant time and rejects timestamps more than 5 minutes
away from its clock (verify_signature).

Secrets at rest: the backend encrypts a new secret with the webhook KMS key
(WebhookStack) and stores only the ciphertext; the delivery Lambda is the only
role that may decrypt it. Both use secret_encryption_context(project_id), so a
ciphertext only decrypts for its own project.
"""

import hashlib
import hmac
import ipaddress
import re
import secrets
import socket
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

MAX_URL_LENGTH = 2048
# https on its default port, or the common alternative; any other port would
# let a URL probe arbitrary services of a public host.
ALLOWED_PORTS = (443, 8443)
SECRET_BYTES = 32
SIGNATURE_TOLERANCE_S = 300

# KMS encryption context "purpose" of a stored signing secret (the key policy
# grants Encrypt and Decrypt only with it).
SECRET_ENCRYPTION_PURPOSE = "webhook-signing-secret"

EVENT_HEADER = "X-DocAI-Event"
DELIVERY_HEADER = "X-DocAI-Delivery"
SIGNATURE_HEADER = "X-DocAI-Signature"

# Never reachable by a webhook, on top of everything ipaddress does not call
# global. IPv4-mapped IPv6 addresses are judged by their IPv4 part.
_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "0.0.0.0/8",  # "this network"
        "10.0.0.0/8",  # private
        "100.64.0.0/10",  # shared address space (carrier-grade NAT)
        "127.0.0.0/8",  # loopback
        "169.254.0.0/16",  # link-local: 169.254.169.254 instance metadata, 169.254.170.2 ECS task metadata
        "172.16.0.0/12",  # private
        "192.0.0.0/24",  # IETF protocol assignments
        "192.0.2.0/24",  # documentation
        "192.88.99.0/24",  # 6to4 relay anycast (deprecated)
        "192.168.0.0/16",  # private
        "198.18.0.0/15",  # benchmarking
        "198.51.100.0/24",  # documentation
        "203.0.113.0/24",  # documentation
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved, with 255.255.255.255
        "::/96",  # unspecified, loopback and IPv4-compatible (deprecated)
        "::ffff:0:0:0/96",  # IPv4-translated (SIIT, RFC 2765 / 6052)
        "64:ff9b::/96",  # NAT64
        "64:ff9b:1::/48",  # local-use NAT64
        "100::/64",  # discard-only
        "2001::/32",  # Teredo
        "2001:db8::/32",  # documentation
        "2002::/16",  # 6to4
        "fc00::/7",  # unique local, with fd00:ec2::254 (instance metadata over IPv6)
        "fe80::/10",  # link-local
        "fec0::/10",  # site-local (deprecated)
        "ff00::/8",  # multicast
    )
)

# IANA global unicast: every other IPv6 range is special-purpose or reserved,
# even where ipaddress still calls it global (e.g. 4000::/3).
_GLOBAL_UNICAST_V6 = ipaddress.ip_network("2000::/3")

_INTERNAL_NAMES = frozenset({"localhost"})
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")
_LABEL_RE = re.compile(r"(?!-)[a-z0-9_-]{1,63}(?<!-)")
# Hosts that inet_aton may read as an IPv4 address (decimal, octal or hex parts).
_NUMERIC_HOST_RE = re.compile(r"[0-9a-fx.]+")

Resolver = Callable[..., list[Any]]


class WebhookUrlError(ValueError):
    """The URL is not acceptable for a webhook; the message says why."""


class WebhookAddressBlockedError(WebhookUrlError):
    """The host resolves to an address a webhook may not reach."""


@dataclass(frozen=True)
class WebhookTarget:
    """Where a delivery goes: host (lowercase; IPv6 without brackets), port and request target (path?query)."""

    host: str
    port: int
    target: str


def is_public_address(address: Any) -> bool:
    """True only for a globally routable unicast address outside every blocked range."""
    try:
        ip = ipaddress.ip_address(str(address).split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address) and ip not in _GLOBAL_UNICAST_V6:
        return False
    if any(ip in network for network in _BLOCKED_NETWORKS if network.version == ip.version):
        return False
    return ip.is_global and not ip.is_multicast


def _check_host(hostname: str) -> str:
    host = hostname.lower()
    if host.endswith("."):
        host = host[:-1]
    if "%" in host and ":" in host:
        raise WebhookUrlError("Webhook URL host must not carry an IPv6 zone ID")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not is_public_address(ip):
            raise WebhookUrlError("Webhook URL host is a private, loopback, link-local or reserved IP address")
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            raise WebhookUrlError("Webhook URL host must be written as an IPv4 address, not IPv4-mapped IPv6")
        return str(ip)
    if _NUMERIC_HOST_RE.fullmatch(host):
        try:
            socket.inet_aton(host)
        except OSError:
            pass
        else:
            raise WebhookUrlError("Webhook URL host must be a DNS name or a dotted-decimal IP address")
    labels = host.split(".")
    if len(host) > 253 or not all(_LABEL_RE.fullmatch(label) for label in labels):
        raise WebhookUrlError("Webhook URL host is not a valid DNS name")
    if host in _INTERNAL_NAMES or host.endswith(_INTERNAL_SUFFIXES):
        raise WebhookUrlError("Webhook URL host is an internal name")
    if len(labels) < 2:
        raise WebhookUrlError("Webhook URL host must be a fully qualified domain name")
    return host


def validate_webhook_url(url: Any) -> WebhookTarget:
    """Check a webhook URL; return where to send, or raise WebhookUrlError."""
    if not isinstance(url, str) or not url:
        raise WebhookUrlError("Webhook URL is required")
    if len(url) > MAX_URL_LENGTH:
        raise WebhookUrlError(f"Webhook URL must be at most {MAX_URL_LENGTH} characters")
    if not url.isascii():
        raise WebhookUrlError("Webhook URL must be ASCII (write international domain names in punycode)")
    if "\\" in url or any(ch.isspace() or not ch.isprintable() for ch in url):
        raise WebhookUrlError("Webhook URL must not contain spaces, control characters or backslashes")
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        raise WebhookUrlError("Webhook URL is not a valid URL") from None
    if parts.scheme != "https":
        raise WebhookUrlError("Webhook URL must use https")
    if "@" in parts.netloc:
        raise WebhookUrlError("Webhook URL must not contain credentials (user:password@)")
    if "#" in url:
        raise WebhookUrlError("Webhook URL must not contain a fragment (#...)")
    if not parts.hostname:
        raise WebhookUrlError("Webhook URL must include a host")
    try:
        port = parts.port
    except ValueError:
        port = 0
    if port is None:
        port = 443
    if not 1 <= port <= 65535:
        raise WebhookUrlError("Webhook URL has an invalid port")
    host = _check_host(parts.hostname)
    if port not in ALLOWED_PORTS:
        raise WebhookUrlError("Webhook URL port must be 443 (the https default) or 8443")
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    return WebhookTarget(host=host, port=port, target=target)


def resolve_public_addresses(host: str, port: int, resolver: Resolver | None = None) -> list[str]:
    """Resolve host once and return its addresses, IPv4 first.

    Raises WebhookAddressBlockedError when ANY address is not public (a mixed
    answer is refused as a whole) and OSError when the name does not resolve.
    """
    infos = (resolver or socket.getaddrinfo)(host, port, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for family, _type, _proto, _canonname, sockaddr in infos:
        if family in (socket.AF_INET, socket.AF_INET6) and sockaddr[0] not in addresses:
            addresses.append(sockaddr[0])
    if not addresses:
        raise OSError(f"{host} has no IPv4 or IPv6 address")
    if not all(is_public_address(address) for address in addresses):
        raise WebhookAddressBlockedError(f"{host} resolves to a private, loopback, link-local or reserved address")
    return sorted(addresses, key=lambda address: ":" in address)


def generate_secret() -> str:
    """A new signing secret: 32 random bytes, URL-safe base64 without padding (43 characters)."""
    return secrets.token_urlsafe(SECRET_BYTES)


def secret_encryption_context(project_id: str) -> dict[str, str]:
    """KMS encryption context of a project's stored signing secret (Encrypt and Decrypt must match)."""
    return {"project_id": str(project_id), "purpose": SECRET_ENCRYPTION_PURPOSE}


def sign_payload(secret: str, body: bytes, timestamp: int) -> str:
    """X-DocAI-Signature value for `body` sent at `timestamp` (unix seconds)."""
    t = int(timestamp)
    digest = hmac.new(secret.encode("utf-8"), f"{t}.".encode("ascii") + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"


def verify_signature(
    secret: str,
    header: str,
    body: bytes,
    *,
    now: float | None = None,
    tolerance_s: int = SIGNATURE_TOLERANCE_S,
) -> bool:
    """Receiver-side check of an X-DocAI-Signature header over the raw body bytes."""
    timestamp = None
    signatures = []
    for part in (header or "").split(","):
        key, sep, value = part.strip().partition("=")
        if not sep or not value.isascii():
            continue
        if key == "t" and value.isdigit():
            timestamp = int(value)
        elif key == "v1":
            signatures.append(value)
    if timestamp is None or not signatures:
        return False
    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance_s:
        return False
    expected = sign_payload(secret, body, timestamp).partition(",v1=")[2]
    return any(hmac.compare_digest(expected, signature) for signature in signatures)
