"""Checks behind the presigned S3 URLs the backend issues to the web app.

The browser has no S3 permissions of its own: the Cognito identity pool role
may only call this API (IAM / SigV4 at API Gateway), the agent runtime and the
WebSocket API. Every upload and download therefore goes through a short-lived
URL that the backend signs after these checks:

- uploads: a supported file type, a well-formed content type allowed for that
  type, a size of 1 byte to 500 MB, and one exact key per document;
- document downloads: a plain key under ``projects/{project_id}/``;
- artifact downloads: a plain key under the caller's ``{user_id}/`` prefix;
- URLs embedded in responses (segment images and video, images in analysis
  markdown, chat attachments; 1 hour): a plain key in the document's own
  ``projects/{project_id}/documents/{document_id}/`` folder, or in the chat
  session's own folder (s3.presign_get_within).

A "plain" key has no empty, ``.`` or ``..`` segment (also percent-encoded), no
backslash and no control character, so it cannot step out of its prefix.
"""

import re
from urllib.parse import unquote

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
MAX_FILE_NAME_LENGTH = 255
MAX_KEY_BYTES = 1024  # S3's own limit

_OCTET_STREAM = "application/octet-stream"

# File types the pipeline processes (preprocessing/type-detection MIME_TYPE_MAP,
# by extension) and the content types a browser may report for each. The web
# app sends file.type, or application/octet-stream when the browser leaves it
# empty, so that one is accepted for every supported extension.
UPLOAD_CONTENT_TYPES: dict[str, frozenset[str]] = {
    "pdf": frozenset({"application/pdf", "application/x-pdf"}),
    "doc": frozenset({"application/msword"}),
    "docx": frozenset({"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}),
    "ppt": frozenset({"application/vnd.ms-powerpoint"}),
    "pptx": frozenset({"application/vnd.openxmlformats-officedocument.presentationml.presentation"}),
    "txt": frozenset({"text/plain"}),
    "md": frozenset({"text/markdown", "text/x-markdown", "text/plain"}),
    "csv": frozenset(
        {
            "text/csv",
            "application/csv",
            "text/x-csv",
            "text/comma-separated-values",
            "text/x-comma-separated-values",
            "application/vnd.ms-excel",
            "text/plain",
        }
    ),
    "tsv": frozenset({"text/tab-separated-values", "text/plain"}),
    "xls": frozenset({"application/vnd.ms-excel"}),
    "xlsx": frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}),
    "png": frozenset({"image/png"}),
    "jpg": frozenset({"image/jpeg", "image/pjpeg"}),
    "jpeg": frozenset({"image/jpeg", "image/pjpeg"}),
    "gif": frozenset({"image/gif"}),
    "tif": frozenset({"image/tiff"}),
    "tiff": frozenset({"image/tiff"}),
    "webp": frozenset({"image/webp"}),
    "mp4": frozenset({"video/mp4"}),
    "mov": frozenset({"video/quicktime"}),
    "avi": frozenset({"video/x-msvideo", "video/avi", "video/msvideo"}),
    "mkv": frozenset({"video/x-matroska"}),
    "webm": frozenset({"video/webm", "audio/webm"}),
    "mp3": frozenset({"audio/mpeg", "audio/mp3"}),
    "wav": frozenset({"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"}),
    "flac": frozenset({"audio/flac", "audio/x-flac"}),
    "m4a": frozenset({"audio/mp4", "audio/x-m4a", "audio/m4a"}),
    "webreq": frozenset({"application/x-webreq"}),
    "dxf": frozenset({"application/dxf", "application/x-dxf", "image/vnd.dxf", "image/x-dxf"}),
}

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
# RFC 6838 type/subtype, optionally followed by parameters (e.g. "; charset=utf-8").
_TOKEN = r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}"
_CONTENT_TYPE = re.compile(rf"^(?P<base>{_TOKEN}/{_TOKEN})(?:\s*;\s*{_TOKEN}=(?:{_TOKEN}|\"[^\"\\]*\"))*$")


class PresignError(ValueError):
    """A request the backend will not sign; ``status`` is the HTTP status to answer."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def check_upload(file_name: str, content_type: str, file_size: int) -> str:
    """Validate an upload request; return the file extension as given (used in the key)."""
    if not file_name or len(file_name) > MAX_FILE_NAME_LENGTH:
        raise PresignError(400, f"File name must be 1 to {MAX_FILE_NAME_LENGTH} characters")
    if _CONTROL_CHARS.search(file_name) or "/" in file_name or "\\" in file_name or file_name in {".", ".."}:
        raise PresignError(400, "File name must not contain a path, a backslash or control characters")

    if file_size > MAX_UPLOAD_BYTES:
        raise PresignError(400, "File size exceeds 500MB limit")
    if file_size < 1:
        raise PresignError(400, "File is empty")

    ext = file_name.rsplit(".", 1)[-1] if "." in file_name else ""
    allowed = UPLOAD_CONTENT_TYPES.get(ext.lower())
    if allowed is None:
        raise PresignError(400, f"Unsupported file type: .{ext}" if ext else "File name has no extension")

    # fullmatch, not match + "$" (which also accepts a trailing newline), and no
    # control characters anywhere (a quoted parameter could carry CR/LF): the
    # value is signed as the Content-Type header and stored with the document.
    match = _CONTENT_TYPE.fullmatch(content_type or "")
    if not match or _CONTROL_CHARS.search(content_type):
        raise PresignError(400, "Content type is not a valid MIME type")
    base = match.group("base").lower()
    if base != _OCTET_STREAM and base not in allowed:
        raise PresignError(400, f"Content type {base} does not match a .{ext.lower()} file")
    return ext


def _unsafe_segment(segment: str) -> bool:
    """Empty, '.' or '..' (also percent-encoded, up to 3 times), or a hidden slash."""
    decoded = segment
    for _ in range(3):
        decoded = unquote(decoded)
    return decoded in {"", ".", ".."} or "/" in decoded or "\\" in decoded


def check_key(key: str, prefix: str) -> str:
    """Return ``key`` when it is a plain S3 key under ``prefix`` (which ends with '/')."""
    if not key or len(key.encode("utf-8")) > MAX_KEY_BYTES:
        raise PresignError(400, f"Key must be 1 to {MAX_KEY_BYTES} bytes")
    if _CONTROL_CHARS.search(key) or "\\" in key:
        raise PresignError(400, "Key must not contain control characters or a backslash")
    if any(_unsafe_segment(segment) for segment in key.split("/")):
        raise PresignError(400, "Key must be a plain path without empty, '.' or '..' segments")
    if not key.startswith(prefix):
        raise PresignError(403, "Key is outside the allowed prefix")
    return key


def project_prefix(project_id: str) -> str:
    """Document-bucket prefix of one project (keys are projects/{project_id}/documents/...)."""
    if _unsafe_segment(project_id) or _CONTROL_CHARS.search(project_id):
        raise PresignError(400, "Invalid project id")
    return f"projects/{project_id}/"


def user_prefix(user_id: str) -> str:
    """Agent-bucket prefix of one user (artifacts are {user_id}/{project_id}/artifacts/...)."""
    if _unsafe_segment(user_id) or _CONTROL_CHARS.search(user_id):
        raise PresignError(400, "Invalid user id")
    return f"{user_id}/"
