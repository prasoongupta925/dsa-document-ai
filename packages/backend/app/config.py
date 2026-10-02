import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    aws_region: str = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"
    document_storage_bucket_name: str = ""
    backend_table_name: str = ""
    session_storage_bucket_name: str = ""
    agent_storage_bucket_name: str = ""
    step_function_arn: str = ""
    qa_regenerator_function_arn: str = ""
    lancedb_function_name: str = "idp-v2-lancedb-service"
    paddleocr_endpoint_name: str = "paddleocr-endpoint"
    paddleocr_scale_in_alarm_name: str = "idp-v2-paddleocr-scale-in"
    graph_service_function_name: str = ""
    graph_delete_queue_url: str = ""
    # Deterministic file-check Lambda (McpStack, idp-v2-file-check-mcp): name or ARN.
    file_check_function_name: str = ""
    # POST /projects/{id}/file-check/ask: AWS-sold model only (Amazon Nova 2 Lite,
    # global inference profile), called with Converse in aws_region.
    file_check_ask_model_id: str = "global.amazon.nova-2-lite-v1:0"
    # Hard cap on the estimated input tokens of one Ask call (verdict + facts +
    # page text + history + question); page text is cut first.
    file_check_ask_max_input_tokens: int = 12000
    file_check_ask_max_output_tokens: int = 1024
    # Days a stored item may live (CDK context retentionDays, default 7): the Ask
    # usage ledger items get expires_at = now + retention_days (DynamoDB TTL).
    retention_days: int = 7
    # CRM webhook delivery Lambda (WebhookStack, idp-v2-webhook-delivery): name or
    # ARN. POST /projects/{id}/integrations/webhook/test invokes it.
    webhook_function_name: str = ""
    # KMS key (WebhookStack) that encrypts the webhook signing secrets; the
    # backend may only Encrypt with it (app/webhook_secret.py).
    webhook_secret_key_arn: str = ""


@lru_cache
def get_config() -> Config:
    return Config()
