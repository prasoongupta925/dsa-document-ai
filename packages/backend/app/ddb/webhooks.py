"""Webhook settings and delivery log of a project (the CRM verdict push).

Settings are top-level attributes of the project META item
(PK = PROJ#{project_id}, SK = META): webhook_url, webhook_enabled and
webhook_secret_enc, the signing secret encrypted with the webhook KMS key
(app/webhook_secret.py; only the delivery Lambda can decrypt it). They sit
beside `data`, so update_project_data (which replaces `data`) never touches
them, and every write here sets or removes only these attributes (and the
plaintext webhook_secret of earlier builds, which is never read). Reads project
PK and the three attributes only; the ciphertext never leaves this module
except as `secret_set`.

The delivery Lambda (packages/infra/src/functions/webhook) writes one item per
delivery in the project's partition:
  PK = PROJ#{project_id}, SK = WHDLV#{iso timestamp}#{delivery_id}
with delivery_id, at, event, status, http_status, error, applicant, attempts,
document_id and expires_at (epoch seconds, now + retention days). DynamoDB TTL
on expires_at deletes them; the other readers of the partition (DOC#, FACTS#,
WF#, DATASET#, FCASK# prefixes) ignore them; deleting the project deletes them.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from app.ddb.client import get_table

DELIVERY_SK_PREFIX = "WHDLV#"
# "$" sorts right after "#", so it bounds every WHDLV#... key from above.
_DELIVERY_SK_UPPER = "WHDLV$"


@dataclass(frozen=True)
class WebhookSettings:
    url: str | None
    enabled: bool
    secret_set: bool


def _key(project_id: str) -> dict[str, str]:
    return {"PK": f"PROJ#{project_id}", "SK": "META"}


def iso_timestamp(ts: datetime) -> str:
    """Fixed-width UTC ISO timestamp (the delivery Lambda writes the same), so SK order is time order."""
    return ts.astimezone(UTC).isoformat(timespec="microseconds")


def get_webhook_settings(project_id: str) -> WebhookSettings | None:
    """The project's webhook settings, or None when the project does not exist."""
    response = get_table().get_item(
        Key=_key(project_id),
        ProjectionExpression="PK, webhook_url, webhook_enabled, webhook_secret_enc",
        ConsistentRead=True,
    )
    item = response.get("Item")
    if not item:
        return None
    url = item.get("webhook_url")
    secret_enc = item.get("webhook_secret_enc")
    return WebhookSettings(
        url=url if isinstance(url, str) and url else None,
        enabled=item.get("webhook_enabled") is True,
        secret_set=isinstance(secret_enc, str) and bool(secret_enc),
    )


def _conditional_update(project_id: str, **kwargs: Any) -> bool:
    """update_item on the META item; False when its condition failed."""
    try:
        get_table().update_item(Key=_key(project_id), **kwargs)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise
    return True


def put_webhook_settings(project_id: str, *, url: str | None, enabled: bool) -> bool:
    """Set webhook_url (or remove it when None) and webhook_enabled.

    Written only while the project exists and, when enabling, while it has a
    secret; returns False otherwise (nothing written).
    """
    values: dict[str, Any] = {":enabled": enabled}
    if url is None:
        expression = "SET webhook_enabled = :enabled REMOVE webhook_url"
    else:
        expression = "SET webhook_url = :url, webhook_enabled = :enabled"
        values[":url"] = url
    condition = "attribute_exists(PK)"
    if enabled:
        condition += " AND attribute_exists(webhook_secret_enc)"
    return _conditional_update(
        project_id,
        UpdateExpression=expression,
        ConditionExpression=condition,
        ExpressionAttributeValues=values,
    )


def put_webhook_secret(project_id: str, secret_enc: str) -> bool:
    """Store a new encrypted signing secret (replacing the old one); False when the project does not exist."""
    return _conditional_update(
        project_id,
        UpdateExpression="SET webhook_secret_enc = :secret REMOVE webhook_secret",
        ConditionExpression="attribute_exists(PK)",
        ExpressionAttributeValues={":secret": secret_enc},
    )


def list_webhook_deliveries(
    project_id: str, *, window_days: int, limit: int = 20, now: datetime | None = None
) -> list[dict[str, Any]]:
    """The newest `limit` delivery items of the last `window_days` days, newest first.

    Selects by the timestamp in the SK, so items past expires_at that TTL has
    not removed yet (removal is asynchronous) are never listed.
    """
    ts = now or datetime.now(UTC)
    lower = f"{DELIVERY_SK_PREFIX}{iso_timestamp(ts - timedelta(days=window_days))}"
    response = get_table().query(
        KeyConditionExpression=Key("PK").eq(f"PROJ#{project_id}") & Key("SK").between(lower, _DELIVERY_SK_UPPER),
        ScanIndexForward=False,
        Limit=limit,
    )
    return list(response.get("Items", []))
