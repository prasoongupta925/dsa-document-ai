import json
from collections.abc import Callable
from functools import lru_cache
from urllib.parse import urlparse

import boto3
from botocore.config import Config as BotoConfig

from app.config import get_config
from app.presigned import PresignError, check_key

# Lifetime of every presigned URL the backend hands to the browser (uploads and
# downloads). S3 checks the expiry when a request starts, so an upload or a
# download that started in time still completes.
PRESIGNED_URL_EXPIRES_IN = 300

# Lifetime of the URLs embedded in API responses (segment page images, images
# in analysis markdown, video chapters, chat attachments). The viewer renders
# them as <img>/<video> sources and requests them again when it re-renders, so
# they last longer; each one is limited to its document's or session's folder
# (see presign_get_within).
EMBEDDED_URL_EXPIRES_IN = 3600

# Turns an s3:// reference into a URL, or None to leave the reference as is.
Presigner = Callable[[str], str | None]


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse S3 URI into bucket and key."""
    parsed = urlparse(uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    return bucket, key


@lru_cache
def get_s3_client():
    """Get cached S3 client singleton."""
    return boto3.client("s3")


@lru_cache
def get_s3_presign_client():
    """S3 client for URLs the browser uses: SigV4 on the bucket's regional endpoint.

    Without an explicit signature version botocore may presign for the legacy
    global endpoint (bucket.s3.amazonaws.com), which answers a new bucket outside
    us-east-1 with a 307 redirect that fails the browser's CORS preflight.
    """
    return boto3.client(
        "s3",
        region_name=get_config().aws_region,
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def presign_put(bucket: str, key: str, content_type: str, content_length: int) -> str:
    """Presigned PUT for exactly one object, content type and size.

    Content-Type and Content-Length are signed headers: S3 rejects an upload
    whose type or size differs from what the backend validated.
    """
    return get_s3_presign_client().generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": key, "ContentType": content_type, "ContentLength": content_length},
        ExpiresIn=PRESIGNED_URL_EXPIRES_IN,
    )


def presign_get(bucket: str, key: str) -> str:
    """Presigned GET for one object."""
    return get_s3_presign_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=PRESIGNED_URL_EXPIRES_IN,
    )


def _get_content_type(key: str) -> str | None:
    """Get content type based on file extension."""
    ext = key.lower().split(".")[-1] if "." in key else ""
    content_types = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
        "svg": "image/svg+xml",
        "pdf": "application/pdf",
        "mp4": "video/mp4",
        "mov": "video/quicktime",
        "avi": "video/x-msvideo",
        "mkv": "video/x-matroska",
        "webm": "video/webm",
    }
    return content_types.get(ext)


def split_s3_uri(uri: str) -> tuple[str, str] | None:
    """Bucket and key of ``s3://bucket/key`` taken literally; None for anything else.

    Unlike parse_s3_uri (urlparse), '?' and '#' stay part of the key, so a
    reference cannot hide a query or fragment that changes the key being signed.
    """
    if not isinstance(uri, str) or not uri.startswith("s3://"):
        return None
    bucket, slash, key = uri[len("s3://") :].partition("/")
    if not bucket or not slash or not key:
        return None
    return bucket, key


def presign_get_within(uri: str, bucket: str, prefix: str, expires_in: int = EMBEDDED_URL_EXPIRES_IN) -> str | None:
    """Presigned GET for an ``s3://`` reference found in stored data, inside one folder only.

    References in segment analyses (markdown images, page images, video
    chapters) and in chat messages come from documents and model output, which
    a user can shape (a .md upload, a Q&A instruction). They are signed only
    when they name ``bucket`` and a plain key under ``prefix`` (the document's
    or the session's own folder); anything else gets None. Without this check
    a reference to another bucket or user's key would be signed with the
    backend's role.
    """
    parts = split_s3_uri(uri)
    if parts is None or parts[0] != bucket:
        return None
    key = parts[1]
    try:
        check_key(key, prefix)
    except PresignError:
        return None

    params = {"Bucket": bucket, "Key": key}
    # Add ResponseContentType for images to fix ORB blocking
    content_type = _get_content_type(key)
    if content_type:
        params["ResponseContentType"] = content_type

    return get_s3_presign_client().generate_presigned_url("get_object", Params=params, ExpiresIn=expires_in)


def _refuse(_uri: str) -> None:
    return None


def folder_presigner(bucket: str, prefix: str) -> Presigner:
    """Presigner for references under ``prefix`` (ends with '/') in ``bucket``."""
    if not bucket or not prefix.endswith("/"):
        return _refuse
    return lambda uri: presign_get_within(uri, bucket, prefix)


def document_presigner(file_uri: str) -> Presigner:
    """Presigner for one document's folder, from its file URI.

    ``s3://{bucket}/projects/{project_id}/documents/{document_id}/{file}``:
    everything the pipeline writes for a document (page images, BDA output
    and its assets, format-parser slides, transcripts, analysis) lives under
    ``projects/{project_id}/documents/{document_id}/``. Any other layout
    signs nothing.
    """
    parts = split_s3_uri(file_uri)
    if parts is None:
        return _refuse
    bucket, key = parts
    segments = key.split("/")
    if len(segments) < 5 or segments[0] != "projects" or segments[2] != "documents":
        return _refuse
    return folder_presigner(bucket, f"projects/{segments[1]}/documents/{segments[3]}/")


def delete_s3_prefix(bucket: str, prefix: str) -> int:
    """Delete all objects under a prefix."""
    s3 = get_s3_client()
    deleted_count = 0
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objects = page.get("Contents", [])
        if not objects:
            continue

        delete_keys = [{"Key": obj["Key"]} for obj in objects]
        s3.delete_objects(Bucket=bucket, Delete={"Objects": delete_keys})
        deleted_count += len(delete_keys)

    return deleted_count


def get_analysis_prefix_from_file_uri(file_uri: str) -> tuple[str, str]:
    """Get S3 bucket and analysis prefix from file URI.

    Args:
        file_uri: S3 URI like s3://bucket/projects/{project_id}/documents/{document_id}/{file}

    Returns:
        Tuple of (bucket, analysis_prefix)
    """
    bucket, key = parse_s3_uri(file_uri)
    # Remove the filename to get the document folder
    doc_folder = key.rsplit("/", 1)[0]
    analysis_prefix = f"{doc_folder}/analysis/segment_"
    return bucket, analysis_prefix


def get_segment_key_by_index(file_uri: str, segment_index: int) -> tuple[str, str]:
    """Return (bucket, key) for a single segment JSON by index."""
    bucket, prefix = get_analysis_prefix_from_file_uri(file_uri)
    return bucket, f"{prefix}{segment_index:04d}.json"


def list_segment_keys(file_uri: str) -> list[str]:
    """List all segment JSON file keys from S3.

    Args:
        file_uri: S3 URI of the original document

    Returns:
        List of S3 keys for segment files, sorted by segment index
    """
    bucket, prefix = get_analysis_prefix_from_file_uri(file_uri)
    s3 = get_s3_client()
    paginator = s3.get_paginator("list_objects_v2")

    segment_keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json"):
                segment_keys.append(key)

    # Sort by segment index (segment_0000.json, segment_0001.json, ...)
    segment_keys.sort()
    return segment_keys


def get_json_object(bucket: str, key: str) -> dict | None:
    """Read one JSON object (e.g. a segment analysis file); None when missing or unreadable.

    Logs the error type only: the object may hold document content.
    """
    try:
        response = get_s3_client().get_object(Bucket=bucket, Key=key)
        data = json.loads(response["Body"].read().decode("utf-8"))
    except Exception as e:  # a page that cannot be read is skipped
        print(f"s3 read failed for {key}: {type(e).__name__}")
        return None
    return data if isinstance(data, dict) else None
