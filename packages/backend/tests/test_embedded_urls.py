"""Presigned URLs embedded in API responses stay inside their document or session.

Segment analyses (page images, video chapters, images in the markdown) and chat
messages carry s3:// references. The markdown comes from the uploaded document
and from model output (a .md upload or a Q&A instruction can put any text
there), so a reference must not make the backend sign another bucket's or
another user's object. URLs are signed for real (offline, with the fake
credentials from conftest) so the assertions check what S3 would see.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.main import app
from app.markdown import transform_markdown_images
from app.presigned import PresignError, check_upload
from app.s3 import document_presigner, folder_presigner, presign_get_within, split_s3_uri

client = TestClient(app)

DOC_BUCKET = "idp-v2-document-storage-test-ap-south-1"
AGENT_BUCKET = "idp-v2-agent-storage-test-ap-south-1"
SESSION_BUCKET = "idp-v2-session-storage-test-ap-south-1"
NOW = "2026-09-29T00:00:00Z"
FILE_URI = f"s3://{DOC_BUCKET}/projects/proj_1/documents/doc-1/doc-1.pdf"
DOC_FOLDER = "projects/proj_1/documents/doc-1/"

# Another user's artifact, written as a markdown image. '?' and '#' would make
# urlparse drop everything after them, so the signed key would be the artifact.
FOREIGN_REFERENCES = [
    f"s3://{AGENT_BUCKET}/bob/proj_9/artifacts/art_1/salary_slip.docx?/assets/",
    f"s3://{AGENT_BUCKET}/bob/proj_9/artifacts/art_1/salary_slip.docx#/assets/x.png",
    f"s3://{AGENT_BUCKET}/bob/proj_9/artifacts/art_1/assets/salary_slip.png",
    f"s3://{DOC_BUCKET}/projects/proj_2/documents/doc-9/assets/page.png",
    f"s3://{DOC_BUCKET}/projects/proj_1/documents/doc-2/assets/page.png",
    f"s3://{DOC_BUCKET}/projects/proj_1/documents/doc-1/../doc-2/assets/page.png",
    f"s3://{SESSION_BUCKET}/sessions/bob/proj_1/session_s1/agents/a/artifacts/x.png",
]


@pytest.fixture(autouse=True)
def config():
    cfg = Config(aws_region="ap-south-1", session_storage_bucket_name=SESSION_BUCKET)
    with patch("app.s3.get_config", return_value=cfg), patch("app.routers.chat.get_config", return_value=cfg):
        yield cfg


def _signed(url: str) -> tuple[str, str, dict[str, str]]:
    parts = urlsplit(url)
    query = {name: values[0] for name, values in parse_qs(parts.query).items()}
    return parts.netloc, unquote(parts.path), query


class TestSplitS3Uri:
    def test_key_is_taken_literally(self):
        assert split_s3_uri("s3://b/a/b.png") == ("b", "a/b.png")
        assert split_s3_uri("s3://b/a/b.png?/assets/") == ("b", "a/b.png?/assets/")
        assert split_s3_uri("s3://b/a#b") == ("b", "a#b")

    @pytest.mark.parametrize("uri", ["", "s3://", "s3://bucket", "s3://bucket/", "https://b/k", None, 7])
    def test_anything_else_is_none(self, uri):
        assert split_s3_uri(uri) is None


class TestPresignGetWithin:
    def test_signs_a_key_in_the_folder_for_the_regional_endpoint(self):
        url = presign_get_within(f"s3://{DOC_BUCKET}/{DOC_FOLDER}preprocessed/page_0001.png", DOC_BUCKET, DOC_FOLDER)

        host, path, query = _signed(url)
        assert host == f"{DOC_BUCKET}.s3.ap-south-1.amazonaws.com"
        assert path == f"/{DOC_FOLDER}preprocessed/page_0001.png"
        assert query["X-Amz-Expires"] == "3600"
        assert query["response-content-type"] == "image/png"

    @pytest.mark.parametrize("uri", FOREIGN_REFERENCES)
    def test_other_buckets_folders_and_malformed_keys_are_not_signed(self, uri):
        with patch("app.s3.get_s3_presign_client") as presign_client:
            assert presign_get_within(uri, DOC_BUCKET, DOC_FOLDER) is None
        presign_client.assert_not_called()

    def test_document_presigner_uses_the_documents_own_folder(self):
        presign = document_presigner(FILE_URI)

        assert presign(f"s3://{DOC_BUCKET}/{DOC_FOLDER}bda-output/job/0/standard_output/0/assets/a.png")
        assert presign(FILE_URI)
        for uri in FOREIGN_REFERENCES:
            assert presign(uri) is None

    @pytest.mark.parametrize(
        "file_uri",
        [
            "",
            f"s3://{DOC_BUCKET}/doc-1.pdf",
            f"s3://{DOC_BUCKET}/other/proj_1/documents/doc-1/doc-1.pdf",
            f"s3://{DOC_BUCKET}/projects/proj_1/uploads/doc-1/doc-1.pdf",
            "https://example.invalid/doc.pdf",
        ],
    )
    def test_document_presigner_signs_nothing_for_another_layout(self, file_uri):
        presign = document_presigner(file_uri)
        assert presign(f"s3://{DOC_BUCKET}/{DOC_FOLDER}preprocessed/page_0001.png") is None
        assert presign(file_uri) is None

    def test_folder_presigner_needs_a_bucket_and_a_folder(self):
        uri = f"s3://{DOC_BUCKET}/{DOC_FOLDER}a.png"
        assert folder_presigner("", DOC_FOLDER)(uri) is None
        assert folder_presigner(DOC_BUCKET, DOC_FOLDER.rstrip("/"))(uri) is None
        assert folder_presigner(DOC_BUCKET, DOC_FOLDER)(uri)


class TestMarkdownWithDocumentPresigner:
    def test_only_the_documents_own_images_become_urls(self):
        presign = document_presigner(FILE_URI)
        own = f"s3://{DOC_BUCKET}/{DOC_FOLDER}bda-output/job/0/standard_output/0/assets/chart.png"
        markdown = f"![own]({own})\n" + "\n".join(f"![x]({uri})" for uri in FOREIGN_REFERENCES)

        result = transform_markdown_images(
            markdown, f"s3://{DOC_BUCKET}/{DOC_FOLDER}preprocessed/page_0001.png", presign=presign
        )

        assert own not in result
        assert f"{DOC_BUCKET}.s3.ap-south-1.amazonaws.com/{DOC_FOLDER}bda-output/" in result
        assert "salary_slip" in result  # left as the s3:// text it was
        assert "X-Amz-Signature" in result.splitlines()[0]
        for line in result.splitlines()[1:]:
            assert "X-Amz-Signature" not in line
            assert "amazonaws.com" not in line

    def test_a_relative_image_cannot_borrow_a_foreign_assets_base(self):
        presign = document_presigner(FILE_URI)
        markdown = f"see s3://{AGENT_BUCKET}/bob/proj_9/artifacts/art_1/assets/ and ![x](./salary_slip.png)"

        result = transform_markdown_images(markdown, "", presign=presign)

        assert "X-Amz-Signature" not in result


class TestSegmentEndpoint:
    def test_foreign_references_in_a_segment_are_not_signed(self):
        workflow = SimpleNamespace(data=SimpleNamespace(file_uri=FILE_URI))
        foreign = FOREIGN_REFERENCES[0]
        segment = {
            "segment_index": 0,
            "segment_type": "VIDEO",
            "image_uri": f"s3://{DOC_BUCKET}/{DOC_FOLDER}preprocessed/page_0001.png",
            "file_uri": f"s3://{AGENT_BUCKET}/bob/proj_9/artifacts/art_1/clip.mp4",
            "bda_indexer": "",
            "format_parser": f"# Uploaded notes\n![x]({foreign})",
            "ai_analysis": [{"analysis_query": "q", "content": f"![y]({foreign})"}],
        }
        with (
            patch("app.routers.workflows.get_workflow_item", return_value=workflow),
            patch("app.routers.workflows._get_segment_from_s3", return_value=segment),
        ):
            response = client.get("/documents/doc-1/workflows/wf-1/segments/0")

        assert response.status_code == 200
        data = response.json()
        host, path, _ = _signed(data["image_url"])
        assert (host, path) == (f"{DOC_BUCKET}.s3.ap-south-1.amazonaws.com", f"/{DOC_FOLDER}preprocessed/page_0001.png")
        assert data["video_url"] is None
        assert data["format_parser"] == f"# Uploaded notes\n![x]({foreign})"
        assert data["ai_analysis"][0]["content"] == f"![y]({foreign})"


class TestChatHistoryAttachments:
    def _history(self, s3_url: str) -> dict:
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            (1, "assistant", [{"image": {"format": "png", "s3_url": s3_url}}], NOW, NOW),
        ]
        with patch("app.routers.chat.get_duckdb_connection", return_value=conn):
            response = client.get("/chat/projects/proj_1/sessions/s1", headers={"x-user-id": "alice"})
        assert response.status_code == 200
        return response.json()["messages"][0]["content"][0]

    def test_attachment_in_the_sessions_folder_is_signed(self):
        key = "sessions/alice/proj_1/session_s1/agents/default/artifacts/img_1.png"

        content = self._history(f"s3://{SESSION_BUCKET}/{key}")

        host, path, query = _signed(content["s3_url"])
        assert (host, path) == (f"{SESSION_BUCKET}.s3.ap-south-1.amazonaws.com", f"/{key}")
        assert query["X-Amz-Expires"] == "3600"

    @pytest.mark.parametrize(
        "s3_url",
        [
            f"s3://{SESSION_BUCKET}/sessions/bob/proj_1/session_s1/agents/default/artifacts/img_1.png",
            f"s3://{SESSION_BUCKET}/sessions/alice/proj_1/session_s2/agents/default/artifacts/img_1.png",
            f"s3://{AGENT_BUCKET}/sessions/alice/proj_1/session_s1/agents/default/artifacts/img_1.png",
            f"s3://{AGENT_BUCKET}/bob/proj_9/artifacts/art_1/salary_slip.docx",
        ],
    )
    def test_attachment_outside_the_session_is_not_signed(self, s3_url):
        assert self._history(s3_url)["s3_url"] is None


class TestUploadContentTypeIsOneHeaderValue:
    @pytest.mark.parametrize(
        "content_type",
        [
            "application/pdf\n",
            "application/pdf\r\n",
            'application/pdf; name="a\r\nX-Injected: 1"',
            "application/pdf\n; charset=utf-8",
        ],
    )
    def test_line_breaks_are_rejected(self, content_type):
        with pytest.raises(PresignError) as refused:
            check_upload("loan_application.pdf", content_type, 1024)
        assert refused.value.status == 400

    def test_parameters_are_still_accepted(self):
        assert check_upload("notes.txt", "text/plain; charset=utf-8", 10) == "txt"
        assert check_upload("notes.txt", 'text/plain; charset="utf-8"', 10) == "txt"
