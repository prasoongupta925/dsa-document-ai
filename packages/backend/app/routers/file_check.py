"""Loan-file check integration API.

Lets an external system (e.g. a DSA CRM before "send loan login") get the
deterministic READY / NOT READY verdict without the chat. The verdict comes from
the file-check Lambda (the chat's run_file_check tool); this router validates
the request, checks the project exists and returns the engine's result.

POST .../file-check/ask answers one question about the file from that verdict
and the documents' facts and page text only (Amazon Nova 2 Lite), and
GET .../file-check/usage reports the Ask calls' tokens and cost (last 7 days).

POST .../file-check/confirmations records that the signed-in user checked a
needs-review checklist item (one the rules cannot verify) of one applicant;
DELETE takes it back. The engine then counts the item as met (CONFIRMED), so a
checklist with required review items can reach READY. Confirmations are kept
for the retention period (app/ddb/file_check_confirmations.py).
"""

import datetime as dt
from typing import Annotated, Any, Literal

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Body, Header, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.config import get_config
from app.ddb import get_project_item
from app.ddb.ask_usage import sum_ask_usage
from app.ddb.file_check_confirmations import delete_confirmation, put_confirmation
from app.file_check import (
    TOOL_LIST_CHECKLISTS,
    TOOL_RUN_FILE_CHECK,
    FileCheckNotConfiguredError,
    FileCheckServiceError,
    UnknownChecklistError,
    invoke_file_check_tool,
)
from app.file_check_ask import USAGE_WINDOW_DAYS, AskModelError, answer_question

router = APIRouter(prefix="/projects/{project_id}", tags=["file-check"])

Verdict = Literal["READY", "NOT READY"]

# Project ids are "proj_" + a nanoid; the pattern also keeps the value safe to log.
ProjectId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,128}$", description="Project id, e.g. proj_...")]

# Audit label of the caller. Authentication is AWS IAM (SigV4) at API Gateway;
# rejecting control characters keeps the value safe to log.
UserId = Annotated[
    str,
    Header(
        alias="x-user-id",
        pattern=r"^[^\x00-\x1f\x7f]{1,256}$",
        description=(
            "Caller id for the audit log: the web app sends the Cognito username, an "
            "integration sends its own name (e.g. `callvarta-crm`). Authentication is "
            "AWS IAM (SigV4) at API Gateway, not this header."
        ),
    ),
]


class ErrorResponse(BaseModel):
    detail: str


class UnknownChecklistDetail(BaseModel):
    message: str
    available: list[str]


class UnknownChecklistResponse(BaseModel):
    detail: UnknownChecklistDetail


_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Project not found"},
    502: {"model": ErrorResponse, "description": "The file-check service failed or answered unexpectedly"},
    503: {"model": ErrorResponse, "description": "The file-check service is not configured"},
}


# ------------------------------------------------------------------ checklists
class ChecklistRule(BaseModel):
    kind: Literal["present", "monthly", "period", "manual"] = Field(
        description=(
            "present: at least min_count documents of doc_types; monthly: one document per month for "
            "the last `months` months (by `field`); period: statements covering the last `months` "
            "months (from_field..to_field); manual: cannot be verified automatically (REVIEW)"
        )
    )
    months: int | None = None
    field: str | None = None
    from_field: str | None = None
    to_field: str | None = None
    min_count: int | None = None


class ChecklistItem(BaseModel):
    id: str
    label: str
    required: bool = Field(description="A required item that is not PRESENT makes the verdict NOT READY")
    doc_types: list[str]
    rule: ChecklistRule


class Checklist(BaseModel):
    id: str = Field(description="Pass as checklist_id to POST /projects/{project_id}/file-check")
    name: str
    product: str | None = None
    applicant_type: str | None = None
    description: str | None = None
    items: list[ChecklistItem]
    consistency_checks: list[str] = Field(description="Cross-document checks this checklist runs")
    foir: dict[str, Any] | None = Field(default=None, description="The checklist's indicative FOIR policy, if any")


class ChecklistCatalog(BaseModel):
    default_checklist: str = Field(description="Checklist applied when checklist_id is omitted")
    checklists: list[Checklist]


# ------------------------------------------------------------------ file check
class FileCheckRequest(BaseModel):
    # Stripped before the length checks: a blank applicant is rejected instead of
    # silently widening the check to every applicant (the Lambda strips too).
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {"checklist_id": "salaried_personal_loan", "applicant": "Sneha Anil Kulkarni"},
                {"applicant": "CKRPK7314M"},
                {},
            ]
        },
    )

    checklist_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_]+$",
        max_length=64,
        description="Checklist to apply (see GET /projects/{project_id}/checklists). Default: default_checklist.",
    )
    applicant: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Check only this applicant: full name (initials and a missing middle name still match) or PAN. "
            "Default: every applicant found in the project."
        ),
    )
    reference_month: str | None = Field(
        default=None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description=(
            "YYYY-MM month that 'last N months' rules end at. Default: the latest salary-slip month "
            "in the file (else the latest statement end)."
        ),
    )


class ChecklistRef(BaseModel):
    id: str
    name: str | None = None


class DocumentUsage(BaseModel):
    model_id: str | None = Field(default=None, description="Model of the facts extraction call")
    input_tokens: int
    output_tokens: int
    cost_usd: float = Field(
        description="USD cost of the call from its tokens, at the Ask feature's prices "
        "(Amazon Nova 2 Lite: $0.35 / $2.95 per million input / output tokens)"
    )


class ApplicantUsageTotal(BaseModel):
    input_tokens: int
    output_tokens: int
    cost_usd: float
    documents_with_usage: int = Field(description="Documents whose usage was recorded (sums cover only these)")
    documents_total: int = Field(description="Documents of this applicant")


class ApplicantDocument(BaseModel):
    document_id: str | None = None
    document_name: str
    doc_type: str = Field(
        description=(
            "loan_application, identity_details, salary_slip, bank_statement, form16_itr, credit_report, "
            "rent_agreement, pension_slip or other"
        )
    )
    grounded: bool | None = Field(
        default=None, description="The document had a machine text layer to verify values against"
    )
    grounding_notes: list[str] = []
    unverified_fields: list[str] = Field(default=[], description="Numeric fields not found in the document text")
    usage: DocumentUsage | None = Field(
        default=None,
        description="Tokens and cost of this document's facts extraction (one model call); "
        "null when not recorded (analysed before cost recording)",
    )


class ItemConfirmationInfo(BaseModel):
    confirmed_by: str | None = Field(default=None, description="x-user-id of the person who confirmed the item")
    confirmed_at: str | None = Field(default=None, description="ISO timestamp (UTC)")


class ChecklistItemResult(BaseModel):
    item_id: str
    item: str
    required: bool
    status: Literal["PRESENT", "MISSING", "REVIEW", "CONFIRMED"] = Field(
        description=(
            "REVIEW: a manual item a person must check; CONFIRMED: a manual item a person confirmed "
            "(POST .../file-check/confirmations), which counts as met like PRESENT"
        )
    )
    ok: bool | None = Field(description="null for REVIEW (manual) items, true for CONFIRMED ones")
    detail: str = Field(description="Human-readable finding citing document names and months")
    documents: list[str] = Field(description="Names of the documents this item used")
    required_months: list[str] | None = Field(default=None, description="monthly / period items: YYYY-MM needed")
    missing_months: list[str] | None = Field(default=None, description="monthly / period items: YYYY-MM missing")
    confirmation: ItemConfirmationInfo | None = Field(default=None, description="CONFIRMED items: who and when")
    stale_confirmation: ItemConfirmationInfo | None = Field(
        default=None,
        description="REVIEW items confirmed before a document they were confirmed on left the file: confirm again",
    )


class ConsistencyResult(BaseModel):
    check_id: str
    check: str
    status: Literal["OK", "MISMATCH", "REVIEW", "N/A", "INFO"] = Field(
        description=(
            "MISMATCH makes the verdict NOT READY; REVIEW is a finding a person must look at "
            "(listed in needs_review) that does not change the verdict"
        )
    )
    detail: str
    documents: list[str]


class BankCredit(BaseModel):
    document_name: str
    date: str | None = None
    amount: float


class IncomeSummary(BaseModel):
    declared_net: float | None = Field(default=None, description="Net monthly salary declared on the application")
    slip_net: float | None = Field(default=None, description="Median net salary on the salary slips")
    slip_gross: float | None = Field(default=None, description="Median gross salary on the salary slips")
    bank_salary_credit: float | None = Field(default=None, description="Median salary credit in the bank statement")
    form16_gross: float | None = None
    slip_gross_x12: float | None = None
    bank_credits: list[BankCredit] = []


class ConfirmedItem(BaseModel):
    item_id: str
    item: str
    confirmed_by: str | None = None
    confirmed_at: str | None = None


class ApplicantResult(BaseModel):
    applicant: str
    pan: str | None = None
    verdict: Verdict
    reference_month: str | None = Field(default=None, description="YYYY-MM the monthly rules ended at")
    reference_month_label: str | None = None
    documents: list[ApplicantDocument]
    usage_total: ApplicantUsageTotal | None = Field(
        default=None, description="Sum of the documents' usage (null from file-check engines without usage)"
    )
    checklist: list[ChecklistItemResult]
    consistency: list[ConsistencyResult]
    income: IncomeSummary
    reasons: list[str] = Field(
        description="Every reason for NOT READY, in order (MISSING / MISMATCH / REVIEW / PENDING)"
    )
    missing_items: list[str] = Field(description="What to collect from the applicant, with exact months")
    mismatches: list[str]
    needs_review: list[str] = Field(
        default=[], description="Consistency findings with status REVIEW; reported, the verdict is unchanged"
    )
    manual_review: list[str] = Field(description="Items a person must verify (not decided by the rules)")
    confirmed_items: list[ConfirmedItem] = Field(
        default=[], description="Manual items a person confirmed; they count as met"
    )
    obligations: dict[str, Any] | None = Field(
        default=None, description="Existing obligations from the bank statement and the application, if computed"
    )
    foir: dict[str, Any] | None = Field(
        default=None, description="Indicative FOIR, if the checklist enables it; the lender's policy decides"
    )


class PendingDocument(BaseModel):
    document_id: str | None = None
    document_name: str | None = None
    status: str | None = None


class FailedDocument(BaseModel):
    document_id: str | None = None
    document_name: str | None = None


class NoFactsDocument(BaseModel):
    document_id: str | None = None
    document_name: str | None = None
    reason: str


class UnsupportedDocument(BaseModel):
    document_id: str | None = None
    document_name: str | None = None
    file_type: str | None = None
    reason: str


class UnassignedDocument(BaseModel):
    document_id: str | None = None
    document_name: str | None = None
    doc_type: str


class RecordingDocument(BaseModel):
    document_id: str | None = None
    document_name: str | None = None
    file_type: str | None = None


class FileCheckResponse(BaseModel):
    project_id: str | None = None
    engine_version: str
    as_of: dt.date
    checklist: ChecklistRef
    overall_verdict: Verdict = Field(
        description="READY only when every applicant is READY and no document is still being analysed"
    )
    summary: str
    applicants: list[ApplicantResult]
    pending_documents: list[PendingDocument] = Field(description="Still being analysed: every applicant is NOT READY")
    failed_documents: list[FailedDocument]
    no_facts_documents: list[NoFactsDocument]
    unsupported_documents: list[UnsupportedDocument]
    unassigned_documents: list[UnassignedDocument]
    recording_documents: list[RecordingDocument] = Field(
        default=[],
        description="Audio / video documents: call recordings (review them with Call QA), never part of a loan file",
    )


# ------------------------------------------------------------------ confirmations
# Item ids of the bundled checklists look like vlp_pl_02 or x05.
ItemId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
ConfirmedDocumentId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
# The applicant as the verdict shows it; rejecting control characters keeps it out of trouble.
ApplicantRef = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[^\x00-\x1f\x7f]{1,200}$")]


class ConfirmItemRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "applicant": "BQXPD4821K",
                    "item_id": "vlp_pl_02",
                    "checklist_id": "vlp_pl_sal",
                    "document_ids": ["doc_01", "doc_02", "doc_03"],
                }
            ]
        },
    )

    applicant: ApplicantRef = Field(
        description="The applicant as the verdict shows it: its `pan` when it has one, else its name"
    )
    item_id: ItemId = Field(description="`checklist[].item_id` of a REVIEW item of that applicant")
    checklist_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_]+$",
        max_length=64,
        description="Checklist the item was confirmed under (kept for the record; item ids carry across checklists)",
    )
    document_ids: list[ConfirmedDocumentId] = Field(
        min_length=1,
        max_length=500,
        description=(
            "The applicant's documents in the verdict (`documents[].document_id`): the confirmation holds "
            "while all of them are still in the applicant's file"
        ),
    )


class UndoConfirmationRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"applicant": "BQXPD4821K", "applicant_name": "Rahul Vijay Deshmukh", "item_id": "vlp_pl_02"},
                {"applicant": "Rahul Vijay Deshmukh", "item_id": "x02"},
            ]
        },
    )

    applicant: ApplicantRef = Field(description="The applicant as the verdict shows it: its `pan`, else its name")
    applicant_name: ApplicantRef | None = Field(
        default=None,
        description=(
            "The applicant's name too, when `applicant` is the PAN: a confirmation saved under the name "
            "(before a PAN was read) still applies, so it is removed as well"
        ),
    )
    item_id: ItemId


class ItemConfirmation(BaseModel):
    item_id: str
    checklist_id: str | None = None
    confirmed_by: str = Field(description="x-user-id of the caller")
    confirmed_at: str = Field(description="ISO timestamp (UTC)")
    expires_at: dt.datetime = Field(description="When the confirmation is deleted (retention period)")


class UndoConfirmationResponse(BaseModel):
    item_id: str
    deleted: bool = Field(description="false when there was no such confirmation (already undone or expired)")


# ------------------------------------------------------------------ ask
class AskHistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class AskRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {"question": "Summarise the monthly obligations", "applicant": "Rahul Vijay Deshmukh"},
                {
                    "question": "And the FOIR?",
                    "applicant": "BQXPD4821K",
                    "history": [
                        {"role": "user", "content": "Summarise the monthly obligations"},
                        {"role": "assistant", "content": "Car loan EMI Rs 8,200 on the 5th ..."},
                    ],
                },
            ]
        },
    )

    question: str = Field(min_length=1, max_length=1000, description="The question about this loan file")
    checklist_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_]+$",
        max_length=64,
        description="Checklist of the verdict the answer uses. Default: default_checklist.",
    )
    applicant: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Answer about this applicant only (name or PAN). Default: every applicant in the project.",
    )
    history: list[AskHistoryMessage] = Field(
        default=[],
        max_length=6,
        description="Earlier turns of this conversation, oldest first (at most 6)",
    )


class AskPricing(BaseModel):
    input_per_million_usd: float
    output_per_million_usd: float
    region: str


class AskGroundedOn(BaseModel):
    applicants: list[str] = Field(description="Applicants whose verdict the answer was given")
    documents: list[str] = Field(description="Documents whose verdict, facts and page text the answer was given")


class AskResponse(BaseModel):
    answer: str = Field(description='Answer from the file only; "not in the file" when it is absent')
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float = Field(description="Cost of this call from its token usage and `pricing`")
    pricing: AskPricing
    grounded_on: AskGroundedOn


class AskUsageResponse(BaseModel):
    window_days: int
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


# ------------------------------------------------------------------ helpers
def _require_project(project_id: str) -> None:
    if not get_project_item(project_id):
        raise HTTPException(status_code=404, detail="Project not found")


def _invoke[M: BaseModel](tool: str, arguments: dict[str, Any], model: type[M]) -> M:
    try:
        payload = invoke_file_check_tool(tool, arguments)
    except FileCheckNotConfiguredError as e:
        raise HTTPException(status_code=503, detail="File check is not configured") from e
    except UnknownChecklistError as e:
        raise HTTPException(status_code=400, detail={"message": str(e), "available": e.available}) from e
    except FileCheckServiceError as e:
        print(f"file-check {tool} failed: {e}")
        raise HTTPException(status_code=502, detail=f"File check failed: {e}") from e
    try:
        return model.model_validate(payload)
    except ValidationError as e:
        # Log field locations only: values could be document facts.
        print(f"file-check {tool} unexpected response at {[err['loc'] for err in e.errors()][:5]}")
        raise HTTPException(status_code=502, detail="File check returned an unexpected response") from e


# ------------------------------------------------------------------ routes
@router.get(
    "/checklists",
    response_model_exclude_none=True,
    responses=_ERRORS,
    summary="List the checklists the file check can apply",
)
def list_checklists(project_id: ProjectId, user_id: UserId) -> ChecklistCatalog:
    """Checklists (loan product x applicant type) with their items and rules."""
    _require_project(project_id)
    catalog = _invoke(TOOL_LIST_CHECKLISTS, {}, ChecklistCatalog)
    print(f"file-check checklists user={user_id} project={project_id} count={len(catalog.checklists)}")
    return catalog


@router.post(
    "/file-check",
    responses={400: {"model": UnknownChecklistResponse, "description": "Unknown checklist_id"}, **_ERRORS},
    summary="Run the deterministic loan-file check",
)
def run_file_check(
    project_id: ProjectId,
    user_id: UserId,
    request: Annotated[FileCheckRequest | None, Body()] = None,
) -> FileCheckResponse:
    """READY / NOT READY for each applicant in the project, decided by rules (no LLM).

    Findings cite document names and exact months: missing documents
    (`missing_items`, `checklist[].missing_months`), cross-document mismatches
    (PAN, name, employer, declared vs slip vs bank salary) and items that need a
    manual review. Only documents whose analysis has finished are checked; any
    document still being analysed keeps every applicant NOT READY.
    """
    _require_project(project_id)
    body = request or FileCheckRequest()
    arguments: dict[str, Any] = {"project_id": project_id, **body.model_dump(exclude_none=True)}
    result = _invoke(TOOL_RUN_FILE_CHECK, arguments, FileCheckResponse)
    print(
        f"file-check user={user_id} project={project_id} checklist={result.checklist.id} "
        f"applicants={len(result.applicants)} verdict={result.overall_verdict}"
    )
    return result


@router.post(
    "/file-check/ask",
    responses={400: {"model": UnknownChecklistResponse, "description": "Unknown checklist_id"}, **_ERRORS},
    summary="Ask a question about the loan file (answered from the file only)",
)
def ask_file_check(project_id: ProjectId, user_id: UserId, request: AskRequest) -> AskResponse:
    """Answer one question from the file-check verdict and the documents' facts and page text.

    The model (Amazon Nova 2 Lite, temperature 0) gets only that material and
    is told to say "not in the file" instead of using outside knowledge or
    guessing. The verdict itself is decided by the rules, never by the model.
    Each call's tokens and cost are recorded for GET .../file-check/usage.
    """
    _require_project(project_id)
    arguments: dict[str, Any] = {
        "project_id": project_id,
        **request.model_dump(include={"checklist_id", "applicant"}, exclude_none=True),
    }
    verdict = _invoke(TOOL_RUN_FILE_CHECK, arguments, FileCheckResponse)
    try:
        result = answer_question(
            project_id,
            verdict.model_dump(mode="json", exclude_none=True),
            request.question,
            [turn.model_dump() for turn in request.history],
        )
    except AskModelError as e:
        print(f"file-check ask failed user={user_id} project={project_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Answer failed: {e}") from e
    stats = result.pop("context", {})
    # Counts only: the question, answer and file content are never logged.
    print(
        f"file-check ask user={user_id} project={project_id} checklist={verdict.checklist.id} "
        f"applicants={len(result['grounded_on']['applicants'])} in={result['input_tokens']} "
        f"out={result['output_tokens']} cost_usd={result['cost_usd']} truncated={stats.get('truncated')}"
    )
    return AskResponse.model_validate(result)


@router.get(
    "/file-check/usage",
    responses={404: _ERRORS[404]},
    summary="Tokens and cost of this project's Ask calls in the last 7 days",
)
def file_check_usage(project_id: ProjectId, user_id: UserId) -> AskUsageResponse:
    """Sums the usage ledger: one item per Ask call, deleted by DynamoDB TTL after the retention period."""
    _require_project(project_id)
    usage = sum_ask_usage(project_id, window_days=USAGE_WINDOW_DAYS)
    print(f"file-check usage user={user_id} project={project_id} calls={usage['calls']}")
    return AskUsageResponse.model_validate(usage)


_STORAGE_ERRORS: dict[int | str, dict[str, Any]] = {
    404: _ERRORS[404],
    502: {"model": ErrorResponse, "description": "The confirmation could not be stored"},
}


@router.post(
    "/file-check/confirmations",
    responses=_STORAGE_ERRORS,
    summary="Confirm a needs-review checklist item of one applicant",
)
def confirm_item(project_id: ProjectId, user_id: UserId, request: ConfirmItemRequest) -> ItemConfirmation:
    """Record that the caller checked a REVIEW (manual) checklist item, such as an address proof.

    The next file check shows the item as CONFIRMED, with who and when, and
    counts it as met; it does so while every document in `document_ids` is
    still in the applicant's file, and for the retention period. Confirming
    again replaces the earlier confirmation. Only manual items can be
    confirmed: a confirmation never makes a missing document present.
    """
    _require_project(project_id)
    config = get_config()
    try:
        data = put_confirmation(
            project_id,
            applicant=request.applicant,
            item_id=request.item_id,
            checklist_id=request.checklist_id,
            document_ids=request.document_ids,
            confirmed_by=user_id,
            retention_days=config.retention_days,
        )
    except (BotoCoreError, ClientError) as e:
        print(f"file-check confirm failed user={user_id} project={project_id} ({type(e).__name__})")
        raise HTTPException(status_code=502, detail="The confirmation could not be stored") from e
    # The item id only: the applicant is personal data.
    print(f"file-check confirm user={user_id} project={project_id} item={request.item_id}")
    return ItemConfirmation(
        item_id=data["item_id"],
        checklist_id=data["checklist_id"],
        confirmed_by=data["confirmed_by"],
        confirmed_at=data["confirmed_at"],
        expires_at=dt.datetime.fromtimestamp(data["expires_at"], dt.UTC),
    )


@router.delete(
    "/file-check/confirmations",
    responses=_STORAGE_ERRORS,
    summary="Undo the confirmation of a needs-review checklist item",
)
def undo_confirmation(
    project_id: ProjectId, user_id: UserId, request: Annotated[UndoConfirmationRequest, Body()]
) -> UndoConfirmationResponse:
    """The item is REVIEW again in the next file check. The applicant travels in the body, not the URL."""
    _require_project(project_id)
    applicants = [request.applicant, *([request.applicant_name] if request.applicant_name else [])]
    try:
        deleted = delete_confirmation(project_id, applicants=applicants, item_id=request.item_id)
    except (BotoCoreError, ClientError) as e:
        print(f"file-check undo confirm failed user={user_id} project={project_id} ({type(e).__name__})")
        raise HTTPException(status_code=502, detail="The confirmation could not be removed") from e
    print(f"file-check undo confirm user={user_id} project={project_id} item={request.item_id} deleted={deleted}")
    return UndoConfirmationResponse(item_id=request.item_id, deleted=deleted)
