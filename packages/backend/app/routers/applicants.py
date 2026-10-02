"""Erase one applicant (DPDP right to erasure).

POST /projects/{project_id}/applicants/erase deletes every document the file
check attributes to one applicant, with exactly the cleanup of
DELETE /projects/{project_id}/documents/{document_id} (the same function runs):
the S3 objects, the LanceDB vectors, the knowledge-graph delete queue and the
DynamoDB items (document, workflow, facts).

The applicant's documents come from the file-check Lambda's read-only
applicant_documents tool, which groups documents like the verdict (PAN first,
else a compatible name), so the erase removes what the verdict shows under
that applicant, no more and no fewer. Documents that belong to no applicant in
the verdict (still being analysed, failed, without facts, spreadsheets,
unassigned) are not touched. The applicant's saved eligibility inputs (the
CIBIL page: PAN, mobile, DOB, addresses, income, loans; ELIG# items saved
under the PAN or the name) and the needs-review items a person confirmed for
them (FCCONF# items: who confirmed which checklist item) are deleted too.

The caller must repeat the applicant's name, as the verdict shows it, in
`confirm`, and list the documents it confirmed in `document_ids`: the grouping
is done again at request time, so when documents joined or left the applicant
since the check, nothing is deleted (409). The applicant's name is also
removed from the project's webhook delivery log (WHDLV# items). Not erased
here, and listed in `not_erased`: chat conversations and artifacts that
mention the applicant (the retention sweep deletes them after the retention
period) and verdicts the webhook already sent to the CRM.

One audit item with counts only (no name, PAN, document ids or caller) is
written as PROJ#{project_id} / ERASE#{timestamp}#{uuid}; DynamoDB TTL on
expires_at removes it after the retention period (default 7 days).
"""

import datetime as dt
import uuid
from typing import Annotated, Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.config import get_config
from app.ddb import get_project_item, get_table
from app.ddb.ask_usage import TTL_ATTRIBUTE
from app.ddb.file_check_confirmations import delete_applicant_confirmations
from app.ddb.webhooks import DELIVERY_SK_PREFIX
from app.file_check import (
    FileCheckNotConfiguredError,
    FileCheckServiceError,
    UnknownChecklistError,
    get_applicant_documents,
)
from app.routers.documents import delete_document
from app.routers.eligibility import erase_applicant_eligibility
from app.routers.file_check import ErrorResponse, ProjectId, UserId
from app.safe_ids import is_safe_segment

router = APIRouter(prefix="/projects/{project_id}/applicants", tags=["applicants"])

ERASE_SK_PREFIX = "ERASE#"
MAX_CONFIRMED_DOCUMENTS = 500

DocumentId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]


class EraseRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "applicant": "CKRPK7314M",
                    "confirm": "Sneha Anil Kulkarni",
                    "document_ids": ["doc_01", "doc_02", "doc_03", "doc_04", "doc_05"],
                },
            ]
        },
    )

    applicant: str = Field(
        min_length=1,
        max_length=200,
        description="The applicant as the file check shows it: PAN (preferred) or name",
    )
    confirm: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "The applicant's name exactly as the file check shows it (`applicants[].applicant`); "
            "case and spacing are ignored"
        ),
    )
    document_ids: list[DocumentId] = Field(
        min_length=1,
        max_length=MAX_CONFIRMED_DOCUMENTS,
        description=(
            "The documents the user confirmed: `applicants[].documents[].document_id` of the file check. "
            "Nothing is deleted (409) unless they are exactly the applicant's documents now."
        ),
    )


class ErasedDocument(BaseModel):
    document_id: str = Field(min_length=1)
    name: str


class EraseFailedDocument(BaseModel):
    document_id: str
    name: str
    error: str = Field(description="What failed; the message holds no document content")


class EraseResponse(BaseModel):
    applicant: str = Field(description="Name of the applicant whose documents were erased")
    documents_deleted: list[ErasedDocument] = Field(description="Documents deleted with every derived store")
    failed: list[EraseFailedDocument] = Field(
        description=(
            "Documents that could not be deleted, or were deleted but whose search-index or graph "
            "cleanup failed; the erasure of these is incomplete"
        )
    )
    delivery_log_redacted: int | None = Field(
        description=(
            "Webhook delivery log entries the applicant's name was removed from; null when that failed "
            "(the entries then expire after the retention period, see not_erased)"
        )
    )
    eligibility_inputs_deleted: int | None = Field(
        default=None,
        description=(
            "Saved eligibility inputs (CIBIL page) of the applicant deleted; null when that failed "
            "(they then expire after the retention period, see not_erased)"
        ),
    )
    not_erased: list[str] = Field(
        description="What this erase does not delete (data outside the documents); handle it separately"
    )
    erased_at: dt.datetime


class _ApplicantDocuments(BaseModel):
    """Answer of the file-check Lambda's applicant_documents tool."""

    applicant_name: str | None = None
    pan_masked: str | None = None
    documents: list[ErasedDocument] = []
    matches: int = Field(ge=0)


_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "`confirm` is not the applicant's name"},
    404: {"model": ErrorResponse, "description": "Project not found, or no such applicant in it"},
    409: {
        "model": ErrorResponse,
        "description": (
            "More than one applicant matches the name (erase by PAN), or the applicant's documents are not "
            "the ones confirmed in document_ids (run the file check again)"
        ),
    },
    502: {"model": ErrorResponse, "description": "The applicant lookup failed or answered unexpectedly"},
    503: {"model": ErrorResponse, "description": "The file-check service is not configured"},
}


def _folded(name: str) -> str:
    """Case- and spacing-insensitive form of a name (the UI collapses spaces when it shows one)."""
    return " ".join(name.split()).casefold()


def _find_applicant(project_id: str, applicant: str) -> _ApplicantDocuments:
    try:
        payload = get_applicant_documents(project_id, applicant)
    except FileCheckNotConfiguredError as e:
        raise HTTPException(status_code=503, detail="File check is not configured") from e
    except (FileCheckServiceError, UnknownChecklistError) as e:
        print(f"applicant erase: lookup failed project={project_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Applicant lookup failed: {e}") from e
    try:
        return _ApplicantDocuments.model_validate(payload)
    except ValidationError as e:
        # Field locations only: values could be document facts.
        print(f"applicant erase: unexpected lookup response at {[err['loc'] for err in e.errors()][:5]}")
        raise HTTPException(status_code=502, detail="Applicant lookup returned an unexpected response") from e


def _delete(project_id: str, document_id: str) -> str | None:
    """Delete one document with the DELETE /documents/{id} cleanup; the error, or None when complete."""
    if not is_safe_segment(document_id):
        return "invalid document id"
    try:
        result = delete_document(project_id, document_id)
    except HTTPException as e:
        return str(e.detail)
    except ClientError as e:
        return f"delete failed ({e.response.get('Error', {}).get('Code') or 'ClientError'})"
    except Exception as e:  # one document's failure must not stop the others
        return f"delete failed ({type(e).__name__})"
    incomplete = []
    if result.details.lancedb_error:
        incomplete.append("its search-index (LanceDB) entries were not deleted")
    if result.details.graph_error:
        incomplete.append("its knowledge-graph delete could not be queued")
    if incomplete:
        return "document deleted, but " + " and ".join(incomplete)
    return None


def _without_name(applicants: str, name: str) -> str | None:
    """A delivery item's comma-separated applicant names without `name`; None when none is left."""
    kept = [n.strip() for n in applicants.split(",") if n.strip() and _folded(n) != _folded(name)]
    return ", ".join(kept) or None


def _redact_delivery_log(project_id: str, name: str) -> int | None:
    """Remove the applicant's name from the project's webhook delivery log; entries changed, None on failure."""
    table = get_table()
    changed = 0
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(f"PROJ#{project_id}") & Key("SK").begins_with(DELIVERY_SK_PREFIX),
        "ProjectionExpression": "PK, SK, applicant",
    }
    try:
        while True:
            page = table.query(**kwargs)
            for item in page.get("Items", []):
                applicants = item.get("applicant")
                if not isinstance(applicants, str):
                    continue
                rest = _without_name(applicants, name)
                if rest == applicants:
                    continue
                update: dict[str, Any] = {
                    "Key": {"PK": item["PK"], "SK": item["SK"]},
                    # Never re-create an entry that TTL has just deleted.
                    "ConditionExpression": "attribute_exists(PK)",
                }
                if rest is None:
                    update["UpdateExpression"] = "REMOVE applicant"
                else:
                    update["UpdateExpression"] = "SET applicant = :applicant"
                    update["ExpressionAttributeValues"] = {":applicant": rest}
                try:
                    table.update_item(**update)
                except ClientError as e:
                    if e.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                        raise
                    continue
                changed += 1
            if not page.get("LastEvaluatedKey"):
                return changed
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    except (ClientError, BotoCoreError) as e:
        print(f"applicant erase: delivery log not redacted project={project_id} ({type(e).__name__})")
        return None


def _erase_eligibility(project_id: str, *identifiers: str) -> int | None:
    """Delete the applicant's saved eligibility inputs (by PAN or name); how many, None on failure."""
    try:
        return erase_applicant_eligibility(project_id, identifiers)
    except (ClientError, BotoCoreError) as e:
        print(f"applicant erase: eligibility inputs not deleted project={project_id} ({type(e).__name__})")
        return None


def _erase_confirmations(project_id: str, document_ids: list[str], *identifiers: str) -> int | None:
    """Delete the applicant's confirmed needs-review items (by PAN, name or document); how many, None
    on failure."""
    try:
        return delete_applicant_confirmations(project_id, applicants=identifiers, document_ids=document_ids)
    except (ClientError, BotoCoreError) as e:
        print(f"applicant erase: review confirmations not deleted project={project_id} ({type(e).__name__})")
        return None


def _not_erased(
    delivery_log_redacted: int | None, eligibility_deleted: int | None = 0, confirmations_deleted: int | None = 0
) -> list[str]:
    days = get_config().retention_days
    items = [
        "Chat conversations and artifacts that mention the applicant: delete them in the chat and artifact "
        f"lists, or the retention sweep deletes them after {days} days",
        "Verdicts and login requests the CRM webhook already delivered: erase them in the CRM",
    ]
    if delivery_log_redacted is None:
        items.append(f"The applicant's name in the webhook delivery log: it expires after {days} days")
    if eligibility_deleted is None:
        items.append(
            "The applicant's saved eligibility inputs (CIBIL page): they are deleted automatically "
            f"{days} days after they were first saved"
        )
    if confirmations_deleted is None:
        items.append(
            "Who confirmed the applicant's needs-review items in the file check: they are deleted automatically "
            f"{days} days after they were confirmed"
        )
    return items


def _write_audit(
    project_id: str,
    erased_at: dt.datetime,
    *,
    matched: int,
    deleted: int,
    failed: int,
    redacted: int,
    eligibility_deleted: int = 0,
    confirmations_deleted: int = 0,
) -> None:
    """One ERASE# item with counts only; DynamoDB TTL deletes it after the retention period."""
    item = {
        "PK": f"PROJ#{project_id}",
        "SK": f"{ERASE_SK_PREFIX}{erased_at.isoformat(timespec='microseconds')}#{uuid.uuid4().hex}",
        "documents_matched": matched,
        "documents_deleted": deleted,
        "documents_failed": failed,
        "delivery_log_redacted": redacted,
        "eligibility_inputs_deleted": eligibility_deleted,
        "review_confirmations_deleted": confirmations_deleted,
        TTL_ATTRIBUTE: int(erased_at.timestamp()) + get_config().retention_days * 86400,
    }
    try:
        get_table().put_item(Item=item)
    except (ClientError, BotoCoreError) as e:
        # The documents are already gone; the answer still reports them.
        print(f"applicant erase: audit write failed project={project_id} ({type(e).__name__})")


@router.post(
    "/erase",
    responses=_ERRORS,
    summary="Erase an applicant: delete all their documents and derived data (DPDP right to erasure)",
)
def erase_applicant(project_id: ProjectId, user_id: UserId, request: EraseRequest) -> EraseResponse:
    """Permanently delete every document the file check shows under one applicant.

    Each document gets the same cleanup as DELETE /projects/{project_id}/documents/{document_id}.
    A document that fails is reported in `failed` and the others are still deleted.
    The applicant is resolved like the file check's applicant filter (PAN first,
    else a compatible name); `confirm` must be the applicant's name as the file
    check shows it, and `document_ids` exactly the applicant's documents now
    (409 otherwise: the file changed since the check). The name is removed from
    the webhook delivery log; `not_erased` lists what this does not delete.
    """
    if not get_project_item(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    found = _find_applicant(project_id, request.applicant)
    if found.matches > 1:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{found.matches} applicants match this name: erase by PAN instead, or delete the documents one by one"
            ),
        )
    if found.matches == 0 or not found.applicant_name or not found.documents:
        raise HTTPException(status_code=404, detail="Applicant not found in this project")
    if _folded(request.confirm) != _folded(found.applicant_name):
        raise HTTPException(status_code=400, detail="confirm does not match the applicant's name")
    current = {doc.document_id for doc in found.documents}
    confirmed = set(request.document_ids)
    if current != confirmed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"The applicant's documents changed since the check ({len(current)} now, {len(confirmed)} "
                "confirmed): run the file check again and review them before erasing"
            ),
        )

    deleted: list[ErasedDocument] = []
    failed: list[EraseFailedDocument] = []
    for doc in found.documents:
        error = _delete(project_id, doc.document_id)
        if error:
            print(f"applicant erase: document not fully deleted project={project_id} document={doc.document_id}")
            failed.append(EraseFailedDocument(document_id=doc.document_id, name=doc.name, error=error))
        else:
            deleted.append(doc)

    redacted = _redact_delivery_log(project_id, found.applicant_name)
    # Saved under the identifier the page used (the PAN when known, else the name).
    eligibility_deleted = _erase_eligibility(project_id, request.applicant, found.applicant_name)
    confirmations_deleted = _erase_confirmations(project_id, sorted(current), request.applicant, found.applicant_name)
    erased_at = dt.datetime.now(dt.UTC)
    _write_audit(
        project_id,
        erased_at,
        matched=len(found.documents),
        deleted=len(deleted),
        failed=len(failed),
        redacted=redacted or 0,
        eligibility_deleted=eligibility_deleted or 0,
        confirmations_deleted=confirmations_deleted or 0,
    )
    # Counts only: never the applicant's name or PAN.
    print(
        f"applicant erase user={user_id} project={project_id} documents={len(found.documents)} "
        f"deleted={len(deleted)} failed={len(failed)} delivery_log_redacted={redacted} "
        f"eligibility_inputs_deleted={eligibility_deleted} review_confirmations_deleted={confirmations_deleted}"
    )
    return EraseResponse(
        applicant=found.applicant_name,
        documents_deleted=deleted,
        failed=failed,
        delivery_log_redacted=redacted,
        eligibility_inputs_deleted=eligibility_deleted,
        not_erased=_not_erased(redacted, eligibility_deleted, confirmations_deleted),
        erased_at=erased_at,
    )
