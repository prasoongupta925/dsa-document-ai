# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Test setup: no network, no real AWS (fake static credentials, IMDS off), Mumbai region."""

import os

os.environ.update(
    AWS_REGION="ap-south-1",
    AWS_DEFAULT_REGION="ap-south-1",
    AWS_ACCESS_KEY_ID="AKIAFAKEFAKEFAKEFAKE",
    AWS_SECRET_ACCESS_KEY="fake/secret/key/for/tests/only/0000000000",
    AWS_SESSION_TOKEN="fake-session-token",
    AWS_EC2_METADATA_DISABLED="true",
    AWS_CONFIG_FILE="/dev/null",
    AWS_SHARED_CREDENTIALS_FILE="/dev/null",
)
for name in ("AWS_PROFILE", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI"):
    os.environ.pop(name, None)

import pytest  # noqa: E402
from loguru import logger  # noqa: E402

import llm  # noqa: E402
from config import load_settings  # noqa: E402
from fakes import IDP_URL  # noqa: E402

logger.remove()


BASE_ENV = {
    "AWS_REGION": "ap-south-1",
    "IDP_API_URL": IDP_URL,
    "COGNITO_USER_POOL_ID": "ap-south-1_TestPool1",
    "COGNITO_APP_CLIENT_IDS": "client-web,client-voice",
}


def make_settings(**overrides):
    env = dict(BASE_ENV)
    env.update({k: str(v) for k, v in overrides.items()})
    return load_settings(env)


@pytest.fixture
def settings(tmp_path):
    return make_settings(RECORDINGS_DIR=str(tmp_path / "rec"))


@pytest.fixture(autouse=True)
def _fresh_model_health():
    llm.reset_model_health()
    yield
    llm.reset_model_health()
