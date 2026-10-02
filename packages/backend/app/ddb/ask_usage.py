"""Usage ledger of POST /projects/{id}/file-check/ask (the Ask cost meter).

One item per model call, in the project's partition:
  PK = PROJ#{project_id}, SK = FCASK#{iso timestamp}#{uuid}
with input_tokens, output_tokens, cost_usd, model_id, created_at and
expires_at (epoch seconds). DynamoDB TTL on expires_at (StorageStack) deletes
the item after the retention period; nothing else reads or writes that
attribute. No question, answer or document content is stored.

The SK starts with FCASK#, so every other reader of the partition (DOC#,
FACTS#, WF#, DATASET# prefixes) ignores these items; deleting the project
deletes them with it.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Key

from app.ddb.client import get_table

ASK_SK_PREFIX = "FCASK#"
# "$" sorts right after "#", so it bounds every FCASK#... key from above.
_ASK_SK_UPPER = "FCASK$"
TTL_ATTRIBUTE = "expires_at"


def _iso(ts: datetime) -> str:
    """Fixed-width UTC ISO timestamp, so SK order is time order."""
    return ts.astimezone(UTC).isoformat(timespec="microseconds")


def put_ask_usage(
    project_id: str,
    *,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    retention_days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write one ledger item and return it."""
    ts = now or datetime.now(UTC)
    created_at = _iso(ts)
    item = {
        "PK": f"PROJ#{project_id}",
        "SK": f"{ASK_SK_PREFIX}{created_at}#{uuid.uuid4().hex}",
        "created_at": created_at,
        "model_id": model_id,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cost_usd": Decimal(str(cost_usd)),
        TTL_ATTRIBUTE: int(ts.timestamp()) + int(retention_days) * 86400,
    }
    get_table().put_item(Item=item)
    return item


def sum_ask_usage(project_id: str, *, window_days: int, now: datetime | None = None) -> dict[str, Any]:
    """Calls, tokens and cost of the Ask calls made in the last `window_days` days.

    Selects by the timestamp in the SK, so items that are past expires_at but
    not yet removed by TTL (removal is asynchronous) are still counted only
    while inside the window.
    """
    ts = now or datetime.now(UTC)
    lower = f"{ASK_SK_PREFIX}{_iso(ts - timedelta(days=window_days))}"
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(f"PROJ#{project_id}") & Key("SK").between(lower, _ASK_SK_UPPER),
        "ProjectionExpression": "input_tokens, output_tokens, cost_usd",
    }
    calls = input_tokens = output_tokens = 0
    cost = Decimal("0")
    table = get_table()
    while True:
        response = table.query(**kwargs)
        for item in response.get("Items", []):
            calls += 1
            input_tokens += int(item.get("input_tokens") or 0)
            output_tokens += int(item.get("output_tokens") or 0)
            cost += Decimal(str(item.get("cost_usd") or 0))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    return {
        "window_days": window_days,
        "calls": calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(float(cost), 8),
    }
