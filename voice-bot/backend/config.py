# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Runtime configuration for the DSA Document AI voice bot.

Everything comes from environment variables. A secret can instead name an SSM
parameter (SecureString) in ``<NAME>_SSM``; it is read once at start-up with the
server role. No secret has a default value in code.

Hard rules enforced here (a broken one stops the server at start-up):
- every AWS call goes to ap-south-1 (Mumbai);
- the LLM chain holds only AWS-sold Bedrock text models (never Anthropic, Cohere,
  TwelveLabs, Stability, Writer, Luma or OpenAI GPT-5/6, which are Marketplace;
  never Nova Sonic: speech here is Transcribe + Polly);
- the models are invoked in-Region by their foundation-model id: no cross-Region
  inference profile (global., apac., in. ...), which could run them outside
  Mumbai (in. profiles also route to ap-south-2).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass, field
from datetime import time as dtime
from functools import lru_cache

MUMBAI = "ap-south-1"

# Both on-demand in ap-south-1. The server role may invoke exactly these (deploy/template.yaml).
DEFAULT_MODEL_CHAIN = (
    "moonshotai.kimi-k2.5",
    "openai.gpt-oss-120b-1:0",
)

# AWS-sold model families only, as in-Region foundation-model ids (no inference-profile prefix).
_ALLOWED_MODEL = re.compile(
    r"^("
    r"moonshotai\.kimi-[a-z0-9.\-:]+"
    r"|openai\.gpt-oss-[a-z0-9.\-:]+"
    r"|amazon\.nova-[a-z0-9.\-:]+"
    r"|deepseek\.[a-z0-9.\-:]+"
    r"|qwen\.[a-z0-9.\-:]+"
    r"|zai\.[a-z0-9.\-:]+"
    r")$"
)
_BLOCKED_MODEL = re.compile(r"anthropic|cohere|twelvelabs|stability|writer|luma|openai\.gpt-[5-9]|nova-sonic|nova-2-sonic")
# Cross-Region (geographic or global) inference-profile prefixes.
_CROSS_REGION_PREFIX = re.compile(r"^(global|apac|in|us|us-gov|eu|jp|au|ca|sa|me|af|mx)\.")

QA_PROJECT_NAME_DEFAULT = "Telecaller QA – Sample calls"
TRANSCRIBE_LANGUAGES = ("hi-IN", "en-IN", "mr-IN")
# WhatsApp WebRTC NAT discovery: AWS's public STUN server in Mumbai (Kinesis Video Streams), so even
# this step stays in ap-south-1. Only the server's public address is learned; no audio goes through it.
MUMBAI_STUN_URL = "stun:stun.kinesisvideo.ap-south-1.amazonaws.com:443"


class ConfigError(ValueError):
    """Raised for a configuration that breaks a hard rule."""


def _env(env: dict, name: str, default: str = "") -> str:
    value = env.get(name)
    return default if value is None else value.strip()


def _bool(env: dict, name: str, default: bool) -> bool:
    value = _env(env, name)
    if not value:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _int(env: dict, name: str, default: int) -> int:
    value = _env(env, name)
    return int(value) if value else default


def _float(env: dict, name: str, default: float) -> float:
    value = _env(env, name)
    return float(value) if value else default


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def exotel_url_key(phone_url_key: str) -> str:
    """The secret in Exotel's stream and status URLs, derived from PHONE_URL_KEY (HMAC-SHA256).

    Exotel account users see these URLs (applet settings, call logs), so they must not carry
    PHONE_URL_KEY itself: that key seals the call tokens (outbound call context, Plivo streams),
    and whoever has it can forge a context that skips caller verification. Stdlib only, so
    deploy/secrets.sh can print the URLs with the system python3.
    """
    if not phone_url_key:
        return ""
    mac = hmac.new(phone_url_key.encode("utf-8"), b"voicebot-url-key:exotel", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")


def is_cross_region(model_id: str) -> bool:
    """True for a cross-Region inference profile (it may run the model outside Mumbai)."""
    return bool(_CROSS_REGION_PREFIX.match(model_id))


def validate_model_chain(chain: list[str]) -> list[str]:
    """The chain without duplicates; ConfigError unless every model is AWS-sold and invoked in-Region."""
    if not chain:
        raise ConfigError("LLM_MODEL_CHAIN is empty")
    kept: list[str] = []
    for model_id in chain:
        if _BLOCKED_MODEL.search(model_id):
            raise ConfigError(f"LLM model {model_id!r} is not allowed (Marketplace-billed or Nova Sonic)")
        if is_cross_region(model_id):
            raise ConfigError(
                f"LLM model {model_id!r} is a cross-Region inference profile: Mumbai only, use the in-Region model id"
            )
        if not _ALLOWED_MODEL.match(model_id):
            raise ConfigError(f"LLM model {model_id!r} is not an allowed AWS-sold Bedrock model")
        if model_id not in kept:
            kept.append(model_id)
    return kept


def _parse_window(value: str) -> tuple[dtime, dtime]:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", value)
    if not m:
        raise ConfigError(f"CALL_WINDOW must look like 10:00-19:00, got {value!r}")
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    start, end = dtime(h1, m1), dtime(h2, m2)
    if not start < end:
        raise ConfigError("CALL_WINDOW start must be before its end")
    return start, end


_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _parse_days(value: str) -> frozenset[int]:
    days: set[int] = set()
    for part in _csv(value.lower()):
        if "-" in part:
            a, b = part.split("-", 1)
            if a not in _DAYS or b not in _DAYS:
                raise ConfigError(f"CALL_DAYS: unknown day in {part!r}")
            days.update(range(_DAYS[a], _DAYS[b] + 1))
        elif part in _DAYS:
            days.add(_DAYS[part])
        else:
            raise ConfigError(f"CALL_DAYS: unknown day {part!r}")
    return frozenset(days)


@dataclass(frozen=True)
class Settings:
    # AWS
    region: str = MUMBAI
    # Bedrock LLM
    model_chain: tuple[str, ...] = DEFAULT_MODEL_CHAIN
    model_params: dict = field(default_factory=dict)  # model id -> additionalModelRequestFields
    llm_max_tokens: int = 1024  # room for hidden reasoning; replies are kept short by the prompt and a spoken-length cap
    llm_temperature: float = 0.3
    llm_first_event_timeout_s: float = 8.0
    llm_first_output_timeout_s: float = 10.0  # no speech and no tool call by then: next model
    # Speech
    default_language: str = "hi-IN"
    polly_voice: str = "Kajal"
    vad_min_volume: float = 0.6
    user_idle_s: float = 12.0
    call_max_s: int = 600
    max_concurrent_calls: int = 8
    # Cognito (the IDP's user pool: the bot accepts the same logins)
    cognito_user_pool_id: str = ""
    cognito_client_ids: tuple[str, ...] = ()
    # IDP (DSA Document AI) backend API, AWS_IAM (SigV4) auth
    idp_api_url: str = ""
    idp_caller_id: str = "voice-bot"
    idp_timeout_s: float = 20.0
    qa_project_id: str = ""
    qa_project_name: str = QA_PROJECT_NAME_DEFAULT
    # Recording
    recording_enabled: bool = True
    recordings_dir: str = "/tmp/voicebot-recordings"
    # Persona
    dsa_name: str = "Varunika Loan Partners"
    assistant_name: str = "Varunika Loans"
    dsa_phone: str = ""
    # Phone (Plivo / Exotel)
    public_host: str = ""
    phone_url_key: str = ""
    plivo_auth_id: str = ""
    plivo_auth_token: str = ""
    plivo_number: str = ""
    plivo_api_base: str = "https://api.plivo.com"
    # soxr presets on the 8 kHz Plivo line (measured: stock VHQ holds back ~52 ms each way, plus ~100 ms at the start).
    plivo_resampler: str = "QQ"  # bot -> caller: 0 ms; Polly renders 8 kHz for Plivo, so QQ has nothing to alias
    plivo_input_resampler: str = "MQ"  # caller -> Transcribe: ~26 ms; QQ would leave images only 18 dB down
    plivo_validate_signature: bool = True
    exotel_sid: str = ""
    exotel_api_key: str = ""
    exotel_api_token: str = ""
    exotel_exophone: str = ""
    exotel_api_base: str = "https://api.in.exotel.com"
    telecaller_number: str = ""
    # Outbound guard
    outbound_allowed_to: tuple[str, ...] = ()
    call_window: tuple[dtime, dtime] = (dtime(10, 0), dtime(19, 0))
    call_days: frozenset[int] = frozenset(range(0, 6))  # Mon-Sat
    outbound_max_attempts_per_day: int = 3
    # WhatsApp Business Calling
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_stun_urls: tuple[str, ...] = (MUMBAI_STUN_URL,)
    # Server
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"
    log_json: bool = True
    allowed_origins: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    # ------------------------------------------------------------- feature flags
    @property
    def auth_enabled(self) -> bool:
        return bool(self.cognito_user_pool_id and self.cognito_client_ids)

    @property
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.cognito_user_pool_id}"

    @property
    def phone_enabled(self) -> bool:
        return len(self.phone_url_key) >= 32 and bool(self.public_host)

    @property
    def plivo_enabled(self) -> bool:
        return self.phone_enabled and bool(self.plivo_auth_id and self.plivo_auth_token)

    @property
    def exotel_enabled(self) -> bool:
        return self.phone_enabled

    @property
    def exotel_outbound_enabled(self) -> bool:
        return self.phone_enabled and bool(
            self.exotel_sid and self.exotel_api_key and self.exotel_api_token and self.exotel_exophone
        )

    @property
    def plivo_outbound_enabled(self) -> bool:
        return self.plivo_enabled and bool(self.plivo_number)

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(
            self.whatsapp_token
            and self.whatsapp_phone_number_id
            and self.whatsapp_app_secret
            and self.whatsapp_verify_token
        )


_SSM_BACKED = (
    "IDP_API_URL",
    "PHONE_URL_KEY",
    "PLIVO_AUTH_ID",
    "PLIVO_AUTH_TOKEN",
    "EXOTEL_API_KEY",
    "EXOTEL_API_TOKEN",
    "WHATSAPP_TOKEN",
    "WHATSAPP_APP_SECRET",
    "WHATSAPP_WEBHOOK_VERIFICATION_TOKEN",
)


def _resolve_ssm(env: dict, region: str, ssm_client=None) -> tuple[dict, list[str]]:
    """Fill NAME from the SSM parameter in NAME_SSM when NAME is empty."""
    resolved = dict(env)
    notes: list[str] = []
    wanted = {n: _env(env, f"{n}_SSM") for n in _SSM_BACKED if not _env(env, n) and _env(env, f"{n}_SSM")}
    if not wanted:
        return resolved, notes
    if ssm_client is None:
        import boto3

        ssm_client = boto3.client("ssm", region_name=region)
    for name, param in wanted.items():
        try:
            value = ssm_client.get_parameter(Name=param, WithDecryption=True)["Parameter"]["Value"]
            resolved[name] = value
        except Exception as e:  # noqa: BLE001 - reported, the feature stays off
            notes.append(f"{name}: SSM parameter {param} unreadable ({type(e).__name__})")
    return resolved, notes


def load_settings(env: dict | None = None, ssm_client=None) -> Settings:
    """Build Settings from the environment; raises ConfigError on a broken hard rule."""
    env = dict(os.environ if env is None else env)
    region = _env(env, "AWS_REGION") or _env(env, "AWS_DEFAULT_REGION") or MUMBAI
    if region != MUMBAI:
        raise ConfigError(f"AWS region must be {MUMBAI} (Mumbai only), got {region!r}")
    env, notes = _resolve_ssm(env, region, ssm_client)

    # Kept for the hosting's env contract: models always run in-Region, whatever it says.
    if not _bool(env, "LLM_MUMBAI_ONLY", True):
        notes.append("LLM_MUMBAI_ONLY=false is ignored: the models are always invoked in ap-south-1")
    chain_raw = _csv(_env(env, "LLM_MODEL_CHAIN")) or (
        [_env(env, "LLM_MODEL")] + list(DEFAULT_MODEL_CHAIN) if _env(env, "LLM_MODEL") else list(DEFAULT_MODEL_CHAIN)
    )
    chain = validate_model_chain(chain_raw)

    model_params_raw = _env(env, "LLM_MODEL_PARAMS")
    try:
        model_params = json.loads(model_params_raw) if model_params_raw else {}
    except json.JSONDecodeError as e:
        raise ConfigError(f"LLM_MODEL_PARAMS is not JSON: {e}") from e
    if not isinstance(model_params, dict) or not all(isinstance(v, dict) for v in model_params.values()):
        raise ConfigError("LLM_MODEL_PARAMS must map a model id to an object")

    default_language = _env(env, "DEFAULT_LANGUAGE", "hi-IN")
    if default_language not in TRANSCRIBE_LANGUAGES:
        raise ConfigError(f"DEFAULT_LANGUAGE must be one of {TRANSCRIBE_LANGUAGES}")

    pool_id = _env(env, "COGNITO_USER_POOL_ID") or _env(env, "USER_POOL_ID")
    if pool_id and not pool_id.startswith(f"{MUMBAI}_"):
        raise ConfigError("COGNITO_USER_POOL_ID must be a pool in ap-south-1")
    client_ids = tuple(_csv(_env(env, "COGNITO_APP_CLIENT_IDS") or _env(env, "APP_CLIENT_ID")))
    if not (pool_id and client_ids):
        notes.append("Cognito is not configured: the browser /ws route refuses every connection")

    idp_url = _env(env, "IDP_API_URL").rstrip("/")
    if idp_url and not re.match(r"^https://[a-z0-9]+\.execute-api\.ap-south-1\.amazonaws\.com(/.*)?$", idp_url):
        notes.append("IDP_API_URL is not an ap-south-1 execute-api URL")
    if not idp_url:
        notes.append("IDP_API_URL is not set: file check, eligibility and recording upload are off")

    phone_url_key = _env(env, "PHONE_URL_KEY")
    if phone_url_key and len(phone_url_key) < 32:
        notes.append("PHONE_URL_KEY is shorter than 32 characters: phone routes are off")

    plivo_resampler = _env(env, "PLIVO_RESAMPLER", "QQ").upper()
    plivo_input_resampler = _env(env, "PLIVO_INPUT_RESAMPLER", "MQ").upper()
    for name, value in (("PLIVO_RESAMPLER", plivo_resampler), ("PLIVO_INPUT_RESAMPLER", plivo_input_resampler)):
        if value not in ("VHQ", "HQ", "MQ", "LQ", "QQ"):
            raise ConfigError(f"{name} must be one of VHQ, HQ, MQ, LQ, QQ")

    allowed_to = tuple(_csv(_env(env, "OUTBOUND_ALLOWED_TO") or _env(env, "ALLOWED_TO")))
    for number in allowed_to + tuple(n for n in (_env(env, "TELECALLER_NUMBER"),) if n):
        if not re.fullmatch(r"\+91\d{10}", number):
            raise ConfigError("OUTBOUND_ALLOWED_TO / TELECALLER_NUMBER must be +91 numbers in E.164 form")

    log_level = _env(env, "LOG_LEVEL", "INFO").upper()
    if log_level not in ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR"):
        raise ConfigError("LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")

    return Settings(
        region=region,
        model_chain=tuple(chain),
        model_params=model_params,
        llm_max_tokens=_int(env, "LLM_MAX_TOKENS", 1024),
        llm_temperature=_float(env, "LLM_TEMPERATURE", 0.3),
        llm_first_event_timeout_s=_float(env, "LLM_FIRST_EVENT_TIMEOUT_S", 8.0),
        llm_first_output_timeout_s=_float(env, "LLM_FIRST_OUTPUT_TIMEOUT_S", 10.0),
        default_language=default_language,
        polly_voice=_env(env, "POLLY_VOICE", "Kajal"),
        vad_min_volume=_float(env, "VAD_MIN_VOLUME", 0.6),
        user_idle_s=_float(env, "USER_IDLE_SECONDS", 12.0),
        call_max_s=_int(env, "CALL_MAX_SECONDS", 600),
        max_concurrent_calls=max(1, _int(env, "MAX_CONCURRENT_CALLS", 8)),
        cognito_user_pool_id=pool_id,
        cognito_client_ids=client_ids,
        idp_api_url=idp_url,
        idp_caller_id=_env(env, "IDP_CALLER_ID", "voice-bot"),
        idp_timeout_s=_float(env, "IDP_TIMEOUT_S", 20.0),
        qa_project_id=_env(env, "QA_PROJECT_ID"),
        qa_project_name=_env(env, "QA_PROJECT_NAME", QA_PROJECT_NAME_DEFAULT),
        recording_enabled=_bool(env, "RECORDING_ENABLED", True),
        recordings_dir=_env(env, "RECORDINGS_DIR", "/tmp/voicebot-recordings"),
        dsa_name=_env(env, "DSA_NAME", "Varunika Loan Partners"),
        assistant_name=_env(env, "ASSISTANT_NAME", "Varunika Loans"),
        dsa_phone=_env(env, "DSA_PHONE"),
        public_host=_env(env, "PUBLIC_HOST").removeprefix("https://").rstrip("/"),
        phone_url_key=phone_url_key,
        plivo_auth_id=_env(env, "PLIVO_AUTH_ID"),
        plivo_auth_token=_env(env, "PLIVO_AUTH_TOKEN"),
        plivo_number=_env(env, "PLIVO_NUMBER"),
        plivo_api_base=_env(env, "PLIVO_API_BASE", "https://api.plivo.com").rstrip("/"),
        plivo_resampler=plivo_resampler,
        plivo_input_resampler=plivo_input_resampler,
        plivo_validate_signature=_bool(env, "PLIVO_VALIDATE_SIGNATURE", True),
        exotel_sid=_env(env, "EXOTEL_SID"),
        exotel_api_key=_env(env, "EXOTEL_API_KEY"),
        exotel_api_token=_env(env, "EXOTEL_API_TOKEN"),
        exotel_exophone=_env(env, "EXOTEL_EXOPHONE"),
        exotel_api_base=_env(env, "EXOTEL_API_BASE", "https://api.in.exotel.com").rstrip("/"),
        telecaller_number=_env(env, "TELECALLER_NUMBER"),
        outbound_allowed_to=allowed_to,
        call_window=_parse_window(_env(env, "CALL_WINDOW", "10:00-19:00")),
        call_days=_parse_days(_env(env, "CALL_DAYS", "mon-sat")),
        outbound_max_attempts_per_day=_int(env, "OUTBOUND_MAX_ATTEMPTS_PER_DAY", 3),
        whatsapp_token=_env(env, "WHATSAPP_TOKEN"),
        whatsapp_phone_number_id=_env(env, "WHATSAPP_PHONE_NUMBER_ID"),
        whatsapp_app_secret=_env(env, "WHATSAPP_APP_SECRET"),
        whatsapp_verify_token=_env(env, "WHATSAPP_WEBHOOK_VERIFICATION_TOKEN"),
        whatsapp_stun_urls=tuple(_csv(_env(env, "WHATSAPP_STUN_URLS", MUMBAI_STUN_URL))),
        host=_env(env, "HOST", "0.0.0.0"),
        port=_int(env, "PORT", 8080),
        log_level=log_level,
        log_json=_bool(env, "LOG_JSON", True),
        allowed_origins=tuple(_csv(_env(env, "ALLOWED_ORIGINS"))),
        warnings=tuple(notes),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def reset_settings() -> None:
    """Forget the cached settings (tests change the environment)."""
    get_settings.cache_clear()
