from unittest.mock import MagicMock

from app.markdown import transform_markdown_images


class TestTransformMarkdownImages:
    def test_empty_markdown(self):
        mock_presigned = MagicMock()
        assert transform_markdown_images("", presign=mock_presigned) == ""
        assert transform_markdown_images(None, presign=mock_presigned) is None
        mock_presigned.assert_not_called()

    def test_no_images(self):
        mock_presigned = MagicMock()
        markdown = "This is plain text without images."
        assert transform_markdown_images(markdown, presign=mock_presigned) == markdown
        mock_presigned.assert_not_called()

    def test_relative_path_with_assets_base(self):
        mock_presigned = MagicMock()
        mock_presigned.return_value = "https://presigned.url/image.png"

        markdown = "![alt](./image.png)"
        image_uri = "s3://bucket/path/assets/base.png"

        result = transform_markdown_images(markdown, image_uri, presign=mock_presigned)

        assert "https://presigned.url/image.png" in result
        mock_presigned.assert_called_with("s3://bucket/path/assets/image.png")

    def test_plain_filename(self):
        mock_presigned = MagicMock()
        mock_presigned.return_value = "https://presigned.url/image.png"

        markdown = "![alt](image.png)"
        image_uri = "s3://bucket/path/assets/base.png"

        result = transform_markdown_images(markdown, image_uri, presign=mock_presigned)

        assert "https://presigned.url/image.png" in result

    def test_s3_uri_with_assets(self):
        mock_presigned = MagicMock()
        mock_presigned.return_value = "https://presigned.url/image.png"

        markdown = "![alt](s3://bucket/path/assets/image.png)"

        result = transform_markdown_images(markdown, presign=mock_presigned)

        assert "https://presigned.url/image.png" in result
        mock_presigned.assert_called_with("s3://bucket/path/assets/image.png")

    def test_s3_uri_without_assets_adds_assets(self):
        mock_presigned = MagicMock()
        mock_presigned.return_value = "https://presigned.url/image.png"

        markdown = "![alt](s3://bucket/path/image.png)"

        result = transform_markdown_images(markdown, presign=mock_presigned)

        assert "https://presigned.url/image.png" in result
        # First call should try with /assets/
        mock_presigned.assert_any_call("s3://bucket/path/assets/image.png")

    def test_http_url_unchanged(self):
        mock_presigned = MagicMock()
        markdown = "![alt](https://example.com/image.png)"
        result = transform_markdown_images(markdown, presign=mock_presigned)
        assert "https://example.com/image.png" in result
        mock_presigned.assert_not_called()

    def test_alt_text_cleanup(self):
        mock_presigned = MagicMock()
        mock_presigned.return_value = "https://presigned.url/image.png"

        # Alt text with newlines and brackets
        markdown = "![text\nwith\nnewlines [and] brackets](./image.png)"
        image_uri = "s3://bucket/assets/base.png"

        result = transform_markdown_images(markdown, image_uri, presign=mock_presigned)

        # Should have cleaned alt text
        assert "text with newlines" in result
        assert "\\[and\\]" in result

    def test_long_alt_text_truncated(self):
        mock_presigned = MagicMock()
        mock_presigned.return_value = "https://presigned.url/image.png"

        long_alt = "a" * 150
        markdown = f"![{long_alt}](./image.png)"
        image_uri = "s3://bucket/assets/base.png"

        result = transform_markdown_images(markdown, image_uri, presign=mock_presigned)

        # Should be truncated to 100 chars + "..."
        assert "..." in result

    def test_multiple_images(self):
        mock_presigned = MagicMock()
        mock_presigned.return_value = "https://presigned.url/image.png"

        markdown = "![first](./a.png) some text ![second](./b.png)"
        image_uri = "s3://bucket/assets/base.png"

        result = transform_markdown_images(markdown, image_uri, presign=mock_presigned)

        assert result.count("https://presigned.url/image.png") == 2

    def test_image_uri_without_assets_constructs_base(self):
        mock_presigned = MagicMock()
        mock_presigned.return_value = "https://presigned.url/image.png"

        markdown = "![alt](./image.png)"
        image_uri = "s3://bucket/path/to/base.png"  # No /assets/

        transform_markdown_images(markdown, image_uri, presign=mock_presigned)

        # Should construct assets base from parent dir
        mock_presigned.assert_called_with("s3://bucket/path/to/assets/image.png")

    def test_presigned_url_failure_keeps_original(self):
        mock_presigned = MagicMock()
        mock_presigned.return_value = None

        markdown = "![alt](./image.png)"
        image_uri = "s3://bucket/assets/base.png"

        result = transform_markdown_images(markdown, image_uri, presign=mock_presigned)

        # Should keep original path when presigned fails
        assert "./image.png" in result
