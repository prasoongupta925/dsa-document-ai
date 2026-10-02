# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Client for the DSA Document AI (IDP) backend API in ap-south-1.

The API Gateway uses AWS_IAM auth: every request is SigV4-signed for
``execute-api`` with the server role's credentials (boto3 default chain), and
carries ``x-user-id`` (the audit label: the signed-in user's Cognito username
for browser calls, ``voice-bot-phone`` etc. otherwise). The calls are the ones
the web app and handoff/seed-demo.py make.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from loguru import logger


class IdpError(Exception):
    """An IDP API call failed. ``status`` is the HTTP status (0 for a network error)."""

    def __init__(self, status: int, path: str, detail: str = ""):
        super().__init__(f"IDP {path} -> {status}")
        self.status = status
        self.path = path
        self.detail = detail


class IdpNotConfigured(IdpError):
    def __init__(self):
        super().__init__(503, "-", "IDP_API_URL is not set")


@dataclass
class UploadTicket:
    document_id: str
    upload_url: str


def _safe_detail(resp: httpx.Response) -> str:
    """The FastAPI 'detail' string when it is short and plain (never echo bodies into logs)."""
    try:
        detail = resp.json().get("detail")
    except Exception:  # noqa: BLE001
        return ""
    return detail[:200] if isinstance(detail, str) else ""


class IdpClient:
    def __init__(
        self,
        base_url: str,
        region: str = "ap-south-1",
        caller_id: str = "voice-bot",
        timeout_s: float = 20.0,
        session: boto3.Session | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base = base_url.rstrip("/")
        self._region = region
        self._caller = caller_id
        self._timeout = timeout_s
        self._session = session or boto3.Session(region_name=region)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._base)

    async def _http(self) -> httpx.AsyncClient:
        async with self._lock:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=self._timeout, transport=self._transport)
            return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _signed_headers(self, method: str, url: str, body: bytes | None, user: str) -> dict[str, str]:
        creds = self._session.get_credentials()
        if creds is None:
            raise IdpError(0, "-", "no AWS credentials")
        headers = {"x-user-id": user}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = AWSRequest(method=method, url=url, data=body, headers=headers)
        SigV4Auth(creds.get_frozen_credentials(), "execute-api", self._region).add_auth(req)
        return dict(req.headers)

    async def request(self, method: str, path: str, body: dict | None = None, user: str | None = None) -> Any:
        if not self._base:
            raise IdpNotConfigured()
        url = f"{self._base}/{path.lstrip('/')}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        user = (user or self._caller)[:256]
        client = await self._http()
        attempts = 2
        for attempt in range(1, attempts + 1):
            # Signed per attempt: the signature carries a timestamp.
            headers = self._signed_headers(method, url, data, user)
            try:
                resp = await client.request(method, url, content=data, headers=headers)
            except httpx.HTTPError as e:
                if attempt < attempts:
                    await asyncio.sleep(0.3)
                    continue
                logger.bind(event="idp_error", path=_path_label(path), status=0).warning(f"IDP network error {type(e).__name__}")
                raise IdpError(0, path, type(e).__name__) from None
            if resp.status_code in (429, 502, 503, 504) and attempt < attempts:
                await asyncio.sleep(0.5)
                continue
            if resp.status_code >= 400:
                detail = _safe_detail(resp)
                logger.bind(event="idp_error", path=_path_label(path), status=resp.status_code).warning("IDP call failed")
                raise IdpError(resp.status_code, path, detail)
            logger.bind(event="idp_call", path=_path_label(path), status=resp.status_code).debug("IDP call ok")
            return resp.json() if resp.content else {}
        raise IdpError(0, path)  # pragma: no cover - loop always returns or raises

    # ------------------------------------------------------------- endpoints
    async def list_projects(self, user: str | None = None) -> list[dict]:
        data = await self.request("GET", "projects", user=user)
        if isinstance(data, dict):
            data = data.get("projects") or data.get("items") or []
        return [p for p in data if isinstance(p, dict)]

    async def file_check(self, project_id: str, applicant: str | None = None, user: str | None = None) -> dict:
        body = {"applicant": applicant} if applicant else {}
        return await self.request("POST", f"projects/{quote(project_id, safe='')}/file-check", body, user=user)

    async def eligibility(self, project_id: str, applicant: str, user: str | None = None) -> dict:
        return await self.request(
            "POST", f"projects/{quote(project_id, safe='')}/eligibility/calculate", {"applicant": applicant}, user=user
        )

    async def eligibility_inputs(self, project_id: str, applicant: str, user: str | None = None) -> dict:
        """Saved CIBIL-page inputs of an applicant (else a draft pre-filled from the documents)."""
        return await self.request(
            "GET",
            f"projects/{quote(project_id, safe='')}/eligibility/inputs?applicant={quote(applicant, safe='')}",
            user=user,
        )

    async def create_document(
        self,
        project_id: str,
        file_name: str,
        content_type: str,
        file_size: int,
        use_transcribe: bool = False,
        transcribe_options: dict | None = None,
        user: str | None = None,
    ) -> UploadTicket:
        body: dict[str, Any] = {
            "file_name": file_name,
            "content_type": content_type,
            "file_size": file_size,
            "use_transcribe": use_transcribe,
        }
        if transcribe_options:
            body["transcribe_options"] = transcribe_options
        data = await self.request("POST", f"projects/{quote(project_id, safe='')}/documents", body, user=user)
        return UploadTicket(document_id=data["document_id"], upload_url=data["upload_url"])

    async def put_file(self, upload_url: str, path: str, content_type: str) -> None:
        """PUT the file to the presigned S3 URL (content type and length are signed headers)."""
        size = os.path.getsize(path)
        client = await self._http()

        def _read() -> bytes:
            with open(path, "rb") as f:
                return f.read()

        content = await asyncio.to_thread(_read)
        try:
            resp = await client.put(
                upload_url,
                content=content,
                headers={"Content-Type": content_type, "Content-Length": str(size)},
                timeout=max(self._timeout, 120.0),
            )
        except httpx.HTTPError as e:  # status 0: the caller may retry
            logger.bind(event="idp_error", path="s3-put", status=0).warning(f"recording PUT network error {type(e).__name__}")
            raise IdpError(0, "s3-put", type(e).__name__) from None
        if resp.status_code not in (200, 204):
            logger.bind(event="idp_error", path="s3-put", status=resp.status_code).warning("recording PUT failed")
            raise IdpError(resp.status_code, "s3-put")

    async def mark_uploaded(self, project_id: str, document_id: str, user: str | None = None) -> dict:
        return await self.request(
            "PUT",
            f"projects/{quote(project_id, safe='')}/documents/{quote(document_id, safe='')}/status",
            {"status": "uploaded"},
            user=user,
        )


def _path_label(path: str) -> str:
    """'projects/proj_x/file-check' -> 'projects/{id}/file-check' (ids are fine in logs, but keep cardinality low)."""
    parts = path.split("?", 1)[0].strip("/").split("/")
    return "/".join("{id}" if i and parts[i - 1] in ("projects", "documents") else p for i, p in enumerate(parts))
