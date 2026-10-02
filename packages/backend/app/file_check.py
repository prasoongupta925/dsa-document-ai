"""Client for the deterministic file-check Lambda (idp-v2-file-check-mcp).

The Lambda is the AgentCore Gateway target behind the chat's run_file_check and
list_checklists tools. The backend invokes it the way the Gateway does: the tool
arguments are the event and the tool name travels in ClientContext.custom. The
chat and the integration API therefore get their verdict from the same engine;
the rules are never duplicated here. The erase-applicant API resolves an
applicant's documents with the Lambda's read-only applicant_documents tool (not
offered to the chat), so it uses the verdict's own grouping.
"""

import base64
import json
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.config import get_config

# gatewayTargetName of the file-check target in packages/infra/src/stacks/mcp-stack.ts.
GATEWAY_TARGET_NAME = "filecheck"
TOOL_RUN_FILE_CHECK = "run_file_check"
TOOL_LIST_CHECKLISTS = "list_checklists"
# Backend-only: not in the Gateway tool schema (packages/lambda/file-check-mcp/schema.json).
TOOL_APPLICANT_DOCUMENTS = "applicant_documents"

_lambda_client = None


class FileCheckNotConfiguredError(Exception):
    """FILE_CHECK_FUNCTION_NAME is not set."""


class FileCheckServiceError(Exception):
    """The Lambda could not be invoked, failed, or answered with an error."""


class UnknownChecklistError(Exception):
    """The Lambda rejected checklist_id; `available` lists the valid ids."""

    def __init__(self, message: str, available: list[str]):
        super().__init__(message)
        self.available = available


def get_file_check_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        config = get_config()
        # The function times out after 30 s; one retry covers throttling.
        _lambda_client = boto3.client(
            "lambda",
            region_name=config.aws_region,
            config=BotoConfig(
                connect_timeout=5,
                read_timeout=40,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
    return _lambda_client


def _client_context(tool: str) -> str:
    """Base64 ClientContext carrying the Gateway-style tool name."""
    context = {"custom": {"bedrockAgentCoreToolName": f"{GATEWAY_TARGET_NAME}___{tool}"}}
    return base64.b64encode(json.dumps(context).encode("utf-8")).decode("ascii")


def invoke_file_check_tool(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke one tool synchronously and return its JSON object.

    Raises FileCheckNotConfiguredError, UnknownChecklistError (the Lambda said
    checklist_id is unknown) or FileCheckServiceError (anything else).
    """
    config = get_config()
    if not config.file_check_function_name:
        raise FileCheckNotConfiguredError("file-check function is not configured")

    client = get_file_check_lambda_client()
    try:
        resp = client.invoke(
            FunctionName=config.file_check_function_name,
            InvocationType="RequestResponse",
            ClientContext=_client_context(tool),
            Payload=json.dumps(arguments).encode("utf-8"),
        )
        raw = resp["Payload"].read()
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code") or "ClientError"
        raise FileCheckServiceError(f"invoke failed ({code})") from e
    except BotoCoreError as e:
        raise FileCheckServiceError(f"invoke failed ({type(e).__name__})") from e

    if resp.get("FunctionError"):
        raise FileCheckServiceError(f"function error ({resp['FunctionError']})")
    try:
        payload = json.loads(raw)
    except ValueError as e:
        raise FileCheckServiceError("non-JSON response") from e
    if not isinstance(payload, dict):
        raise FileCheckServiceError("non-object response")

    error = payload.get("error")
    if error:
        # The handler answers errors as {"error": ...} and never raises; an
        # unknown checklist_id also carries the valid ids. Messages hold no
        # document facts (index.py logs and returns the exception type only).
        if isinstance(payload.get("available"), list):
            raise UnknownChecklistError(str(error), [str(a) for a in payload["available"]])
        raise FileCheckServiceError(str(error))
    return payload


def get_applicant_documents(project_id: str, applicant: str) -> dict[str, Any]:
    """One applicant's documents, grouped exactly as the verdict groups them (read-only).

    Returns {project_id, applicant_name, pan_masked, documents: [{document_id,
    name}], matches}; `matches` counts the applicants that matched (PAN first,
    else a compatible name) and only a single match lists documents. Raises
    like invoke_file_check_tool.
    """
    return invoke_file_check_tool(TOOL_APPLICANT_DOCUMENTS, {"project_id": project_id, "applicant": applicant})
