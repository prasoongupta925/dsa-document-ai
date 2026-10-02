"""
Bidirectional Voice Model Configuration

The voice agent runs on Amazon Nova 2 Sonic only. It is sold by AWS on Bedrock and
authenticates with the container's IAM role, so there are no API keys. Third-party
realtime models (Gemini Live, OpenAI Realtime) are not supported.

Voices (Nova 2 Sonic voice ids):
   - tiffany (female), matthew (male): polyglot, speak every Nova 2 Sonic language
   - kiara (female), arjun (male): Indian English and Hindi; kiara is the Hindi default
"""

import logging
import os
from enum import StrEnum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

NOVA_SONIC_MODEL_ID = "amazon.nova-2-sonic-v1:0"

# Voice id -> voice gender (the prompt asks for matching Hindi verb forms).
NOVA_SONIC_VOICES: dict[str, str] = {
    "tiffany": "female",
    "matthew": "male",
    "kiara": "female",
    "arjun": "male",
}
DEFAULT_VOICE = "tiffany"
HINDI_DEFAULT_VOICE = "kiara"


class BidiModelType(StrEnum):
    """Supported bidirectional voice model types."""

    NOVA_SONIC = "nova_sonic"  # Amazon Nova 2 Sonic on Bedrock (IAM role, no API key)


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local", env_file_encoding="utf-8", extra="ignore"
    )

    aws_region: str = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"
    # Region for the Nova Sonic voice model (env VOICE_MODEL_REGION). Nova Sonic is
    # not offered in every region (e.g. ap-south-1); empty = aws_region.
    voice_model_region: str = ""
    agent_storage_bucket_name: str = ""
    session_storage_bucket_name: str = ""
    mcp_gateway_url: str = ""


@lru_cache
def get_config() -> Config:
    return Config()


def resolve_voice(voice: str | None, language: str | None = None) -> str:
    """Return the voice if Nova Sonic offers it, else the default (kiara for Hindi)."""
    if isinstance(voice, str) and voice in NOVA_SONIC_VOICES:
        return voice
    return HINDI_DEFAULT_VOICE if language == "Hindi" else DEFAULT_VOICE


def create_bidi_model(
    model_type: str = BidiModelType.NOVA_SONIC,
    voice: str | None = None,
    language: str | None = None,
):
    """
    Create the Nova 2 Sonic BidiModel (Strands SDK).

    Args:
        model_type: Must be "nova_sonic"; no other voice model is available
        voice: Nova Sonic voice id; unknown ids fall back to the default voice
        language: User's default language (e.g. "Hindi"), picks the default voice

    Raises:
        ValueError: model_type is not "nova_sonic"
    """
    if model_type != BidiModelType.NOVA_SONIC:
        # Short ASCII: main.py sends this as the WebSocket close reason (max 123 bytes)
        raise ValueError(f"Unsupported voice model: {model_type!a:.40}. Only nova_sonic is available.")

    from strands.experimental.bidi.models import BidiNovaSonicModel

    config = get_config()
    nova_voice = resolve_voice(voice, language)
    logger.info(f"Creating nova_sonic model with voice={nova_voice}")
    return BidiNovaSonicModel(
        model_id=NOVA_SONIC_MODEL_ID,
        provider_config={
            "audio": {"voice": nova_voice},
        },
        client_config={"region": config.voice_model_region or config.aws_region},
    )
