import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.local", env_file_encoding="utf-8", extra="ignore")

    aws_region: str = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"
    session_storage_bucket_name: str = ""
    agent_storage_bucket_name: str = ""
    mcp_gateway_url: str = ""
    agentcore_runtime_id: str = ""
    backend_table_name: str = ""
    websocket_message_queue_url: str = ""
    bedrock_model_id: str = "zai.glm-5"
    code_interpreter_identifier: str = ""

    @property
    def is_agentcore(self) -> bool:
        return bool(self.agentcore_runtime_id)


@lru_cache
def get_config() -> Config:
    return Config()
