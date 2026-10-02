"""CRM webhook of a project: push the loan-file verdict to CallVarta CRM (or any CRM).

GET  /projects/{project_id}/integrations/webhook          settings and the last 20 deliveries
PUT  /projects/{project_id}/integrations/webhook          {url, enabled}
POST /projects/{project_id}/integrations/webhook/secret   new signing secret, returned once
POST /projects/{project_id}/integrations/webhook/test     send a signed test event now

While enabled, each document whose analysis completes triggers one delivery
(event file_check.completed): the workflow finalizer queues the webhook Lambda
(packages/infra/src/functions/webhook), which runs the deterministic file check
and POSTs the verdict of the applicant(s) that document belongs to, signed
with the project's secret (X-DocAI-Signature, see app/webhook_security.py).
The test event needs a URL and a secret but not `enabled`; it carries no
applicant data (results is empty), and one per project every 10 s is allowed
(429 otherwise).

Settings live in the project META item (webhook_url, webhook_enabled and
webhook_secret_enc: the secret encrypted with the webhook KMS key, which only
the delivery Lambda can decrypt); delivery log items expire after the retention
period (DynamoDB TTL). The secret is only ever returned by POST .../secret.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Path, Response
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_config
from app.ddb.webhooks import (
    WebhookSettings,
    get_webhook_settings,
    list_webhook_deliveries,
    put_webhook_secret,
    put_webhook_settings,
)
from app.webhook_delivery import (
    WebhookNotConfiguredError,
    WebhookRateLimitedError,
    WebhookServiceError,
    WebhookSkippedError,
    send_test_event,
)
from app.webhook_secret import SecretEncryptionError, SecretKeyNotConfiguredError, encrypt_secret
from app.webhook_security import MAX_URL_LENGTH, WebhookUrlError, generate_secret, validate_webhook_url

router = APIRouter(prefix="/projects/{project_id}/integrations", tags=["integrations"])

DELIVERIES_SHOWN = 20

# Project ids are "proj_" + a nanoid; the pattern also keeps the value safe to log.
ProjectId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,128}$", description="Project id, e.g. proj_...")]

# Audit label of the caller (the web app sends the Cognito username).
# Authentication is AWS IAM (SigV4) at API Gateway; rejecting control
# characters keeps the value safe to log.
UserId = Annotated[
    str,
    Header(
        alias="x-user-id",
        pattern=r"^[^\x00-\x1f\x7f]{1,256}$",
        description="Caller id for the audit log. Authentication is AWS IAM (SigV4) at API Gateway, not this header.",
    ),
]

DeliveryStatus = Literal["delivered", "failed"]


class ErrorResponse(BaseModel):
    detail: str


class WebhookDelivery(BaseModel):
    delivery_id: str
    at: str = Field(description="ISO 8601 UTC time the delivery was made")
    event: str = Field(description="file_check.completed, file_login.requested or test")
    applicant: str | None = Field(
        default=None, description="Applicant(s) reported, comma-separated; null for test and project-level deliveries"
    )
    status: DeliveryStatus
    http_status: int | None = Field(
        default=None, description="HTTP status of the last attempt; null when it got no answer (or was blocked)"
    )
    error: str | None = Field(default=None, description="Why the delivery failed (never the payload or the secret)")


class WebhookSettingsResponse(BaseModel):
    url: str | None
    enabled: bool
    secret_set: bool = Field(description="A signing secret exists; the secret itself is only shown when generated")
    deliveries: list[WebhookDelivery] = Field(description="Last 20 deliveries of the retention period, newest first")


class WebhookUpdateRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"url": "https://crm.example.com/hooks/idp", "enabled": True}]},
    )

    url: str | None = Field(
        description=(
            f"https URL of the CRM endpoint, at most {MAX_URL_LENGTH} characters, port 443 or 8443, no "
            "credentials, public host only. null (or blank) removes it."
        ),
    )
    enabled: bool = Field(description="Deliver after every analysed document; needs a URL and a secret")


class WebhookSecretResponse(BaseModel):
    secret: str = Field(description="HMAC-SHA256 key for X-DocAI-Signature; store it now, it is not shown again")


class WebhookTestResult(BaseModel):
    delivery_id: str
    status: DeliveryStatus
    http_status: int | None = None
    error: str | None = None


_NOT_FOUND = {404: {"model": ErrorResponse, "description": "Project not found"}}


# ------------------------------------------------------------------ helpers
def _require_settings(project_id: str) -> WebhookSettings:
    settings = get_webhook_settings(project_id)
    if settings is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return settings


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _delivery(item: dict[str, Any]) -> WebhookDelivery:
    return WebhookDelivery(
        delivery_id=str(item.get("delivery_id") or str(item.get("SK", "")).rpartition("#")[2]),
        at=str(item.get("at") or ""),
        event=str(item.get("event") or ""),
        applicant=_as_str(item.get("applicant")),
        status="delivered" if item.get("status") == "delivered" else "failed",
        http_status=_as_int(item.get("http_status")),
        error=_as_str(item.get("error")),
    )


def _settings_response(project_id: str, settings: WebhookSettings) -> WebhookSettingsResponse:
    items = list_webhook_deliveries(project_id, window_days=get_config().retention_days, limit=DELIVERIES_SHOWN)
    return WebhookSettingsResponse(
        url=settings.url,
        enabled=settings.enabled,
        secret_set=settings.secret_set,
        deliveries=[_delivery(item) for item in items],
    )


def _host(url: str | None) -> str:
    """Host only, for the audit log: the path or query may hold a CRM token."""
    if not url:
        return "-"
    try:
        return validate_webhook_url(url).host
    except WebhookUrlError:
        return "?"


# ------------------------------------------------------------------ routes
@router.get("/webhook", responses=_NOT_FOUND, summary="Webhook settings and recent deliveries")
def get_webhook(project_id: ProjectId, user_id: UserId) -> WebhookSettingsResponse:
    settings = _require_settings(project_id)
    return _settings_response(project_id, settings)


@router.put(
    "/webhook",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid URL, or enabled without URL or secret"},
        **_NOT_FOUND,
        409: {"model": ErrorResponse, "description": "The settings changed during the request"},
    },
    summary="Set the webhook URL and turn deliveries on or off",
)
def put_webhook(project_id: ProjectId, user_id: UserId, request: WebhookUpdateRequest) -> WebhookSettingsResponse:
    """https only on port 443 or 8443, at most 2048 characters, no credentials, and the host must be public:
    IP literals in private, loopback, link-local (169.254.169.254) or other reserved ranges and internal names
    are refused. `enabled` needs a URL and a secret (POST .../webhook/secret)."""
    settings = _require_settings(project_id)
    url = (request.url or "").strip() or None
    if url is not None:
        try:
            validate_webhook_url(url)
        except WebhookUrlError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    if request.enabled and url is None:
        raise HTTPException(status_code=400, detail="Set a webhook URL before enabling the webhook")
    if request.enabled and not settings.secret_set:
        raise HTTPException(status_code=400, detail="Generate a signing secret before enabling the webhook")
    if not put_webhook_settings(project_id, url=url, enabled=request.enabled):
        # Changed since the read above: 404 when the project was deleted meanwhile.
        _require_settings(project_id)
        raise HTTPException(status_code=409, detail="Webhook settings changed meanwhile; reload and try again")
    print(f"webhook settings user={user_id} project={project_id} enabled={request.enabled} host={_host(url)}")
    return _settings_response(
        project_id, WebhookSettings(url=url, enabled=request.enabled, secret_set=settings.secret_set)
    )


@router.post(
    "/webhook/secret",
    responses={
        **_NOT_FOUND,
        502: {"model": ErrorResponse, "description": "The secret could not be encrypted (KMS)"},
        503: {"model": ErrorResponse, "description": "The webhook secret key is not configured"},
    },
    summary="Generate a new signing secret (returned once)",
)
def create_webhook_secret(project_id: ProjectId, user_id: UserId, response: Response) -> WebhookSecretResponse:
    """32 random bytes, URL-safe base64. Replaces the previous secret at once: deliveries from now on are
    signed with the new one. Only this response contains it; it is stored encrypted with the webhook KMS
    key (only the delivery Lambda can decrypt it), and GET .../webhook reports `secret_set`."""
    _require_settings(project_id)
    secret = generate_secret()
    try:
        secret_enc = encrypt_secret(project_id, secret)
    except SecretKeyNotConfiguredError as e:
        raise HTTPException(status_code=503, detail="Webhook secret encryption is not configured") from e
    except SecretEncryptionError as e:
        print(f"webhook secret not stored user={user_id} project={project_id}: {e}")
        raise HTTPException(status_code=502, detail=f"The signing secret could not be encrypted: {e}") from e
    if not put_webhook_secret(project_id, secret_enc):
        raise HTTPException(status_code=404, detail="Project not found")
    response.headers["Cache-Control"] = "no-store"
    print(f"webhook secret rotated user={user_id} project={project_id}")
    return WebhookSecretResponse(secret=secret)


@router.post(
    "/webhook/test",
    responses={
        400: {"model": ErrorResponse, "description": "No webhook URL or no signing secret"},
        **_NOT_FOUND,
        409: {"model": ErrorResponse, "description": "The delivery Lambda sent nothing (it says why)"},
        429: {"model": ErrorResponse, "description": "One test per project every 10 s (see Retry-After)"},
        502: {"model": ErrorResponse, "description": "The delivery Lambda failed or answered unexpectedly"},
        503: {"model": ErrorResponse, "description": "Webhook delivery is not configured"},
    },
    summary="Send a signed test event now",
)
def send_webhook_test(project_id: ProjectId, user_id: UserId) -> WebhookTestResult:
    """Signed like a real delivery, with event `test`, document_id null and no results. Works while the
    webhook is disabled. A CRM that is down (5xx, timeout) makes this a `failed` result, not an error.
    One test per project every 10 s, and only a few at a time: 429 with Retry-After otherwise."""
    settings = _require_settings(project_id)
    if settings.url is None or not settings.secret_set:
        raise HTTPException(status_code=400, detail="Set a webhook URL and generate a signing secret first")
    try:
        result = send_test_event(project_id)
    except WebhookNotConfiguredError as e:
        raise HTTPException(status_code=503, detail="Webhook delivery is not configured") from e
    except WebhookRateLimitedError as e:
        raise HTTPException(status_code=429, detail=str(e), headers={"Retry-After": str(e.retry_after)}) from e
    except WebhookSkippedError as e:
        raise HTTPException(status_code=409, detail=f"Test event not sent: {e}") from e
    except WebhookServiceError as e:
        print(f"webhook test failed user={user_id} project={project_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Webhook delivery failed: {e}") from e
    outcome = WebhookTestResult(
        delivery_id=str(result["delivery_id"]),
        status=result["status"],
        http_status=_as_int(result.get("http_status")),
        error=_as_str(result.get("error")),
    )
    print(
        f"webhook test user={user_id} project={project_id} delivery={outcome.delivery_id} "
        f"status={outcome.status} http_status={outcome.http_status}"
    )
    return outcome
