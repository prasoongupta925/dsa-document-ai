"""Needs-review checklist items a person confirmed (POST / DELETE .../file-check/confirmations).

A checklist item the rules cannot verify (kind manual, e.g. "Address proof" or
"Cross-check: PAN format") is REVIEW in the verdict until a person confirms
it. One item per applicant and checklist item, in the project's partition:
  PK = PROJ#{project_id}, SK = FCCONF#{applicant_key}#{item_id}
with data = {applicant_key, item_id, checklist_id, document_ids, confirmed_by,
confirmed_at, expires_at} and expires_at (epoch seconds) = confirmation time +
the retention period: DynamoDB TTL deletes the item. applicant_key is a SHA-256
of the applicant's PAN or name (the same key as engine.applicant_key in
packages/lambda/file-check-mcp), so the item holds neither in clear (the hash
is pseudonymous, not anonymous: it expires with the item); confirmed_by is the
caller's x-user-id (the web app sends the Cognito username).

The file-check Lambda reads the data with the project's DOC# and FACTS# items
and the engine counts the item as met (status CONFIRMED) while every document
in document_ids is still in the applicant's file. The engine looks under the
applicant's PAN key and name key, so undo deletes both. Confirming again
replaces the item, and deleting the project deletes it with it; erasing an
applicant (POST .../applicants/erase) deletes the applicant's items. Every
other reader of the partition (DOC#, FACTS#, WF#, ELIG# ... prefixes) ignores
these items.
"""

import hashlib
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from boto3.dynamodb.conditions import Key

from app.ddb.ask_usage import TTL_ATTRIBUTE
from app.ddb.client import get_table

CONFIRMATION_SK_PREFIX = "FCCONF#"

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def applicant_key(applicant: str) -> str:
    """SHA-256 of the PAN (any case / spacing) or of the name (case and spacing ignored), as the engine keys it."""
    compact = re.sub(r"\s", "", applicant).upper()
    basis = f"pan:{compact}" if _PAN_RE.match(compact) else "name:" + " ".join(applicant.split()).casefold()
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:40]


def confirmation_key(project_id: str, applicant: str, item_id: str) -> dict[str, str]:
    return {"PK": f"PROJ#{project_id}", "SK": f"{CONFIRMATION_SK_PREFIX}{applicant_key(applicant)}#{item_id}"}


def _iso(ts: datetime) -> str:
    """Fixed-width UTC ISO timestamp, so string order is time order (the engine keeps the latest)."""
    return ts.astimezone(UTC).isoformat(timespec="microseconds")


def put_confirmation(
    project_id: str,
    *,
    applicant: str,
    item_id: str,
    checklist_id: str | None,
    document_ids: list[str],
    confirmed_by: str,
    retention_days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write (or replace) one confirmation and return its data."""
    ts = now or datetime.now(UTC)
    expires_at = int(ts.timestamp()) + int(retention_days) * 86400
    data = {
        "applicant_key": applicant_key(applicant),
        "item_id": item_id,
        "checklist_id": checklist_id,
        "document_ids": sorted(set(document_ids)),
        "confirmed_by": confirmed_by,
        "confirmed_at": _iso(ts),
        "expires_at": expires_at,
    }
    item = {**confirmation_key(project_id, applicant, item_id), "data": data, TTL_ATTRIBUTE: expires_at}
    get_table().put_item(Item=item)
    return data


def delete_confirmation(project_id: str, *, applicants: Sequence[str], item_id: str) -> bool:
    """Delete the item's confirmation under each of the applicant's identifiers (PAN, name).

    The engine finds a confirmation under either key (one saved under the name
    before a PAN was read still applies), so undo removes both. True when there
    was one.
    """
    table = get_table()
    deleted = False
    keys = {applicant_key(a): a for a in applicants}
    for applicant in keys.values():
        response = table.delete_item(Key=confirmation_key(project_id, applicant, item_id), ReturnValues="ALL_OLD")
        deleted = bool(response.get("Attributes")) or deleted
    return deleted


def delete_applicant_confirmations(project_id: str, *, applicants: Sequence[str], document_ids: Iterable[str]) -> int:
    """Delete one applicant's confirmations (the erase): those under any of its identifiers (PAN,
    name) and those made on any of its documents (a PAN the erase was not given). How many."""
    keys = {applicant_key(a) for a in applicants if a and a.strip()}
    documents = set(document_ids)
    table = get_table()
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(f"PROJ#{project_id}") & Key("SK").begins_with(CONFIRMATION_SK_PREFIX)
    }
    doomed: list[dict[str, str]] = []
    while True:
        response = table.query(**kwargs)
        for item in response.get("Items", []):
            sk = str(item.get("SK") or "")
            key = sk[len(CONFIRMATION_SK_PREFIX) :].split("#", 1)[0]
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            made_on = {d for d in data.get("document_ids") or [] if isinstance(d, str)}
            if key in keys or made_on & documents:
                doomed.append({"PK": item["PK"], "SK": sk})
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    with table.batch_writer() as batch:
        for key in doomed:
            batch.delete_item(Key=key)
    return len(doomed)
