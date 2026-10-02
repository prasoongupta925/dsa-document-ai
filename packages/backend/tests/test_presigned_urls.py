"""Backend-issued presigned URLs: the only way the web app reaches S3.

Covers POST /projects/{id}/documents (upload URL), GET
/projects/{id}/documents/download-url and GET /artifacts/download-url: caller
header, unknown project, keys outside the project / the caller's prefix, path
traversal, file name / type / size limits and the 5-minute expiry. URLs are
signed for real (offline, with the fake credentials from conftest) so the
assertions check what S3 would see.
"""

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.main import app
from app.presigned import MAX_UPLOAD_BYTES, PresignError, check_key, check_upload

client = TestClient(app)

DOC_BUCKET = "idp-v2-document-storage-test-ap-south-1"
AGENT_BUCKET = "idp-v2-agent-storage-test-ap-south-1"
USER = {"x-user-id": "alice"}

PROJECT_ITEM = {
    "Item": {
        "PK": "PROJ#proj-1",
        "SK": "META",
        "GSI1PK": "PROJECTS",
        "GSI1SK": "2026-09-29T00:00:00+00:00",
        "data": {"project_id": "proj-1", "name": "Synthetic loan files", "description": "", "status": "active"},
        "created_at": "2026-09-29T00:00:00+00:00",
        "updated_at": "2026-09-29T00:00:00+00:00",
    }
}


@pytest.fixture
def config():
    cfg = Config(
        aws_region="ap-south-1",
        document_storage_bucket_name=DOC_BUCKET,
        agent_storage_bucket_name=AGENT_BUCKET,
    )
    with (
        patch("app.s3.get_config", return_value=cfg),
        patch("app.routers.documents.get_config", return_value=cfg),
        patch("app.routers.artifacts.get_config", return_value=cfg),
    ):
        yield cfg


@pytest.fixture
def table():
    """One mock table for projects and documents; proj-1 exists, nothing else does."""
    mock_table = MagicMock()
    mock_table.get_item.side_effect = lambda Key: PROJECT_ITEM if Key == {"PK": "PROJ#proj-1", "SK": "META"} else {}
    with (
        patch("app.ddb.projects.get_table", return_value=mock_table),
        patch("app.ddb.documents.get_table", return_value=mock_table),
    ):
        yield mock_table


def _url_parts(url: str) -> tuple[str, str, dict[str, str]]:
    parts = urlsplit(url)
    query = {name: values[0] for name, values in parse_qs(parts.query).items()}
    return parts.netloc, parts.path, query


def _upload(file_name="loan_application.pdf", content_type="application/pdf", file_size=1024, project="proj-1"):
    return client.post(
        f"/projects/{project}/documents",
        json={"file_name": file_name, "content_type": content_type, "file_size": file_size},
        headers=USER,
    )


def _doc_url(key: str, project="proj-1", headers=USER, raw=False):
    return client.get(f"/projects/{project}/documents/download-url?key={key if raw else quote(key)}", headers=headers)


def _artifact_url(key: str, headers=USER, raw=False):
    return client.get(f"/artifacts/download-url?key={key if raw else quote(key)}", headers=headers)


# --------------------------------------------------------------------- upload
class TestUploadUrl:
    def test_presigned_put_for_the_documents_key_type_and_size(self, config, table):
        response = _upload()

        assert response.status_code == 200
        data = response.json()
        doc_id = data["document_id"]
        assert data["file_name"] == "loan_application.pdf"
        assert data["expires_in"] == 300

        host, path, query = _url_parts(data["upload_url"])
        # Regional endpoint, SigV4, 5 minutes, and the key layout the pipeline expects.
        assert host == f"{DOC_BUCKET}.s3.ap-south-1.amazonaws.com"
        assert path == f"/projects/proj-1/documents/{doc_id}/{doc_id}.pdf"
        assert query["X-Amz-Algorithm"] == "AWS4-HMAC-SHA256"
        assert query["X-Amz-Expires"] == "300"
        assert "/ap-south-1/s3/aws4_request" in query["X-Amz-Credential"]
        # S3 rejects an upload whose type or size differs from the validated ones.
        assert query["X-Amz-SignedHeaders"] == "content-length;content-type;host"

        # The document record points at the same key (status "uploading" as before).
        item = table.put_item.call_args.kwargs["Item"]
        assert item["data"]["s3_key"] == f"projects/proj-1/documents/{doc_id}/{doc_id}.pdf"
        assert item["data"]["status"] == "uploading"
        assert item["data"]["file_type"] == "application/pdf"

    def test_expiry_and_signed_headers_are_passed_to_s3(self, config, table):
        s3 = MagicMock()
        s3.generate_presigned_url.return_value = "https://example.invalid/put"
        with patch("app.s3.get_s3_presign_client", return_value=s3):
            response = _upload(file_name="salary_slip.png", content_type="image/png", file_size=2048)

        assert response.status_code == 200
        doc_id = response.json()["document_id"]
        s3.generate_presigned_url.assert_called_once_with(
            "put_object",
            Params={
                "Bucket": DOC_BUCKET,
                "Key": f"projects/proj-1/documents/{doc_id}/{doc_id}.png",
                "ContentType": "image/png",
                "ContentLength": 2048,
            },
            ExpiresIn=300,
        )

    def test_extension_is_kept_as_given_in_the_key(self, config, table):
        response = _upload(file_name="Scan.PDF")

        assert response.status_code == 200
        doc_id = response.json()["document_id"]
        _, path, _ = _url_parts(response.json()["upload_url"])
        assert path.endswith(f"/{doc_id}/{doc_id}.PDF")

    def test_content_type_is_signed_exactly_as_sent(self, config, table):
        s3 = MagicMock()
        s3.generate_presigned_url.return_value = "https://example.invalid/put"
        with patch("app.s3.get_s3_presign_client", return_value=s3):
            response = _upload(file_name="notes.txt", content_type="text/plain;charset=UTF-8")

        assert response.status_code == 200
        assert s3.generate_presigned_url.call_args.kwargs["Params"]["ContentType"] == "text/plain;charset=UTF-8"

    def test_unknown_project_is_404_and_nothing_is_signed(self, config, table):
        s3 = MagicMock()
        with patch("app.s3.get_s3_presign_client", return_value=s3):
            response = _upload(project="proj-unknown")

        assert response.status_code == 404
        assert response.json()["detail"] == "Project not found"
        table.put_item.assert_not_called()
        s3.generate_presigned_url.assert_not_called()

    def test_invalid_project_id_is_rejected(self, config, table):
        assert _upload(project="proj.1").status_code == 422
        table.get_item.assert_not_called()

    @pytest.mark.parametrize(
        ("file_size", "status"),
        [(1, 200), (MAX_UPLOAD_BYTES, 200), (MAX_UPLOAD_BYTES + 1, 400), (0, 400), (-5, 400)],
    )
    def test_size_limits(self, config, table, file_size, status):
        response = _upload(file_size=file_size)

        assert response.status_code == status
        if status == 400:
            table.put_item.assert_not_called()

    @pytest.mark.parametrize(
        ("file_name", "content_type"),
        [
            ("bank_statement.pdf", "application/pdf"),
            ("bank_statement.pdf", "application/octet-stream"),
            ("form16.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("statement.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("ledger.csv", "application/vnd.ms-excel"),
            ("call.wav", "audio/x-wav"),
            ("site_visit.mov", "video/quicktime"),
            ("example_com_20260929_1000.webreq", "application/x-webreq"),
            ("plan.dxf", "application/dxf"),
        ],
    )
    def test_supported_types(self, config, table, file_name, content_type):
        assert _upload(file_name=file_name, content_type=content_type).status_code == 200

    @pytest.mark.parametrize(
        ("file_name", "content_type", "detail"),
        [
            ("payload.exe", "application/octet-stream", "Unsupported file type: .exe"),
            ("page.html", "text/html", "Unsupported file type: .html"),
            ("README", "text/plain", "File name has no extension"),
            ("bank_statement.pdf", "text/html", "Content type text/html does not match a .pdf file"),
            ("photo.png", "image/svg+xml", "Content type image/svg+xml does not match a .png file"),
            ("bank_statement.pdf", "pdf", "Content type is not a valid MIME type"),
            ("statement.pdf", "application/pdf\r\nx-amz-acl: public-read", "Content type is not a valid MIME type"),
            ("bank_statement.pdf", "", "Content type is not a valid MIME type"),
        ],
    )
    def test_type_limits(self, config, table, file_name, content_type, detail):
        response = _upload(file_name=file_name, content_type=content_type)

        assert response.status_code == 400
        assert response.json()["detail"] == detail
        table.put_item.assert_not_called()

    @pytest.mark.parametrize(
        "file_name",
        ["", "../other.pdf", "a/b.pdf", "a\\b.pdf", "bad\x00name.pdf", "tab\tname.pdf", "x" * 252 + ".pdf"],
    )
    def test_file_name_limits(self, config, table, file_name):
        assert _upload(file_name=file_name).status_code == 400
        table.put_item.assert_not_called()

    def test_storage_not_configured_is_503(self, table):
        cfg = Config(document_storage_bucket_name="")
        with patch("app.routers.documents.get_config", return_value=cfg):
            assert _upload().status_code == 503
        table.put_item.assert_not_called()


# ----------------------------------------------------------- document download
class TestDocumentDownloadUrl:
    KEY = "projects/proj-1/documents/doc-1/doc-1.pdf"

    def test_presigned_get_for_a_key_in_the_project(self, config, table):
        response = _doc_url(self.KEY)

        assert response.status_code == 200
        data = response.json()
        assert data["expires_in"] == 300
        host, path, query = _url_parts(data["url"])
        assert host == f"{DOC_BUCKET}.s3.ap-south-1.amazonaws.com"
        assert path == f"/{self.KEY}"
        assert query["X-Amz-Expires"] == "300"
        assert query["X-Amz-SignedHeaders"] == "host"

    def test_segment_images_of_the_project_are_allowed(self, config, table):
        assert _doc_url("projects/proj-1/documents/doc-1/segments/segment_0001.png").status_code == 200

    def test_expiry_is_passed_to_s3(self, config, table):
        s3 = MagicMock()
        s3.generate_presigned_url.return_value = "https://example.invalid/get"
        with patch("app.s3.get_s3_presign_client", return_value=s3):
            assert _doc_url(self.KEY).status_code == 200
        s3.generate_presigned_url.assert_called_once_with(
            "get_object", Params={"Bucket": DOC_BUCKET, "Key": self.KEY}, ExpiresIn=300
        )

    def test_caller_header_is_required(self, config, table):
        response = _doc_url(self.KEY, headers={})

        assert response.status_code == 422
        table.get_item.assert_not_called()

    def test_unknown_project_is_404(self, config, table):
        response = _doc_url("projects/proj-unknown/documents/doc-1/doc-1.pdf", project="proj-unknown")

        assert response.status_code == 404
        assert response.json()["detail"] == "Project not found"

    @pytest.mark.parametrize(
        "key",
        [
            "projects/proj-2/documents/doc-9/doc-9.pdf",  # another project
            "projects/proj-10/documents/doc-9/doc-9.pdf",  # shares the text prefix "projects/proj-1"
            "projects/proj-1",  # the project folder itself, without the separator
            "alice/proj-1/artifacts/art_1/report.docx",  # not a document key at all
            "sessions/alice/proj-1/session_1/session.json",
        ],
    )
    def test_key_outside_the_project_is_403(self, config, table, key):
        s3 = MagicMock()
        with patch("app.s3.get_s3_presign_client", return_value=s3):
            response = _doc_url(key)

        assert response.status_code == 403
        assert response.json()["detail"] == "Key is outside the allowed prefix"
        s3.generate_presigned_url.assert_not_called()

    @pytest.mark.parametrize(
        "key",
        [
            "projects/proj-1/../proj-2/documents/doc-9/doc-9.pdf",
            "projects/proj-1/documents/../../proj-2/doc-9.pdf",
            "projects/proj-1/./documents/doc-1/doc-1.pdf",
            "projects/proj-1//documents/doc-1/doc-1.pdf",
            "/projects/proj-1/documents/doc-1/doc-1.pdf",
            "projects/proj-1/documents/doc-1/",
            "projects/proj-1/%2e%2e/proj-2/doc-9.pdf",  # arrives decoded once: a literal "%2e%2e" segment
            "projects/proj-1/%252e%252e/proj-2/doc-9.pdf",
            "projects/proj-1/documents/doc-1/..%2f..%2fproj-2",
            "projects/proj-1\\..\\proj-2\\doc-9.pdf",
            "projects/proj-1/documents/doc-1/\x00.pdf",
            "projects/proj-1/" + "a" * 1100,
            "",
        ],
    )
    def test_traversal_and_malformed_keys_are_400(self, config, table, key):
        s3 = MagicMock()
        with patch("app.s3.get_s3_presign_client", return_value=s3):
            response = _doc_url(key)

        assert response.status_code == 400
        table.get_item.assert_not_called()
        s3.generate_presigned_url.assert_not_called()

    def test_traversal_sent_percent_encoded_is_400(self, config, table):
        # %2e%2e%2f in the query string decodes to "../" before validation.
        assert _doc_url("projects/proj-1/%2e%2e%2fproj-2/doc-9.pdf", raw=True).status_code == 400

    def test_missing_key_is_422(self, config, table):
        assert client.get("/projects/proj-1/documents/download-url", headers=USER).status_code == 422

    def test_route_does_not_shadow_get_document(self, config, table):
        # /documents/{document_id} still answers for a document id.
        assert client.get("/projects/proj-1/documents/doc-unknown").status_code == 404

    def test_storage_not_configured_is_503(self, table):
        cfg = Config(document_storage_bucket_name="")
        with patch("app.routers.documents.get_config", return_value=cfg):
            assert _doc_url(self.KEY).status_code == 503


# ----------------------------------------------------------- artifact download
class TestArtifactDownloadUrl:
    KEY = "alice/proj-1/artifacts/art_abc123/loan_readiness_letter.docx"

    def test_presigned_get_for_the_callers_artifact(self, config):
        response = _artifact_url(self.KEY)

        assert response.status_code == 200
        host, path, query = _url_parts(response.json()["url"])
        assert host == f"{AGENT_BUCKET}.s3.ap-south-1.amazonaws.com"
        assert path == f"/{self.KEY}"
        assert query["X-Amz-Expires"] == "300"
        assert response.json()["expires_in"] == 300

    def test_file_names_with_spaces_and_unicode_are_allowed(self, config):
        key = "alice/proj-1/artifacts/art_abc123/Loan letter (final) - राहुल.docx"
        response = _artifact_url(key)

        assert response.status_code == 200
        assert urlsplit(response.json()["url"]).path == "/" + quote(key)

    def test_expiry_is_passed_to_s3(self, config):
        s3 = MagicMock()
        s3.generate_presigned_url.return_value = "https://example.invalid/get"
        with patch("app.s3.get_s3_presign_client", return_value=s3):
            assert _artifact_url(self.KEY).status_code == 200
        s3.generate_presigned_url.assert_called_once_with(
            "get_object", Params={"Bucket": AGENT_BUCKET, "Key": self.KEY}, ExpiresIn=300
        )

    def test_caller_header_is_required(self, config):
        assert _artifact_url(self.KEY, headers={}).status_code == 422

    @pytest.mark.parametrize(
        "key",
        [
            "bob/proj-1/artifacts/art_abc123/letter.docx",  # another user's artifact
            "alice2/proj-1/artifacts/art_abc123/letter.docx",  # shares the text prefix "alice"
            "projects/proj-1/documents/doc-1/doc-1.pdf",
        ],
    )
    def test_key_outside_the_callers_prefix_is_403(self, config, key):
        s3 = MagicMock()
        with patch("app.s3.get_s3_presign_client", return_value=s3):
            response = _artifact_url(key)

        assert response.status_code == 403
        s3.generate_presigned_url.assert_not_called()

    @pytest.mark.parametrize(
        "key",
        [
            "alice/../bob/proj-1/artifacts/art_1/letter.docx",
            "alice/proj-1/artifacts/../../../bob/letter.docx",
            "alice//bob/letter.docx",
            "alice/%2e%2e/bob/letter.docx",
        ],
    )
    def test_traversal_is_400(self, config, key):
        assert _artifact_url(key).status_code == 400

    @pytest.mark.parametrize("user_id", ["alice/proj-1", "..", "."])
    def test_user_id_that_is_not_one_segment_is_400(self, config, user_id):
        response = _artifact_url(f"{user_id}/artifacts/x.docx", headers={"x-user-id": user_id})

        assert response.status_code == 400

    def test_storage_not_configured_is_503(self):
        with patch("app.routers.artifacts.get_config", return_value=Config(agent_storage_bucket_name="")):
            assert _artifact_url(self.KEY).status_code == 503


# ------------------------------------------------------------------ validators
class TestValidators:
    def test_check_key_returns_the_key_unchanged(self):
        assert check_key("projects/p/documents/d/d.pdf", "projects/p/") == "projects/p/documents/d/d.pdf"

    def test_check_key_statuses(self):
        with pytest.raises(PresignError) as outside:
            check_key("projects/q/x.pdf", "projects/p/")
        with pytest.raises(PresignError) as traversal:
            check_key("projects/p/../q/x.pdf", "projects/p/")

        assert outside.value.status == 403
        assert traversal.value.status == 400

    def test_check_upload_returns_the_extension_as_given(self):
        assert check_upload("Scan.PDF", "application/pdf", 10) == "PDF"
        assert check_upload("archive.tar.xlsx", "application/octet-stream", 10) == "xlsx"
