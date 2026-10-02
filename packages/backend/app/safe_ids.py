"""Validation for ids that end up inside S3 keys and DuckDB glob paths.

Chat history is read with DuckDB ``read_json_auto`` over a glob such as
``sessions/{user}/{project}/session_{id}/agents/*/messages/message_*.json``.
A request id containing glob syntax (``*``, ``?``, ``[``) or a path separator
would widen that glob to other users' or projects' sessions, so every id that
is placed in such a path must pass :func:`safe_segment` first.
"""

import re

from fastapi import HTTPException

# Glob metacharacters (DuckDB/fnmatch), path separators and control characters.
_UNSAFE = re.compile(r"[*?\[\]{}/\\\x00-\x1f\x7f]")


def safe_segment(value: str | None, field: str) -> str:
    """Return ``value`` if it is safe as one path segment, else raise HTTP 400."""
    if not value or value in (".", "..") or len(value) > 256 or _UNSAFE.search(value):
        raise HTTPException(status_code=400, detail=f"Invalid {field}")
    return value


def is_safe_segment(value: str | None) -> bool:
    """Non-raising form of :func:`safe_segment`."""
    try:
        safe_segment(value, "id")
    except HTTPException:
        return False
    return True
