"""Webhook delivery Lambda (idp-v2-webhook-delivery): push the loan-file verdict to the project's CRM.

Input: {"project_id": "...", "document_id": "..." (optional), "login": {...} (file_login.requested),
        "event": "file_check.completed" | "test" | "file_login.requested"}
- file_check.completed: queued (asynchronous invoke) by the workflow finalizer when a
  document's analysis completes and the project's webhook is enabled;
- test: invoked synchronously by POST /projects/{id}/integrations/webhook/test;
- file_login.requested: invoked synchronously by POST /projects/{id}/eligibility/login when
  the file is logged in with a lender; "login" carries {applicant, lender, eligible_amount,
  emi, tenure_months, roi, note} (the backend's indicative figures), sent as the one result.

1. Load the project META item (webhook_url, webhook_enabled, webhook_secret_enc) with a
   strongly consistent read. file_check.completed and file_login.requested need the webhook
   enabled; test needs only a URL and a secret. Otherwise (or for a malformed login) answer
   {"status": "skipped"} and call nothing.
   The secret is stored encrypted with the webhook KMS key (WEBHOOK_SECRET_KEY_ARN,
   encryption context webhook_security.secret_encryption_context); this function is
   the only one allowed to decrypt it.
2. file_check.completed: run the deterministic file check (the file-check Lambda,
   invoked like the backend does: tool name in ClientContext) and report the
   applicant(s) the document belongs to. A document the check cannot attribute
   (still pending, failed, without facts, unsupported, unassigned) gets one
   project-level result without applicant data. test: no file check and no applicant
   data (results = []). file_login.requested: no file check; results = [the login].
3. POST {event, delivery_id, project_id, document_id, at, results} as JSON with the
   X-DocAI-Event, X-DocAI-Delivery and X-DocAI-Signature headers
   (webhook_security.sign_payload): each attempt gets 5 s in total (connect, TLS,
   request and response headers; at most 2 addresses tried), up to 3 attempts (1 s and
   2 s backoff) within 25 s on 5xx and network errors, no retry on other answers,
   redirects never followed. The URL is checked again, the host is resolved once and
   the delivery is refused when any address is not public; the connection goes to
   those addresses only.
4. Record one delivery item PK=PROJ#{project_id}, SK=WHDLV#{at}#{delivery_id} with
   expires_at = now + RETENTION_DAYS (DynamoDB TTL) and answer
   {delivery_id, status, http_status, error}.

Never logged: the secret, the URL (its host only), the payload or any applicant data.
The handler never raises; the function has no asynchronous retries (retryAttempts 0),
so Lambda never sends a delivery twice.
"""

import base64
import contextlib
import http.client
import json
import math
import os
import re
import socket
import ssl
import threading
import time
import traceback
import uuid
from datetime import UTC, datetime
from typing import Any

import webhook_security as ws
from botocore.exceptions import BotoCoreError, ClientError

TABLE_NAME = os.environ.get("BACKEND_TABLE_NAME", "")
FILE_CHECK_FUNCTION_NAME = os.environ.get("FILE_CHECK_FUNCTION_NAME", "")
# KMS key of the stored signing secrets (WebhookStack); only this function may decrypt.
SECRET_KEY_ARN = os.environ.get("WEBHOOK_SECRET_KEY_ARN", "")
AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")

EVENT_FILE_CHECK = "file_check.completed"
EVENT_TEST = "test"
EVENT_LOGIN = "file_login.requested"
EVENTS = (EVENT_FILE_CHECK, EVENT_TEST, EVENT_LOGIN)
# Events sent only while the project's webhook is enabled (test is sent regardless).
EVENTS_NEEDING_ENABLED = (EVENT_FILE_CHECK, EVENT_LOGIN)

DELIVERY_SK_PREFIX = "WHDLV#"
# One attempt in total: connect, TLS handshake, request and response headers.
TIMEOUT_S = 5
# Addresses of the host tried per attempt (IPv4 first).
MAX_ADDRESSES_PER_ATTEMPT = 2
MAX_ATTEMPTS = 3
BACKOFF_S = (1, 2)
# Whole delivery, retries included: the backend's test call must answer within
# API Gateway's 30 s.
DELIVERY_BUDGET_S = 25
# The finalizer has just marked the document completed; if an eventually
# consistent read still shows it pending, look again after these delays.
PENDING_RECHECK_S = (2, 4)
USER_AGENT = "idp-v2-webhook/1.0"
MAX_ERROR_CHARS = 300
MAX_SUMMARY_CHARS = 1000
MAX_APPLICANT_CHARS = 500

# Gateway-style tool name (packages/backend/app/file_check.py sends the same).
FILE_CHECK_TOOL = "filecheck___run_file_check"

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _retention_days() -> int:
    try:
        days = int(os.environ.get("RETENTION_DAYS") or 7)
    except ValueError:
        return 7
    return days if days >= 1 else 7


RETENTION_DAYS = _retention_days()

# Module-level so tests can replace them.
_resolve = socket.getaddrinfo
_sleep = time.sleep
_time = time.time
_monotonic = time.monotonic

_table = None
_lambda_client = None
_kms_client = None
_tls_context = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _get_table():
    global _table
    if _table is None:
        import boto3

        _table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(TABLE_NAME)
    return _table


def _get_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        import boto3
        from botocore.config import Config

        # The file-check function times out after 30 s; one retry covers throttling.
        _lambda_client = boto3.client(
            "lambda",
            region_name=AWS_REGION,
            config=Config(connect_timeout=5, read_timeout=40, retries={"max_attempts": 2, "mode": "standard"}),
        )
    return _lambda_client


def _get_kms_client():
    global _kms_client
    if _kms_client is None:
        import boto3
        from botocore.config import Config

        _kms_client = boto3.client(
            "kms",
            region_name=AWS_REGION,
            config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 3, "mode": "standard"}),
        )
    return _kms_client


def _get_tls_context() -> ssl.SSLContext:
    global _tls_context
    if _tls_context is None:
        # Certificate and host name verification with the system CA bundle.
        _tls_context = ssl.create_default_context()
    return _tls_context


# ------------------------------------------------------------------ settings
def load_webhook(project_id: str) -> dict | None:
    """URL, enabled flag and encrypted secret from the project META item; None when the project does not exist."""
    response = _get_table().get_item(
        Key={"PK": f"PROJ#{project_id}", "SK": "META"},
        ProjectionExpression="PK, webhook_url, webhook_enabled, webhook_secret_enc",
        ConsistentRead=True,
    )
    item = response.get("Item")
    if not item:
        return None
    url, secret_enc = item.get("webhook_url"), item.get("webhook_secret_enc")
    return {
        "url": url if isinstance(url, str) and url else None,
        "enabled": item.get("webhook_enabled") is True,
        "secret_enc": secret_enc if isinstance(secret_enc, str) and secret_enc else None,
    }


class SecretError(Exception):
    """The signing secret could not be decrypted; the message says why (never key material)."""


def decrypt_secret(project_id: str, secret_enc: str) -> str:
    """The project's signing secret: KMS Decrypt of the stored ciphertext with the project's context."""
    if not SECRET_KEY_ARN:
        raise SecretError("secret key is not configured")
    try:
        blob = base64.b64decode(secret_enc, validate=True)
    except ValueError:
        raise SecretError("stored secret is malformed") from None
    try:
        response = _get_kms_client().decrypt(
            CiphertextBlob=blob,
            KeyId=SECRET_KEY_ARN,
            EncryptionContext=ws.secret_encryption_context(project_id),
        )
    except ClientError as e:
        raise SecretError(f"decrypt failed ({e.response.get('Error', {}).get('Code') or 'ClientError'})") from None
    except BotoCoreError as e:
        raise SecretError(f"decrypt failed ({type(e).__name__})") from None
    try:
        secret = response["Plaintext"].decode("utf-8")
    except (KeyError, AttributeError, UnicodeDecodeError):
        raise SecretError("decrypted secret is malformed") from None
    if not secret:
        raise SecretError("decrypted secret is empty")
    return secret


# ------------------------------------------------------------------ file check
class FileCheckError(Exception):
    """The file-check Lambda could not be invoked, failed or answered with an error."""


def run_file_check(project_id: str) -> dict:
    """The file check of the whole project (default checklist), as the backend's integration API gets it."""
    if not FILE_CHECK_FUNCTION_NAME:
        raise FileCheckError("file-check function is not configured")
    context = base64.b64encode(json.dumps({"custom": {"bedrockAgentCoreToolName": FILE_CHECK_TOOL}}).encode("utf-8"))
    try:
        response = _get_lambda_client().invoke(
            FunctionName=FILE_CHECK_FUNCTION_NAME,
            InvocationType="RequestResponse",
            ClientContext=context.decode("ascii"),
            Payload=json.dumps({"project_id": project_id}).encode("utf-8"),
        )
        raw = response["Payload"].read()
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code") or "ClientError"
        raise FileCheckError(f"invoke failed ({code})") from e
    except BotoCoreError as e:
        raise FileCheckError(f"invoke failed ({type(e).__name__})") from e
    if response.get("FunctionError"):
        raise FileCheckError(f"function error ({response['FunctionError']})")
    try:
        payload = json.loads(raw)
    except ValueError as e:
        raise FileCheckError("non-JSON response") from e
    if not isinstance(payload, dict):
        raise FileCheckError("non-object response")
    if payload.get("error"):
        # The handler reports errors as {"error": ...}; messages carry no document facts.
        raise FileCheckError(str(payload["error"])[:200])
    return payload


def _is_pending(check: dict, document_id: str) -> bool:
    return any(
        isinstance(p, dict) and p.get("document_id") == document_id for p in check.get("pending_documents") or []
    )


def file_check_for_document(project_id: str, document_id: str | None) -> dict:
    """run_file_check, looked at again while the triggering document still shows as pending."""
    check = run_file_check(project_id)
    for delay in PENDING_RECHECK_S:
        if not document_id or not _is_pending(check, document_id):
            break
        _sleep(delay)
        check = run_file_check(project_id)
    return check


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def applicant_summary(applicant: dict) -> str:
    """'<name> <verdict> (N issues, M to review): <reason headlines>' like the web app's summary line."""
    verdict = str(applicant.get("verdict") or "NOT READY")
    reasons = [str(r) for r in applicant.get("reasons") or []]
    needs_review = applicant.get("needs_review") or []
    text = f"{applicant.get('applicant') or 'Unknown'} {verdict}"
    notes = []
    if verdict != "READY" and reasons:
        notes.append(_plural(len(reasons), "issue"))
    if needs_review:
        notes.append(f"{len(needs_review)} to review")
    if notes:
        text += f" ({', '.join(notes)})"
    if verdict != "READY" and reasons:
        # "MISSING – Salary slips: <detail>" -> "MISSING – Salary slips"
        text += ": " + "; ".join(reason.split(": ", 1)[0] for reason in reasons)
    return text[:MAX_SUMMARY_CHARS]


# Why the check could not attribute a document, by the list it appears in.
_UNATTRIBUTED = (
    ("pending_documents", "it is still being analysed"),
    ("failed_documents", "its analysis failed"),
    ("no_facts_documents", "no facts were extracted from it"),
    ("unsupported_documents", "the file check does not read this file type"),
    ("unassigned_documents", "no applicant could be identified in it"),
)


def _unattributed_reason(check: dict, document_id: str | None) -> str:
    for key, reason in _UNATTRIBUTED:
        if any(isinstance(d, dict) and d.get("document_id") == document_id for d in check.get(key) or []):
            return reason
    return "it is not in the file check"


def build_results(check: dict, document_id: str | None) -> list[dict]:
    """One result per applicant the document belongs to.

    Data minimisation: when the check cannot attribute the document (or there
    is no document id), one project-level result is returned instead, with
    applicant null, the overall verdict and a summary that names no applicant.
    With no applicant at all (nothing analysed yet, or no applicant could be
    identified) that result carries the check's own summary, which names none.
    """
    applicants = [a for a in check.get("applicants") or [] if isinstance(a, dict)]
    checklist_id = (check.get("checklist") or {}).get("id")
    chosen = []
    if document_id:
        chosen = [
            a
            for a in applicants
            if any(isinstance(d, dict) and d.get("document_id") == document_id for d in a.get("documents") or [])
        ]
    if not chosen:
        if applicants:
            reason = _unattributed_reason(check, document_id) if document_id else "no document was named"
            count = _plural(len(applicants), "applicant")
            summary = f"Document not attributed to an applicant: {reason} ({count} in the file)"
        else:
            summary = str(check.get("summary") or "")
        return [
            {
                "applicant": None,
                "verdict": check.get("overall_verdict") or "NOT READY",
                "summary": summary[:MAX_SUMMARY_CHARS],
                "missing": [],
                "checklist_id": checklist_id,
            }
        ]
    return [
        {
            "applicant": a.get("applicant"),
            "verdict": a.get("verdict"),
            "summary": applicant_summary(a),
            "missing": [str(m) for m in a.get("missing_items") or []],
            "checklist_id": checklist_id,
        }
        for a in chosen
    ]


# (field, kind, limit): text up to `limit` characters, or a finite number from 0 to `limit`.
_LOGIN_FIELDS = (
    ("applicant", "text", 200),
    ("lender", "text", 100),
    ("eligible_amount", "number", 1e10),
    ("emi", "number", 1e10),
    ("tenure_months", "int", 600),
    ("roi", "number", 100),
    ("note", "optional_text", 300),
)


def login_result(login) -> dict | None:
    """The file_login.requested result: exactly the known login fields, checked; None when malformed."""
    if not isinstance(login, dict):
        return None
    result: dict[str, Any] = {}
    for name, kind, limit in _LOGIN_FIELDS:
        value = login.get(name)
        if kind in ("text", "optional_text"):
            if value is None and kind == "optional_text":
                continue
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                return None
            result[name] = value.strip()
        elif kind == "int":
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= limit:
                return None
            result[name] = value
        else:
            if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
                return None
            if not 0 <= value <= limit:
                return None
            result[name] = value
    return result


# ------------------------------------------------------------------ HTTP
class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to `host` (SNI, certificate check and Host header) over TCP to pre-checked addresses only.

    http.client would resolve the host again when connecting; connecting to the
    addresses that resolve_public_addresses returned closes the window in which
    a second DNS answer could point the request at an internal address.

    At most MAX_ADDRESSES_PER_ATTEMPT addresses are tried, each connect gets only
    the time left before `deadline` (a _monotonic() time), and abort() ends a
    connect, TLS handshake, send or read that is still blocked: socket timeouts
    apply per operation, so a server that answers byte by byte would otherwise
    hold the attempt far longer.
    """

    def __init__(
        self,
        host: str,
        port: int,
        addresses: list[str],
        timeout: float,
        context: ssl.SSLContext,
        deadline: float | None = None,
    ):
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._addresses = list(addresses)[:MAX_ADDRESSES_PER_ATTEMPT]
        self._tls = context
        self._deadline = _monotonic() + timeout if deadline is None else deadline
        self._lock = threading.Lock()
        self._active = None
        self.aborted = False

    def _time_left(self) -> float:
        left = min(self.timeout, self._deadline - _monotonic())
        if left <= 0 or self.aborted:
            raise TimeoutError("attempt deadline passed")
        return left

    def _track(self, sock) -> None:
        """Make `sock` the one abort() shuts down; raises when abort() already ran."""
        with self._lock:
            self._active = sock
            aborted = self.aborted
        if aborted:
            raise TimeoutError("attempt aborted")

    def abort(self) -> None:
        """End the attempt now (the attempt's timer calls it); a blocked socket call returns."""
        with self._lock:
            self.aborted = True
            sock = self._active
        if sock is not None:
            with contextlib.suppress(OSError, ValueError):
                # The plain socket's shutdown: SSLSocket.shutdown would also drop the
                # SSL state that the blocked thread is using.
                socket.socket.shutdown(sock, socket.SHUT_RDWR)

    def connect(self) -> None:
        sock = None
        last_error: OSError | None = None
        for address in self._addresses:
            try:
                sock = socket.create_connection((address, self.port), self._time_left())
                break
            except TimeoutError:
                raise
            except OSError as e:
                last_error = e
        if sock is None:
            raise last_error or OSError("no address to connect to")
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            self._track(sock)
            sock.settimeout(self._time_left())
            tls = self._tls.wrap_socket(sock, server_hostname=self.host, do_handshake_on_connect=False)
        except BaseException:
            sock.close()
            raise
        # From here on close() closes it.
        self.sock = tls
        self._track(tls)
        tls.do_handshake()


def _post_once(
    target: ws.WebhookTarget,
    addresses: list[str],
    body: bytes,
    headers: dict[str, str],
    timeout_s: float = TIMEOUT_S,
) -> int:
    """One POST; returns the HTTP status (redirects are not followed).

    The whole attempt gets timeout_s: a timer aborts the connection when it runs
    out. Raises TimeoutError then, else OSError / HTTPException.
    """
    connection = PinnedHTTPSConnection(
        target.host, target.port, addresses, timeout_s, _get_tls_context(), deadline=_monotonic() + timeout_s
    )
    timer = threading.Timer(timeout_s, connection.abort)
    timer.daemon = True
    timer.start()
    try:
        connection.request("POST", target.target, body=body, headers=headers)
        status = connection.getresponse().status
        # After an abort http.client may still parse a cut-off answer as complete.
        if connection.aborted:
            raise TimeoutError
        return status
    except (OSError, http.client.HTTPException):
        if connection.aborted:
            raise TimeoutError(f"no answer within {timeout_s:g} s") from None
        raise
    finally:
        timer.cancel()
        connection.close()


def _outcome(status: str, http_status: int | None, error: str | None, attempts: int) -> dict:
    return {
        "status": status,
        "http_status": http_status,
        "error": error[:MAX_ERROR_CHARS] if error else None,
        "attempts": attempts,
    }


def deliver(url: str, secret: str, event: str, delivery_id: str, body: bytes) -> dict:
    """POST `body` to `url`, signed, with retries; returns {status, http_status, error, attempts}."""
    try:
        target = ws.validate_webhook_url(url)
    except ws.WebhookUrlError as e:
        return _outcome("failed", None, f"blocked: {e}", 0)
    deadline = _monotonic() + DELIVERY_BUDGET_S
    http_status: int | None = None
    error = "not sent"
    attempt = 0
    while attempt < MAX_ATTEMPTS:
        attempt += 1
        http_status = None
        try:
            addresses = ws.resolve_public_addresses(target.host, target.port, resolver=_resolve)
        except ws.WebhookUrlError as e:
            # Blocked by policy: another attempt would be blocked as well.
            return _outcome("failed", None, f"blocked: {e}", attempt)
        except (OSError, UnicodeError):
            error = "DNS lookup failed"
        else:
            # The attempt never runs past the delivery budget (a slow DNS answer counts).
            timeout_s = min(TIMEOUT_S, deadline - _monotonic())
            if timeout_s < 1:
                error = "delivery time budget used up"
                break
            headers = {
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                ws.EVENT_HEADER: event,
                ws.DELIVERY_HEADER: delivery_id,
                ws.SIGNATURE_HEADER: ws.sign_payload(secret, body, int(_time())),
            }
            try:
                http_status = _post_once(target, addresses, body, headers, timeout_s)
            except ssl.SSLCertVerificationError:
                return _outcome("failed", None, "TLS certificate verification failed", attempt)
            except TimeoutError:
                error = f"timed out after {TIMEOUT_S} s"
            except (OSError, http.client.HTTPException) as e:
                error = f"connection failed ({type(e).__name__})"
            else:
                if 200 <= http_status < 300:
                    return _outcome("delivered", http_status, None, attempt)
                if 300 <= http_status < 400:
                    return _outcome("failed", http_status, f"HTTP {http_status}: redirects are not followed", attempt)
                if not 500 <= http_status < 600:
                    return _outcome("failed", http_status, f"HTTP {http_status}", attempt)
                error = f"HTTP {http_status}"
        if attempt >= MAX_ATTEMPTS:
            break
        delay = BACKOFF_S[min(attempt, len(BACKOFF_S)) - 1]
        if _monotonic() + delay + TIMEOUT_S > deadline:
            break
        _sleep(delay)
    return _outcome("failed", http_status, error, attempt)


# ------------------------------------------------------------------ log
def record_delivery(project_id: str, delivery_id: str, at: datetime, event: str, document_id, applicant, outcome):
    """One delivery log item (never the payload or the secret), deleted by TTL after RETENTION_DAYS."""
    at_iso = at.astimezone(UTC).isoformat(timespec="microseconds")
    item: dict[str, Any] = {
        "PK": f"PROJ#{project_id}",
        "SK": f"{DELIVERY_SK_PREFIX}{at_iso}#{delivery_id}",
        "delivery_id": delivery_id,
        "at": at_iso,
        "event": event,
        "status": outcome["status"],
        "attempts": outcome["attempts"],
        "expires_at": int(at.timestamp()) + RETENTION_DAYS * 86400,
    }
    for name, value in (
        ("document_id", document_id),
        ("applicant", applicant),
        ("http_status", outcome["http_status"]),
        ("error", outcome["error"]),
    ):
        if value is not None:
            item[name] = value
    _get_table().put_item(Item=item)


# ------------------------------------------------------------------ handler
def _skipped(reason: str) -> dict:
    return {"status": "skipped", "reason": reason}


def _host_for_log(url: str) -> str:
    try:
        return ws.validate_webhook_url(url).host
    except ws.WebhookUrlError:
        return "?"


def _handle(event: dict) -> dict:
    project_id = event.get("project_id")
    if not isinstance(project_id, str) or not _ID_RE.match(project_id):
        return _skipped("invalid project_id")
    kind = event.get("event")
    if kind not in EVENTS:
        return _skipped("unknown event")
    document_id = event.get("document_id") if kind == EVENT_FILE_CHECK else None
    if not isinstance(document_id, str) or not _ID_RE.match(document_id):
        document_id = None
    login = login_result(event.get("login")) if kind == EVENT_LOGIN else None
    if kind == EVENT_LOGIN and login is None:
        return _skipped("invalid login")

    webhook = load_webhook(project_id)
    if webhook is None:
        return _skipped("project not found")
    if kind in EVENTS_NEEDING_ENABLED and not webhook["enabled"]:
        return _skipped("webhook disabled")
    if not webhook["url"] or not webhook["secret_enc"]:
        return _skipped("webhook URL or secret not set")

    delivery_id = str(uuid.uuid4())
    at = _utcnow()
    results: list[dict] = []
    outcome = None
    secret = ""
    try:
        secret = decrypt_secret(project_id, webhook["secret_enc"])
    except SecretError as e:
        outcome = _outcome("failed", None, f"signing secret unavailable: {e}", 0)
    if outcome is None and kind == EVENT_FILE_CHECK:
        try:
            results = build_results(file_check_for_document(project_id, document_id), document_id)
        except FileCheckError as e:
            outcome = _outcome("failed", None, f"file check failed: {e}", 0)
    if outcome is None and login is not None:
        results = [login]
    if outcome is None:
        payload = {
            "event": kind,
            "delivery_id": delivery_id,
            "project_id": project_id,
            "document_id": document_id,
            "at": at.astimezone(UTC).isoformat(timespec="microseconds"),
            "results": results,
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        outcome = deliver(webhook["url"], secret, kind, delivery_id, body)
    names = ", ".join(str(r["applicant"]) for r in results if r.get("applicant"))
    applicant = names[:MAX_APPLICANT_CHARS] or None

    try:
        record_delivery(project_id, delivery_id, at, kind, document_id, applicant, outcome)
    except Exception as e:  # noqa: BLE001 - the delivery happened; losing its log entry must not fail it
        print(f"webhook delivery log not written delivery={delivery_id}: {type(e).__name__}")
    # Counts and ids only: no URL path, no payload, no applicant data.
    print(
        f"webhook project={project_id} event={kind} delivery={delivery_id} host={_host_for_log(webhook['url'])} "
        f"status={outcome['status']} http_status={outcome['http_status']} attempts={outcome['attempts']} "
        f"results={len(results)} error={outcome['error']}"
    )
    return {
        "delivery_id": delivery_id,
        "status": outcome["status"],
        "http_status": outcome["http_status"],
        "error": outcome["error"],
    }


def handler(event, context):
    try:
        return _handle(event if isinstance(event, dict) else {})
    except Exception as e:  # noqa: BLE001 - never raise: report the failure type only
        frames = [f"  {f.filename}:{f.lineno} in {f.name}" for f in traceback.extract_tb(e.__traceback__)]
        print(f"webhook failed: {type(e).__name__}\nTraceback (most recent call last):\n" + "\n".join(frames))
        return {"status": "error", "error": f"webhook failed: {type(e).__name__}"}
