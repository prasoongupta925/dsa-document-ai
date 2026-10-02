"""Tests for the CRM webhook API (app/routers/integrations.py), no AWS calls.

The router is mounted on a test app (main.py includes it in the real app).
DynamoDB is a fake table that applies the module's SET / REMOVE updates and
attribute_exists conditions, KMS is a fake whose ciphertexts only decrypt with
the key and encryption context they were made with, and the boto3 Lambda
client is stubbed. One test runs the real delivery Lambda handler
(packages/infra/src/functions/webhook) in-process with stubbed DNS and HTTP,
so the backend <-> Lambda contract, the secret's encryption and the signature
are checked end to end. Two run the real boto3 client against a local HTTP
endpoint, to count the invokes that reach Lambda.
"""

import base64
import copy
import importlib.util
import io
import json
import os
import re
import sys
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.webhook_delivery as webhook_delivery
from app.config import get_config
from app.routers import integrations
from app.webhook_security import secret_encryption_context, verify_signature

webhook_app = FastAPI()
webhook_app.include_router(integrations.router)
client = TestClient(webhook_app)

HEADERS = {"x-user-id": "admin"}
PROJECT_ID = "proj_demo"
BASE = f"/projects/{PROJECT_ID}/integrations/webhook"
FUNCTION_NAME = "idp-v2-webhook-delivery"
CRM_URL = "https://crm.example.com/hooks/idp?token=abc"
KEY_ARN = "arn:aws:kms:ap-south-1:111111111111:key/00000000-0000-4000-8000-000000000000"

WEBHOOK_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "infra", "src", "functions", "webhook")
)


# ------------------------------------------------------------------ fakes
def _matches(condition, item) -> bool:
    expr = condition.get_expression()
    op, values = expr["operator"], expr["values"]
    if op == "AND":
        return _matches(values[0], item) and _matches(values[1], item)
    value = item.get(values[0].name)
    if op == "=":
        return value == values[1]
    if op == "BETWEEN":
        return value is not None and values[1] <= value <= values[2]
    raise AssertionError(f"unsupported key condition {op}")


def _conditional_failed():
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "The conditional request failed"}},
        "UpdateItem",
    )


class FakeTable:
    """Base table: get/put, key-condition queries and the update subset app/ddb/webhooks.py uses."""

    def __init__(self, items=()):
        self.items = {(i["PK"], i["SK"]): copy.deepcopy(i) for i in items}
        self.updates = []
        self.puts = []
        self.queries = []

    def item(self, pk, sk):
        return self.items.get((pk, sk))

    def get_item(self, Key, ProjectionExpression=None, ConsistentRead=None):
        item = self.items.get((Key["PK"], Key["SK"]))
        if item is None:
            return {}
        if ProjectionExpression:
            names = [n.strip() for n in ProjectionExpression.split(",")]
            item = {n: item[n] for n in names if n in item}
        return {"Item": copy.deepcopy(item)}

    def put_item(self, Item):
        self.puts.append(copy.deepcopy(Item))
        self.items[(Item["PK"], Item["SK"])] = copy.deepcopy(Item)

    def update_item(self, Key, UpdateExpression, ConditionExpression=None, ExpressionAttributeValues=None):
        self.updates.append(UpdateExpression)
        item = self.items.get((Key["PK"], Key["SK"]))
        for clause in (ConditionExpression or "").split(" AND "):
            if not clause:
                continue
            match = re.fullmatch(r"attribute_exists\((\w+)\)", clause.strip())
            assert match, f"unsupported condition {clause}"
            if item is None or match.group(1) not in item:
                raise _conditional_failed()
        if item is None:
            item = self.items[(Key["PK"], Key["SK"])] = dict(Key)
        match = re.fullmatch(r"SET (?P<set>.+?)(?: REMOVE (?P<remove>.+))?", UpdateExpression)
        assert match, f"unsupported update {UpdateExpression}"
        for assignment in match.group("set").split(","):
            name, value = (part.strip() for part in assignment.split("="))
            item[name] = ExpressionAttributeValues[value]
        for name in (match.group("remove") or "").split(","):
            if name.strip():
                item.pop(name.strip(), None)

    def query(self, KeyConditionExpression, ScanIndexForward=True, Limit=None, **kwargs):
        self.queries.append({"ScanIndexForward": ScanIndexForward, "Limit": Limit, **kwargs})
        rows = sorted(
            (i for i in self.items.values() if _matches(KeyConditionExpression, i)),
            key=lambda i: i["SK"],
            reverse=not ScanIndexForward,
        )
        if Limit:
            rows = rows[:Limit]
        return {"Items": copy.deepcopy(rows)}


class FakeKms:
    """KMS stub: a ciphertext decrypts only with the key and the encryption context it was made with."""

    PREFIX = b"fake-kms:"

    def __init__(self, encrypt_error=None):
        self.encrypts = []
        self.decrypts = []
        self.encrypt_error = encrypt_error

    def encrypt(self, KeyId, Plaintext, EncryptionContext):
        self.encrypts.append({"KeyId": KeyId, "EncryptionContext": EncryptionContext})
        if self.encrypt_error is not None:
            raise self.encrypt_error
        blob = json.dumps({"key": KeyId, "context": EncryptionContext, "pt": base64.b64encode(Plaintext).decode()})
        return {"CiphertextBlob": self.PREFIX + blob.encode("utf-8"), "KeyId": KeyId}

    def decrypt(self, CiphertextBlob, KeyId=None, EncryptionContext=None):
        self.decrypts.append({"KeyId": KeyId, "EncryptionContext": EncryptionContext})
        invalid = ClientError({"Error": {"Code": "InvalidCiphertextException", "Message": "x"}}, "Decrypt")
        if not CiphertextBlob.startswith(self.PREFIX):
            raise invalid
        data = json.loads(CiphertextBlob[len(self.PREFIX) :])
        if (KeyId, EncryptionContext) != (data["key"], data["context"]):
            raise invalid
        return {"Plaintext": base64.b64decode(data["pt"]), "KeyId": data["key"]}

    def open(self, secret_enc, project_id=PROJECT_ID):
        """The plaintext of a stored secret, as the delivery Lambda decrypts it."""
        blob = base64.b64decode(secret_enc)
        return self.decrypt(blob, KeyId=KEY_ARN, EncryptionContext=secret_encryption_context(project_id))[
            "Plaintext"
        ].decode("utf-8")


def sealed(secret, project_id=PROJECT_ID):
    blob = FakeKms().encrypt(KEY_ARN, secret.encode("utf-8"), secret_encryption_context(project_id))["CiphertextBlob"]
    return base64.b64encode(blob).decode("ascii")


META = {
    "PK": f"PROJ#{PROJECT_ID}",
    "SK": "META",
    "data": {"project_id": PROJECT_ID, "name": "Demo", "description": "", "status": "active", "language": "en"},
    "created_at": "2026-09-28T00:00:00+00:00",
    "updated_at": "2026-09-28T00:00:00+00:00",
    "GSI1PK": "PROJECTS",
    "GSI1SK": "2026-09-28T00:00:00+00:00",
}


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat(timespec="microseconds")


def delivery_item(at: datetime, n: int, **fields):
    item = {
        "PK": f"PROJ#{PROJECT_ID}",
        "SK": f"WHDLV#{_iso(at)}#d-{n}",
        "delivery_id": f"d-{n}",
        "at": _iso(at),
        "event": "file_check.completed",
        "status": "delivered",
        "http_status": Decimal(200),
        "attempts": Decimal(1),
        "expires_at": Decimal(int(at.timestamp()) + 7 * 86400),
    }
    item.update(fields)
    return item


@pytest.fixture
def table():
    fake = FakeTable([META])
    with patch("app.ddb.webhooks.get_table", return_value=fake):
        yield fake


@pytest.fixture
def kms():
    fake = FakeKms()
    with patch("app.webhook_secret.get_kms_client", return_value=fake):
        yield fake


@pytest.fixture
def configured(monkeypatch, kms):
    monkeypatch.setattr(get_config(), "webhook_function_name", FUNCTION_NAME)
    monkeypatch.setattr(get_config(), "webhook_secret_key_arn", KEY_ARN)
    monkeypatch.setattr(get_config(), "retention_days", 7)


def canned_lambda(payload=None, function_error=None, raw=None, error=None):
    stub = MagicMock()
    if error is not None:
        stub.invoke.side_effect = error
        return stub
    body = raw if raw is not None else json.dumps(payload).encode("utf-8")
    response = {"StatusCode": 200, "Payload": io.BytesIO(body)}
    if function_error:
        response["FunctionError"] = function_error
    stub.invoke.return_value = response
    return stub


def repeating_lambda(payload):
    """Like canned_lambda, with a fresh response body for every invoke."""
    stub = MagicMock()
    stub.invoke.side_effect = lambda **kwargs: {
        "StatusCode": 200,
        "Payload": io.BytesIO(json.dumps(payload).encode("utf-8")),
    }
    return stub


def use_lambda(stub):
    return patch("app.webhook_delivery.get_webhook_lambda_client", return_value=stub)


def meta(table):
    return table.item(f"PROJ#{PROJECT_ID}", "META")


def put(url, enabled):
    return client.put(BASE, headers=HEADERS, json={"url": url, "enabled": enabled})


def with_secret(table, secret="s" * 43, url=CRM_URL, enabled=False):
    item = meta(table)
    item["webhook_secret_enc"] = sealed(secret)
    if url:
        item["webhook_url"] = url
    item["webhook_enabled"] = enabled


# ------------------------------------------------------------------ 404 / auth
class TestProjectAndHeader:
    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("get", "", None),
            ("put", "", {"url": CRM_URL, "enabled": False}),
            ("post", "/secret", None),
            ("post", "/test", None),
        ],
    )
    def test_unknown_project_is_404(self, table, configured, method, path, body):
        url = f"/projects/proj_missing/integrations/webhook{path}"
        response = client.request(method.upper(), url, headers=HEADERS, json=body)

        assert response.status_code == 404
        assert response.json() == {"detail": "Project not found"}
        assert not table.updates and not table.puts
        assert table.item("PROJ#proj_missing", "META") is None

    def test_user_header_is_required(self, table):
        assert client.get(BASE).status_code == 422

    def test_invalid_project_id_is_422(self, table):
        assert client.get("/projects/bad%20id/integrations/webhook", headers=HEADERS).status_code == 422


# ------------------------------------------------------------------ GET / PUT
class TestSettings:
    def test_defaults_without_webhook_fields(self, table, configured):
        response = client.get(BASE, headers=HEADERS)

        assert response.status_code == 200
        assert response.json() == {"url": None, "enabled": False, "secret_set": False, "deliveries": []}

    def test_put_stores_url_without_disturbing_other_fields(self, table, configured):
        before = copy.deepcopy(meta(table))
        response = put(CRM_URL, False)

        assert response.status_code == 200
        assert response.json() == {"url": CRM_URL, "enabled": False, "secret_set": False, "deliveries": []}
        after = meta(table)
        assert after["webhook_url"] == CRM_URL
        assert after["webhook_enabled"] is False
        assert {k: v for k, v in after.items() if not k.startswith("webhook_")} == before
        assert client.get(BASE, headers=HEADERS).json()["url"] == CRM_URL

    def test_put_strips_whitespace_and_blank_means_none(self, table, configured):
        assert put(f"  {CRM_URL}  ", False).json()["url"] == CRM_URL
        response = put("   ", False)

        assert response.status_code == 200
        assert response.json()["url"] is None
        assert "webhook_url" not in meta(table)

    def test_enable_needs_a_secret(self, table, configured):
        response = put(CRM_URL, True)

        assert response.status_code == 400
        assert response.json() == {"detail": "Generate a signing secret before enabling the webhook"}
        assert "webhook_url" not in meta(table)

    def test_enable_needs_a_url(self, table, configured):
        with_secret(table, url=None)
        response = put(None, True)

        assert response.status_code == 400
        assert response.json() == {"detail": "Set a webhook URL before enabling the webhook"}
        assert meta(table)["webhook_enabled"] is False

    def test_enable_with_url_and_secret(self, table, configured):
        with_secret(table, url=None)
        response = put(CRM_URL, True)

        assert response.status_code == 200
        body = response.json()
        assert (body["url"], body["enabled"], body["secret_set"]) == (CRM_URL, True, True)
        assert "s" * 43 not in response.text
        assert meta(table)["webhook_enabled"] is True

    def test_put_null_url_removes_it_and_disables(self, table, configured):
        with_secret(table, enabled=True)
        response = put(None, False)

        assert response.status_code == 200
        assert response.json()["url"] is None
        assert response.json()["enabled"] is False
        assert "webhook_url" not in meta(table)
        assert meta(table)["webhook_secret_enc"] == sealed("s" * 43)  # the secret stays

    @pytest.mark.parametrize(
        ("url", "message"),
        [
            ("http://crm.example.com/hook", "must use https"),
            ("https://user:pw@crm.example.com/hook", "must not contain credentials"),
            ("https://169.254.169.254/latest/meta-data/", "private, loopback, link-local or reserved"),
            ("https://10.0.0.5/hook", "private, loopback, link-local or reserved"),
            ("https://127.0.0.1:8080/hook", "private, loopback, link-local or reserved"),
            ("https://crm.example.com:8080/hook", "port must be 443"),
            ("https://[::ffff:0:a9fe:a9fe]/hook", "private, loopback, link-local or reserved"),
            ("https://[::1]/hook", "private, loopback, link-local or reserved"),
            ("https://[fd00:ec2::254]/hook", "private, loopback, link-local or reserved"),
            ("https://localhost/hook", "internal name"),
            ("https://2130706433/hook", "dotted-decimal"),
            ("https://crm.example.com/" + "a" * 2030, "at most 2048 characters"),
        ],
    )
    def test_invalid_urls_are_400_and_nothing_is_written(self, table, configured, url, message):
        response = put(url, False)

        assert response.status_code == 400
        assert message in response.json()["detail"]
        assert not table.updates
        assert "webhook_url" not in meta(table)

    def test_body_shape_is_validated(self, table, configured):
        assert client.put(BASE, headers=HEADERS, json={"url": CRM_URL}).status_code == 422
        assert client.put(BASE, headers=HEADERS, json={"url": CRM_URL, "enabled": False, "x": 1}).status_code == 422

    def test_changed_meanwhile_is_409(self, table, configured):
        with_secret(table, url=None)
        with patch("app.routers.integrations.put_webhook_settings", return_value=False):
            response = put(CRM_URL, True)

        assert response.status_code == 409


# ------------------------------------------------------------------ secret
class TestSecret:
    def test_secret_is_returned_once_and_only_flagged_later(self, table, configured, kms):
        response = client.post(f"{BASE}/secret", headers=HEADERS)

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        secret = response.json()["secret"]
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", secret)

        got = client.get(BASE, headers=HEADERS)
        assert got.json()["secret_set"] is True
        assert secret not in got.text
        assert secret not in put(CRM_URL, True).text

    def test_only_the_kms_ciphertext_is_stored(self, table, configured, kms):
        secret = client.post(f"{BASE}/secret", headers=HEADERS).json()["secret"]

        stored = meta(table)
        assert "webhook_secret" not in stored
        assert secret not in json.dumps(stored)
        # Encrypted with the webhook key and bound to this project (the Lambda decrypts with the same context).
        assert kms.encrypts == [
            {"KeyId": KEY_ARN, "EncryptionContext": {"project_id": PROJECT_ID, "purpose": "webhook-signing-secret"}}
        ]
        assert kms.open(stored["webhook_secret_enc"]) == secret
        with pytest.raises(ClientError):
            kms.open(stored["webhook_secret_enc"], project_id="proj_other")

    def test_new_secret_replaces_the_old_one(self, table, configured, kms):
        first = client.post(f"{BASE}/secret", headers=HEADERS).json()["secret"]
        second = client.post(f"{BASE}/secret", headers=HEADERS).json()["secret"]

        assert first != second
        assert kms.open(meta(table)["webhook_secret_enc"]) == second
        assert {k: v for k, v in meta(table).items() if not k.startswith("webhook_")} == META

    def test_a_plaintext_secret_of_an_earlier_build_is_not_a_secret_and_is_removed(self, table, configured):
        meta(table)["webhook_secret"] = "p" * 43

        assert client.get(BASE, headers=HEADERS).json()["secret_set"] is False
        assert put(CRM_URL, True).status_code == 400  # enabling still needs a (new) secret
        assert client.post(f"{BASE}/secret", headers=HEADERS).status_code == 200
        assert "webhook_secret" not in meta(table)

    def test_key_not_configured_is_503_and_nothing_is_stored(self, table, configured, monkeypatch):
        monkeypatch.setattr(get_config(), "webhook_secret_key_arn", "")

        response = client.post(f"{BASE}/secret", headers=HEADERS)

        assert response.status_code == 503
        assert response.json() == {"detail": "Webhook secret encryption is not configured"}
        assert not table.updates

    def test_kms_failure_is_502_and_nothing_is_stored(self, table, configured, kms):
        kms.encrypt_error = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Encrypt")

        response = client.post(f"{BASE}/secret", headers=HEADERS)

        assert response.status_code == 502
        assert response.json() == {
            "detail": "The signing secret could not be encrypted: encrypt failed (AccessDeniedException)"
        }
        assert not table.updates
        assert "secret" not in response.json()


# ------------------------------------------------------------------ deliveries
class TestDeliveries:
    def test_last_20_of_the_retention_window_newest_first(self, table, configured):
        now = datetime.now(UTC)
        for n in range(25):
            table.put_item(delivery_item(now - timedelta(hours=n), n))
        table.put_item(delivery_item(now - timedelta(days=8), 99))  # past the window, TTL not run yet
        table.put_item({"PK": f"PROJ#{PROJECT_ID}", "SK": f"FCASK#{_iso(now)}#x", "input_tokens": 1})
        table.put_item({"PK": f"PROJ#{PROJECT_ID}", "SK": "DOC#doc-1", "data": {}})
        table.put_item(delivery_item(now, 7, PK="PROJ#proj_other"))
        table.put_item(
            delivery_item(
                now + timedelta(seconds=1),
                100,
                event="file_check.completed",
                status="failed",
                http_status=Decimal(503),
                error="HTTP 503",
                applicant="Sneha Anil Kulkarni",
                attempts=Decimal(3),
            )
        )

        response = client.get(BASE, headers=HEADERS)

        assert response.status_code == 200
        deliveries = response.json()["deliveries"]
        assert len(deliveries) == 20
        assert [d["delivery_id"] for d in deliveries] == ["d-100"] + [f"d-{n}" for n in range(19)]
        assert deliveries[0] == {
            "delivery_id": "d-100",
            "at": _iso(now + timedelta(seconds=1)),
            "event": "file_check.completed",
            "applicant": "Sneha Anil Kulkarni",
            "status": "failed",
            "http_status": 503,
            "error": "HTTP 503",
        }
        assert deliveries[1]["http_status"] == 200 and deliveries[1]["error"] is None
        (query,) = table.queries
        assert query["ScanIndexForward"] is False and query["Limit"] == 20


# ------------------------------------------------------------------ test event
class TestTestEvent:
    def test_needs_url_and_secret(self, table, configured):
        response = client.post(f"{BASE}/test", headers=HEADERS)

        assert response.status_code == 400
        assert response.json() == {"detail": "Set a webhook URL and generate a signing secret first"}

    def test_not_configured_is_503(self, table, monkeypatch):
        monkeypatch.setattr(get_config(), "webhook_function_name", "")
        with_secret(table)

        response = client.post(f"{BASE}/test", headers=HEADERS)

        assert response.status_code == 503

    def test_sends_through_the_lambda_even_while_disabled(self, table, configured):
        with_secret(table, enabled=False)
        stub = canned_lambda({"delivery_id": "d-1", "status": "delivered", "http_status": 204, "error": None})
        with use_lambda(stub):
            response = client.post(f"{BASE}/test", headers=HEADERS)

        assert response.status_code == 200
        assert response.json() == {"delivery_id": "d-1", "status": "delivered", "http_status": 204, "error": None}
        (call,) = stub.invoke.call_args_list
        assert call.kwargs["FunctionName"] == FUNCTION_NAME
        assert call.kwargs["InvocationType"] == "RequestResponse"
        assert json.loads(call.kwargs["Payload"]) == {"project_id": PROJECT_ID, "event": "test"}

    def test_crm_failure_is_a_failed_result_not_an_error(self, table, configured):
        with_secret(table)
        stub = canned_lambda({"delivery_id": "d-2", "status": "failed", "http_status": 500, "error": "HTTP 500"})
        with use_lambda(stub):
            response = client.post(f"{BASE}/test", headers=HEADERS)

        assert response.status_code == 200
        assert response.json() == {"delivery_id": "d-2", "status": "failed", "http_status": 500, "error": "HTTP 500"}

    def test_one_test_per_project_every_10_seconds(self, table, configured, monkeypatch):
        clock = {"now": 1000.0}
        monkeypatch.setattr(webhook_delivery, "_monotonic", lambda: clock["now"])
        with_secret(table)
        stub = repeating_lambda({"delivery_id": "d-1", "status": "delivered", "http_status": 204, "error": None})
        with use_lambda(stub):
            assert client.post(f"{BASE}/test", headers=HEADERS).status_code == 200
            clock["now"] += 4
            second = client.post(f"{BASE}/test", headers=HEADERS)
            assert second.status_code == 429
            assert second.headers["retry-after"] == "6"
            assert second.json() == {"detail": "Wait 6 s before sending another test event"}
            # Another project is not held back by this one.
            other = copy.deepcopy(META)
            other.update(PK="PROJ#proj_other", webhook_url=CRM_URL, webhook_secret_enc=sealed("o" * 43, "proj_other"))
            table.put_item(other)
            assert client.post("/projects/proj_other/integrations/webhook/test", headers=HEADERS).status_code == 200
            clock["now"] += 6
            assert client.post(f"{BASE}/test", headers=HEADERS).status_code == 200
        assert stub.invoke.call_count == 3

    def test_only_a_few_tests_run_at_a_time(self, table, configured):
        with_secret(table)
        for n in range(webhook_delivery.MAX_CONCURRENT_TESTS):
            webhook_delivery._begin_test(f"proj_busy{n}")
        stub = canned_lambda({"delivery_id": "d-1", "status": "delivered", "http_status": 204, "error": None})
        with use_lambda(stub):
            response = client.post(f"{BASE}/test", headers=HEADERS)
            assert response.status_code == 429
            assert "Too many test events" in response.json()["detail"]
            stub.invoke.assert_not_called()
            webhook_delivery._end_test()
            webhook_delivery._last_test_at.pop(PROJECT_ID, None)  # the refused call did not count
            assert client.post(f"{BASE}/test", headers=HEADERS).status_code == 200

    def test_a_failed_invoke_frees_its_slot(self, table, configured):
        with_secret(table)
        error = ClientError({"Error": {"Code": "TooManyRequestsException", "Message": "x"}}, "Invoke")
        with use_lambda(canned_lambda(error=error)):
            assert client.post(f"{BASE}/test", headers=HEADERS).status_code == 502
        assert webhook_delivery._running_tests == 0

    @pytest.mark.parametrize(
        ("stub", "status"),
        [
            (canned_lambda({"status": "skipped", "reason": "webhook URL or secret not set"}), 409),
            (canned_lambda({"errorMessage": "boom"}, function_error="Unhandled"), 502),
            (canned_lambda(raw=b"not json"), 502),
            (canned_lambda({"status": "error", "error": "webhook failed: KeyError"}), 502),
            (
                canned_lambda(
                    error=ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Invoke")
                ),
                502,
            ),
        ],
    )
    def test_lambda_problems(self, table, configured, stub, status):
        with_secret(table)
        with use_lambda(stub):
            response = client.post(f"{BASE}/test", headers=HEADERS)

        assert response.status_code == status


# ------------------------------------------------------------------ invoke: never sent twice
LAMBDA_URL = "https://lambda.ap-south-1.amazonaws.com"
DELIVERED = {"delivery_id": "d-1", "status": "delivered", "http_status": 204, "error": None}
INVOKE_PATH = f"/2015-03-31/functions/{FUNCTION_NAME}/invocations"


def lambda_answer(payload=DELIVERED):
    return {"StatusCode": 200, "Payload": io.BytesIO(json.dumps(payload).encode("utf-8"))}


class TestInvokeRetry:
    """Every run of the delivery Lambda sends the event, so an invoke is sent again only when it cannot have run."""

    def test_every_call_gets_a_new_client_that_never_retries(self):
        first = webhook_delivery.get_webhook_lambda_client()
        second = webhook_delivery.get_webhook_lambda_client()

        assert first is not second
        assert first.meta.config.retries["total_max_attempts"] == 1
        assert (first.meta.config.connect_timeout, first.meta.config.read_timeout) == (3, 35)

    @pytest.mark.parametrize(
        "error",
        [
            EndpointConnectionError(endpoint_url=LAMBDA_URL),
            ConnectTimeoutError(endpoint_url=LAMBDA_URL),
            ClientError({"Error": {"Code": "TooManyRequestsException", "Message": "Rate Exceeded."}}, "Invoke"),
        ],
        ids=["no-connection", "connect-timeout", "throttled"],
    )
    def test_an_invoke_that_did_not_start_is_sent_once_more(self, table, configured, monkeypatch, error):
        pauses = []
        monkeypatch.setattr(webhook_delivery, "_sleep", pauses.append)
        with_secret(table)
        stub = MagicMock()
        stub.invoke.side_effect = [error, lambda_answer()]
        with use_lambda(stub):
            response = client.post(f"{BASE}/test", headers=HEADERS)

        assert response.status_code == 200
        assert response.json() == DELIVERED
        first, second = stub.invoke.call_args_list
        assert first == second
        assert pauses == [webhook_delivery.RETRY_PAUSE_S]
        stub.close.assert_called_once()

    @pytest.mark.parametrize(
        "error",
        [
            ReadTimeoutError(endpoint_url=LAMBDA_URL),
            ConnectionClosedError(endpoint_url=LAMBDA_URL),
            ClientError({"Error": {"Code": "ServiceException", "Message": "x"}}, "Invoke"),
        ],
        ids=["read-timeout", "connection-closed", "service-error"],
    )
    def test_an_invoke_that_may_have_run_is_not_sent_again(self, table, configured, monkeypatch, error):
        pauses = []
        monkeypatch.setattr(webhook_delivery, "_sleep", pauses.append)
        with_secret(table)
        stub = MagicMock()
        stub.invoke.side_effect = [error, lambda_answer()]
        with use_lambda(stub):
            response = client.post(f"{BASE}/test", headers=HEADERS)

        assert response.status_code == 502
        assert stub.invoke.call_count == 1
        assert pauses == []
        stub.close.assert_called_once()

    def test_gives_up_after_the_second_attempt(self, table, configured):
        with_secret(table)
        stub = MagicMock()
        stub.invoke.side_effect = [EndpointConnectionError(endpoint_url=LAMBDA_URL)] * 2 + [lambda_answer()]
        with use_lambda(stub):
            response = client.post(f"{BASE}/test", headers=HEADERS)

        assert response.status_code == 502
        assert response.json() == {"detail": "Webhook delivery failed: invoke failed (EndpointConnectionError)"}
        assert stub.invoke.call_count == 2


class FakeLambdaEndpoint:
    """Local HTTP endpoint for the real boto3 client. Each request gets the next answer, (status, error type,
    body); "drop" closes the connection without one, as when the invoke may have run."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.paths = []
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                endpoint.paths.append(self.path)
                answer = endpoint.answers.pop(0)
                if answer == "drop":
                    self.close_connection = True
                    return
                status, error_type, body = answer
                self.send_response(status)
                if error_type:
                    self.send_header("x-amzn-ErrorType", error_type)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def test_the_real_client_sends_an_invoke_that_may_have_run_only_once(table, configured, monkeypatch):
    with_secret(table)
    with FakeLambdaEndpoint("drop", "drop") as endpoint:
        monkeypatch.setenv("AWS_ENDPOINT_URL_LAMBDA", endpoint.url)
        response = client.post(f"{BASE}/test", headers=HEADERS)

    assert response.status_code == 502
    assert response.json() == {"detail": "Webhook delivery failed: invoke failed (ConnectionClosedError)"}
    assert endpoint.paths == [INVOKE_PATH]


def test_the_real_client_sends_a_throttled_invoke_once_more(table, configured, monkeypatch):
    with_secret(table)
    throttled = (429, "TooManyRequestsException", b'{"Type":"User","message":"Rate Exceeded."}')
    delivered = (200, None, json.dumps(DELIVERED).encode("utf-8"))
    with FakeLambdaEndpoint(throttled, delivered) as endpoint:
        monkeypatch.setenv("AWS_ENDPOINT_URL_LAMBDA", endpoint.url)
        response = client.post(f"{BASE}/test", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == DELIVERED
    assert endpoint.paths == [INVOKE_PATH, INVOKE_PATH]


# ------------------------------------------------------------------ end to end with the real Lambda
def _load_webhook_lambda():
    if WEBHOOK_DIR not in sys.path:
        sys.path.insert(0, WEBHOOK_DIR)
    path = os.path.join(WEBHOOK_DIR, "index.py")
    spec = importlib.util.spec_from_file_location("webhook_delivery_lambda_index", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InProcessLambda:
    """boto3 Lambda client stub that runs the real delivery handler."""

    def __init__(self, module):
        self.module = module
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        result = self.module.handler(json.loads(kwargs["Payload"]), None)
        return {"StatusCode": 200, "Payload": io.BytesIO(json.dumps(result).encode("utf-8"))}

    def close(self):
        pass


def test_end_to_end_test_event_is_signed_and_logged(table, configured, kms, monkeypatch):
    lam = _load_webhook_lambda()
    monkeypatch.setattr(lam, "_table", table)
    monkeypatch.setattr(lam, "_kms_client", kms)  # the backend's ciphertext, decrypted by the Lambda
    monkeypatch.setattr(lam, "SECRET_KEY_ARN", KEY_ARN)
    monkeypatch.setattr(lam, "_resolve", lambda host, port, type=None: [(2, 1, 6, "", ("93.184.216.34", port))])
    sent = []

    def fake_post(target, addresses, body, headers, timeout_s=None):
        sent.append((target, addresses, body, headers))
        return 200

    monkeypatch.setattr(lam, "_post_once", fake_post)

    assert put(CRM_URL, False).status_code == 200
    secret = client.post(f"{BASE}/secret", headers=HEADERS).json()["secret"]
    with use_lambda(InProcessLambda(lam)):
        response = client.post(f"{BASE}/test", headers=HEADERS)

    assert response.status_code == 200
    result = response.json()
    assert (result["status"], result["http_status"], result["error"]) == ("delivered", 200, None)

    ((target, addresses, body, headers),) = sent
    assert (target.host, target.port, target.target) == ("crm.example.com", 443, "/hooks/idp?token=abc")
    assert addresses == ["93.184.216.34"]
    assert headers["X-DocAI-Event"] == "test"
    assert headers["X-DocAI-Delivery"] == result["delivery_id"]
    assert headers["Content-Type"] == "application/json"
    assert verify_signature(secret, headers["X-DocAI-Signature"], body)
    payload = json.loads(body)
    assert payload["event"] == "test"
    assert payload["delivery_id"] == result["delivery_id"]
    assert payload["project_id"] == PROJECT_ID
    assert payload["document_id"] is None
    assert payload["results"] == []

    deliveries = client.get(BASE, headers=HEADERS).json()["deliveries"]
    assert deliveries == [
        {
            "delivery_id": result["delivery_id"],
            "at": payload["at"],
            "event": "test",
            "applicant": None,
            "status": "delivered",
            "http_status": 200,
            "error": None,
        }
    ]
    (log_item,) = [i for i in table.puts if i["SK"].startswith("WHDLV#")]
    assert secret not in json.dumps(log_item, default=str)
    assert kms.decrypts == [
        {"KeyId": KEY_ARN, "EncryptionContext": {"project_id": PROJECT_ID, "purpose": "webhook-signing-secret"}}
    ]
    assert 7 * 86400 - 60 <= log_item["expires_at"] - int(datetime.now(UTC).timestamp()) <= 7 * 86400
