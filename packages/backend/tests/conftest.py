"""Unit tests configuration module.

Every test runs isolated from AWS and from the other tests:
- fake credentials and region, no instance metadata lookups and no shared
  config/credentials files, so a client a test forgets to stub can never reach
  a real account (this machine has real credentials in ~/.aws);
- the module-level AWS clients the app caches (S3 and its presign client,
  DynamoDB, the Lambda, Bedrock and KMS clients) are reset before and after
  each test, so a client created or stubbed in one test never leaks into the
  next; so are the webhook test-event rate limits;
- the pause before a webhook invoke is sent again is skipped (a test that
  checks it replaces webhook_delivery._sleep itself).
"""

import os

import pytest

import app.ddb.client as ddb_client
import app.file_check as file_check
import app.file_check_ask as file_check_ask
import app.lancedb as lancedb
import app.routers.graph as graph_router
import app.webhook_delivery as webhook_delivery
import app.webhook_secret as webhook_secret
from app.s3 import get_s3_client, get_s3_presign_client

_FAKE_AWS_ENV = {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "ap-south-1",
    "AWS_EC2_METADATA_DISABLED": "true",
    "AWS_CONFIG_FILE": os.devnull,
    "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
}


def _reset_cached_clients() -> None:
    get_s3_client.cache_clear()
    get_s3_presign_client.cache_clear()
    ddb_client._ddb_resource = None
    file_check._lambda_client = None
    file_check_ask._bedrock_client = None
    lancedb._lambda_client = None
    graph_router._lambda_client = None
    webhook_secret._kms_client = None
    webhook_delivery.reset_test_limits()


@pytest.fixture(autouse=True)
def _isolated_aws(monkeypatch):
    for name, value in _FAKE_AWS_ENV.items():
        monkeypatch.setenv(name, value)
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(webhook_delivery, "_sleep", lambda seconds: None)
    _reset_cached_clients()
    yield
    _reset_cached_clients()
