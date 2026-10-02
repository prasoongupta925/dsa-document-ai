# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from datetime import time

import pytest

from config import DEFAULT_MODEL_CHAIN, ConfigError, is_cross_region, load_settings, validate_model_chain
from conftest import BASE_ENV, make_settings


def test_defaults_are_mumbai_kimi_first():
    s = make_settings()
    assert s.region == "ap-south-1"
    # Mumbai only: both models invoked in-Region, no cross-Region profile anywhere in the chain.
    assert s.model_chain == DEFAULT_MODEL_CHAIN == ("moonshotai.kimi-k2.5", "openai.gpt-oss-120b-1:0")
    assert not any(is_cross_region(m) for m in s.model_chain)
    assert s.polly_voice == "Kajal" and s.default_language == "hi-IN"
    assert s.qa_project_name == "Telecaller QA – Sample calls"
    assert not any("LLM" in w for w in s.warnings)
    assert s.auth_enabled and s.cognito_issuer == "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_TestPool1"
    assert s.cognito_client_ids == ("client-web", "client-voice")
    assert not s.phone_enabled and not s.whatsapp_enabled
    # Room for hidden reasoning; a reply with neither speech nor a tool call after 10 s goes to the next model.
    assert s.llm_max_tokens == 1024 and s.llm_first_event_timeout_s == 8.0 and s.llm_first_output_timeout_s == 10.0
    # Plivo line: QQ for the bot's voice (Polly renders 8 kHz), MQ for the caller's audio.
    assert (s.plivo_resampler, s.plivo_input_resampler) == ("QQ", "MQ")
    assert s.max_concurrent_calls == 8


def test_tuning_settings_and_their_limits():
    s = make_settings(MAX_CONCURRENT_CALLS="4", PLIVO_RESAMPLER="vhq", PLIVO_INPUT_RESAMPLER="HQ", LLM_FIRST_OUTPUT_TIMEOUT_S="6")
    assert (s.max_concurrent_calls, s.plivo_resampler, s.plivo_input_resampler, s.llm_first_output_timeout_s) == (4, "VHQ", "HQ", 6.0)
    assert make_settings(MAX_CONCURRENT_CALLS="0").max_concurrent_calls == 1
    for bad in ({"PLIVO_RESAMPLER": "fast"}, {"PLIVO_INPUT_RESAMPLER": "X"}):
        with pytest.raises(ConfigError):
            make_settings(**bad)


@pytest.mark.parametrize("region", ["us-east-1", "ap-northeast-1", "ap-south-2"])
def test_any_other_region_is_refused(region):
    with pytest.raises(ConfigError):
        load_settings({**BASE_ENV, "AWS_REGION": region})


@pytest.mark.parametrize(
    "model",
    [
        "anthropic.claude-3-haiku-20240307-v1:0",
        "apac.anthropic.claude-sonnet-4-20250514-v1:0",
        "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "openai.gpt-5.5",
        "global.openai.gpt-6-sol",
        "cohere.command-r-v1:0",
        "writer.palmyra-x5-v1:0",
        "amazon.nova-2-sonic-v1:0",
        "amazon.nova-sonic-v1:0",
        "",
    ],
)
def test_marketplace_models_are_refused(model):
    with pytest.raises(ConfigError):
        validate_model_chain(["moonshotai.kimi-k2.5", model])
    if model:
        with pytest.raises(ConfigError):
            make_settings(LLM_MODEL_CHAIN=f"moonshotai.kimi-k2.5,{model}")


@pytest.mark.parametrize(
    "model",
    [
        "global.amazon.nova-2-lite-v1:0",
        "apac.amazon.nova-lite-v1:0",
        "in.amazon.nova-lite-v1:0",  # India profiles also route to ap-south-2 (Hyderabad)
        "global.moonshotai.kimi-k2.5",
        "apac.openai.gpt-oss-120b-1:0",
        "us.openai.gpt-oss-120b-1:0",
        "eu.amazon.nova-pro-v1:0",
    ],
)
def test_cross_region_profiles_are_refused_at_start_up(model):
    assert is_cross_region(model)
    with pytest.raises(ConfigError, match="cross-Region"):
        validate_model_chain(["moonshotai.kimi-k2.5", model])
    # Also with LLM_MUMBAI_ONLY=false: the switch cannot turn the rule off.
    for flag in ("true", "false"):
        with pytest.raises(ConfigError, match="cross-Region"):
            make_settings(LLM_MODEL_CHAIN=f"moonshotai.kimi-k2.5,{model}", LLM_MUMBAI_ONLY=flag)


def test_mumbai_only_switch_is_always_on():
    assert make_settings(LLM_MUMBAI_ONLY="true").model_chain == DEFAULT_MODEL_CHAIN
    s = make_settings(LLM_MUMBAI_ONLY="false")
    assert s.model_chain == DEFAULT_MODEL_CHAIN and any("LLM_MUMBAI_ONLY=false is ignored" in w for w in s.warnings)
    # The hosting's exact chain (deploy/template.yaml) is accepted as is; duplicates are dropped.
    s = make_settings(LLM_MODEL_CHAIN="moonshotai.kimi-k2.5,openai.gpt-oss-120b-1:0,moonshotai.kimi-k2.5", LLM_MUMBAI_ONLY="true")
    assert s.model_chain == ("moonshotai.kimi-k2.5", "openai.gpt-oss-120b-1:0")


def test_legacy_llm_model_becomes_the_first_choice():
    s = make_settings(LLM_MODEL="openai.gpt-oss-120b-1:0")
    assert s.model_chain == ("openai.gpt-oss-120b-1:0", "moonshotai.kimi-k2.5")
    s = make_settings(LLM_MODEL="qwen.qwen3-32b-v1:0")
    assert s.model_chain == ("qwen.qwen3-32b-v1:0",) + DEFAULT_MODEL_CHAIN


def test_model_params_must_be_json_objects():
    s = make_settings(LLM_MODEL_PARAMS='{"openai.gpt-oss-120b-1:0": {"reasoning_effort": "low"}}')
    assert s.model_params == {"openai.gpt-oss-120b-1:0": {"reasoning_effort": "low"}}
    with pytest.raises(ConfigError):
        make_settings(LLM_MODEL_PARAMS="{not json")
    with pytest.raises(ConfigError):
        make_settings(LLM_MODEL_PARAMS='{"m": 3}')


def test_pool_must_be_in_mumbai_and_legacy_names_work():
    s = load_settings({"AWS_REGION": "ap-south-1", "USER_POOL_ID": "ap-south-1_abc", "APP_CLIENT_ID": "c1"})
    assert s.cognito_user_pool_id == "ap-south-1_abc" and s.cognito_client_ids == ("c1",)
    with pytest.raises(ConfigError):
        load_settings({"AWS_REGION": "ap-south-1", "COGNITO_USER_POOL_ID": "us-east-1_abc"})
    s = load_settings({"AWS_REGION": "ap-south-1"})
    assert not s.auth_enabled and any("Cognito" in w for w in s.warnings)


def test_phone_switches_on_only_with_a_long_key_and_host():
    assert not make_settings(PHONE_URL_KEY="short", PUBLIC_HOST="d1.cloudfront.net").phone_enabled
    s = make_settings(PHONE_URL_KEY="k" * 32, PUBLIC_HOST="https://d1.cloudfront.net/")
    assert s.phone_enabled and s.public_host == "d1.cloudfront.net"
    assert not s.plivo_enabled
    s = make_settings(PHONE_URL_KEY="k" * 32, PUBLIC_HOST="d1.cloudfront.net", PLIVO_AUTH_ID="MA1", PLIVO_AUTH_TOKEN="t")
    assert s.plivo_enabled and not s.plivo_outbound_enabled
    assert make_settings(
        PHONE_URL_KEY="k" * 32, PUBLIC_HOST="d1.cloudfront.net", PLIVO_AUTH_ID="MA1", PLIVO_AUTH_TOKEN="t", PLIVO_NUMBER="+91" "2212345678"
    ).plivo_outbound_enabled


def test_whatsapp_stun_stays_in_mumbai_by_default():
    # NAT discovery for WhatsApp WebRTC: AWS's STUN server in ap-south-1, not a third-party one.
    assert make_settings().whatsapp_stun_urls == ("stun:stun.kinesisvideo.ap-south-1.amazonaws.com:443",)
    assert make_settings(WHATSAPP_STUN_URLS="").whatsapp_stun_urls == ()  # empty: host candidates only
    assert make_settings(WHATSAPP_STUN_URLS="stun:a:3478, stun:b:3478").whatsapp_stun_urls == ("stun:a:3478", "stun:b:3478")


def test_outbound_guard_settings():
    s = make_settings(OUTBOUND_ALLOWED_TO="+91" "9800000001, +91" "9800000002", CALL_WINDOW="09:30-18:30", CALL_DAYS="mon-fri")
    assert s.outbound_allowed_to == ("+91" "9800000001", "+91" "9800000002")
    assert s.call_window == (time(9, 30), time(18, 30)) and s.call_days == frozenset(range(5))
    for bad in ({"OUTBOUND_ALLOWED_TO": "9800000001"}, {"CALL_WINDOW": "19:00-10:00"}, {"CALL_DAYS": "funday"}, {"TELECALLER_NUMBER": "+1415555"}):
        with pytest.raises(ConfigError):
            make_settings(**bad)


def test_secrets_can_come_from_ssm():
    class FakeSsm:
        def __init__(self):
            self.asked = []

        def get_parameter(self, Name, WithDecryption):
            self.asked.append((Name, WithDecryption))
            if Name == "/voicebot/plivo-token":
                return {"Parameter": {"Value": "from-ssm"}}
            raise RuntimeError("AccessDenied")

    ssm = FakeSsm()
    env = {**BASE_ENV, "PLIVO_AUTH_TOKEN_SSM": "/voicebot/plivo-token", "PHONE_URL_KEY_SSM": "/voicebot/missing"}
    s = load_settings(env, ssm_client=ssm)
    assert s.plivo_auth_token == "from-ssm"
    assert s.phone_url_key == "" and any("PHONE_URL_KEY" in w for w in s.warnings)
    assert ("/voicebot/plivo-token", True) in ssm.asked
    # An explicit value wins and SSM is not asked.
    ssm2 = FakeSsm()
    load_settings({**env, "PLIVO_AUTH_TOKEN": "explicit", "PHONE_URL_KEY": "k" * 32}, ssm_client=ssm2)
    assert ssm2.asked == []
