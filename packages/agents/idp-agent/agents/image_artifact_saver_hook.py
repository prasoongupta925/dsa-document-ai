import re

import boto3
from nanoid import generate as nanoid_generate
from strands.hooks.events import AfterToolCallEvent
from strands.hooks.registry import HookProvider, HookRegistry

from config import get_config

_config = get_config()
s3_client = boto3.client("s3", region_name=_config.aws_region)


class ImageArtifactSaverHook(HookProvider):
    """Hook that saves generated images as artifacts after generate_image tool completes."""

    def __init__(self, user_id: str | None = None, project_id: str | None = None):
        self.user_id = user_id
        self.project_id = project_id

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(AfterToolCallEvent, self._save_image_artifact)

    def _save_image_artifact(self, event: AfterToolCallEvent) -> None:
        if event.selected_tool is None:
            return

        tool_name = event.selected_tool.tool_name
        if tool_name != "generate_image":
            return

        if event.exception or not event.result:
            return

        result = event.result
        if result.get("status") != "success":
            return

        if not self.user_id or not self.project_id:
            return

        config = get_config()
        if not config.agent_storage_bucket_name:
            return

        # Extract image bytes from result content
        image_bytes = None
        image_format = "png"
        for content_block in result.get("content", []):
            if "image" in content_block:
                image_data = content_block["image"]
                image_format = image_data.get("format", "png")
                source = image_data.get("source", {})
                image_bytes = source.get("bytes")
                break

        if not image_bytes:
            return

        # Build filename from prompt
        prompt = event.tool_use.get("input", {}).get("prompt", "generated_image")
        filename = self._create_filename(prompt, image_format)

        try:
            content_type = f"image/{image_format}"
            artifact_id = f"art_{nanoid_generate(size=21)}"
            s3_key = f"{self.user_id}/{self.project_id}/artifacts/{artifact_id}/{filename}"

            # Upload to S3
            s3_client.put_object(
                Bucket=config.agent_storage_bucket_name,
                Key=s3_key,
                Body=image_bytes,
                ContentType=content_type,
            )

            # Append artifact info to result content
            result["content"].append({"text": f"\n\n[artifact:{artifact_id}]({filename})"})

        except Exception:
            pass

    @staticmethod
    def _create_filename(prompt: str, fmt: str) -> str:
        words = re.sub(r"[^\w\s]", "", prompt).split()[:5]
        name = "_".join(words) if words else "generated_image"
        return f"{name}.{fmt}"
