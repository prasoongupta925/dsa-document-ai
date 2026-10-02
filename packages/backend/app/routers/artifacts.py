from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from app.config import get_config
from app.ddb.artifacts import (
    delete_artifact_item,
    get_artifact_item,
    query_user_artifacts,
    query_user_project_artifacts,
)
from app.presigned import PresignError, check_key, user_prefix
from app.s3 import PRESIGNED_URL_EXPIRES_IN, delete_s3_prefix, presign_get

router = APIRouter(prefix="/artifacts", tags=["artifacts"])

# The web app sends the Cognito username; artifacts live under {user_id}/ in the
# agent bucket. Authentication is AWS IAM (SigV4) at API Gateway.
UserId = Annotated[str, Header(alias="x-user-id", pattern=r"^[^\x00-\x1f\x7f]{1,256}$")]


class ArtifactResponse(BaseModel):
    artifact_id: str
    user_id: str
    project_id: str
    filename: str
    content_type: str
    s3_key: str
    s3_bucket: str
    file_size: int
    created_at: str


class ListArtifactsResponse(BaseModel):
    items: list[ArtifactResponse]
    next_cursor: str | None


class PresignedUrlResponse(BaseModel):
    url: str
    expires_in: int


class DeletedArtifactInfo(BaseModel):
    artifact_id: str


class DeleteArtifactResponse(BaseModel):
    message: str
    details: DeletedArtifactInfo


@router.get("", response_model=ListArtifactsResponse)
def list_artifacts(
    user_id: str = Header(alias="x-user-id"),
    project_id: str | None = Query(None, description="Filter by project ID"),
    limit: int = Query(20, description="Number of items to return"),
    next_cursor: str | None = Query(None, description="Pagination cursor"),
) -> ListArtifactsResponse:
    """List artifacts for a user, optionally filtered by project."""
    if project_id:
        result = query_user_project_artifacts(user_id, project_id, limit, next_cursor)
    else:
        result = query_user_artifacts(user_id, limit, next_cursor)

    items = [
        ArtifactResponse(
            artifact_id=artifact.artifact_id,
            user_id=artifact.data.user_id,
            project_id=artifact.data.project_id,
            filename=artifact.data.filename,
            content_type=artifact.data.content_type,
            s3_key=artifact.data.s3_key,
            s3_bucket=artifact.data.s3_bucket,
            file_size=artifact.data.file_size,
            created_at=artifact.created_at,
        )
        for artifact in result.items
    ]

    return ListArtifactsResponse(items=items, next_cursor=result.next_cursor)


@router.get(
    "/download-url",
    responses={
        400: {"description": "The key is not a plain S3 key (e.g. a '..' segment)"},
        403: {"description": "The key is outside the caller's prefix"},
    },
)
def get_artifact_download_url(
    user_id: UserId,
    key: str = Query(
        description="S3 key in the agent bucket under {user_id}/, e.g. {user_id}/{project_id}/artifacts/..."
    ),
) -> PresignedUrlResponse:
    """Presigned GET (5 minutes) for one of the caller's objects in the agent bucket.

    The bucket is always the agent bucket; the key must be a plain key under the
    caller's ``{user_id}/`` prefix, where the agents write artifacts.
    """
    try:
        check_key(key, user_prefix(user_id))
    except PresignError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None

    bucket = get_config().agent_storage_bucket_name
    if not bucket:
        raise HTTPException(status_code=503, detail="Agent storage is not configured")

    print(f"presign get artifact user={user_id}")
    return PresignedUrlResponse(url=presign_get(bucket, key), expires_in=PRESIGNED_URL_EXPIRES_IN)


@router.delete("/{artifact_id:path}")
def delete_artifact(
    artifact_id: str,
    user_id: str = Header(alias="x-user-id"),
) -> DeleteArtifactResponse:
    """Delete an artifact and its S3 objects."""
    artifact = get_artifact_item(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    if artifact.data.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this artifact")

    # s3_key에서 artifact 폴더 prefix 추출
    prefix = artifact.data.s3_key.rsplit("/", 1)[0] + "/"
    delete_s3_prefix(artifact.data.s3_bucket, prefix)

    # Delete from DynamoDB
    delete_artifact_item(artifact_id)

    return DeleteArtifactResponse(
        message=f"Artifact {artifact_id} deleted",
        details=DeletedArtifactInfo(artifact_id=artifact_id),
    )
