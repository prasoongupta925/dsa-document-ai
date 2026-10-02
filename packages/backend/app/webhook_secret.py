"""Encryption of the CRM webhook signing secrets (the KMS key of WebhookStack).

POST /projects/{id}/integrations/webhook/secret generates a secret, returns it
once and stores only the ciphertext this module makes (webhook_secret_enc on
the project META item, base64). The backend role may only Encrypt with the
key; the delivery Lambda is the only role that may Decrypt. Both calls use
webhook_security.secret_encryption_context(project_id), so a ciphertext only
decrypts for its own project, and the key policy (IAM) allows neither call
without that context's purpose.
"""

import base64

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.config import get_config
from app.webhook_security import secret_encryption_context

_kms_client = None


class SecretKeyNotConfiguredError(Exception):
    """WEBHOOK_SECRET_KEY_ARN is not set."""


class SecretEncryptionError(Exception):
    """KMS could not encrypt the secret; the message says why (never the secret)."""


def get_kms_client():
    global _kms_client
    if _kms_client is None:
        config = get_config()
        _kms_client = boto3.client(
            "kms",
            region_name=config.aws_region,
            config=BotoConfig(connect_timeout=5, read_timeout=10, retries={"max_attempts": 3, "mode": "standard"}),
        )
    return _kms_client


def encrypt_secret(project_id: str, secret: str) -> str:
    """Base64 of the KMS ciphertext of `secret`, bound to the project by the encryption context."""
    key_arn = get_config().webhook_secret_key_arn
    if not key_arn:
        raise SecretKeyNotConfiguredError("webhook secret key is not configured")
    try:
        response = get_kms_client().encrypt(
            KeyId=key_arn,
            Plaintext=secret.encode("utf-8"),
            EncryptionContext=secret_encryption_context(project_id),
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code") or "ClientError"
        raise SecretEncryptionError(f"encrypt failed ({code})") from e
    except BotoCoreError as e:
        raise SecretEncryptionError(f"encrypt failed ({type(e).__name__})") from e
    return base64.b64encode(response["CiphertextBlob"]).decode("ascii")
