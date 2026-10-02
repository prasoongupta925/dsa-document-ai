import io
import os
import tempfile
from typing import Optional
from urllib.parse import urlparse

import boto3
from PIL import Image
from strands import Agent
from strands.models import BedrockModel, CacheConfig

from bedrock_tier import service_tier
from tools import (
    create_image_analyzer_tool,
    create_image_rotator_tool,
    create_script_extractor_tool,
    create_video_analyzer_tool,
)


class VisionReactAgent:
    def __init__(
        self,
        model_id: str,
        region: str = 'us-east-1',
        video_model_id: str = '',
        bucket_owner_account_id: str = ''
    ):
        self.model_id = model_id
        self.video_model_id = video_model_id
        self.bucket_owner_account_id = bucket_owner_account_id
        self.region = region
        self.s3_client = boto3.client('s3', region_name=region)
        self.bedrock_client = boto3.client('bedrock-runtime', region_name=region)
        self.analysis_steps = []
        self.current_image_data = None
        self.previous_context = ''
        self.current_video_uri = ''
        self.current_start_timecode = ''
        self.current_end_timecode = ''

    def _load_prompt(self, prompt_name: str) -> str:
        bucket = os.environ.get('AGENT_STORAGE_BUCKET_NAME', '')
        if not bucket:
            print('AGENT_STORAGE_BUCKET_NAME not set')
            return ''
        s3_key = f'__prompts/analysis/{prompt_name}.txt'
        try:
            resp = self.s3_client.get_object(Bucket=bucket, Key=s3_key)
            return resp['Body'].read().decode('utf-8')
        except Exception as e:
            print(f'Failed to load prompt from S3 ({s3_key}): {e}')
            return ''

    def _detect_image_format(self, image_data: bytes) -> str:
        if image_data[:8] == b'\x89PNG\r\n\x1a\n':
            return 'png'
        if image_data[:2] == b'\xff\xd8':
            return 'jpeg'
        if image_data[:4] == b'GIF8':
            return 'gif'
        if image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
            return 'webp'
        return 'png'

    def _prepare_image_for_agent(self, image_data: bytes, max_size_mb: float = 3.75) -> bytes:
        max_bytes = int(max_size_mb * 1024 * 1024)
        if len(image_data) <= max_bytes:
            return image_data

        try:
            image = Image.open(io.BytesIO(image_data))
            target_ratio = (max_bytes * 0.8 / len(image_data)) ** 0.5
            new_size = (int(image.size[0] * target_ratio), int(image.size[1] * target_ratio))
            resized = image.resize(new_size, Image.LANCZOS)

            buf = io.BytesIO()
            if image.mode in ('RGBA', 'LA'):
                resized.save(buf, format='PNG', optimize=True)
            else:
                if resized.mode == 'RGBA':
                    resized = resized.convert('RGB')
                resized.save(buf, format='JPEG', quality=85, optimize=True)

            print(f'Agent image resized: {image.size} -> {new_size}')
            return buf.getvalue()
        except Exception as e:
            print(f'Agent image resize failed: {e}')
            return image_data

    def _download_image(self, image_uri: str) -> Optional[bytes]:
        if not image_uri:
            return None

        try:
            parsed = urlparse(image_uri)
            bucket = parsed.netloc
            key = parsed.path.lstrip('/')

            print(f'Downloading image from s3://{bucket}/{key}')

            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                self.s3_client.download_file(bucket, key, tmp.name)
                with open(tmp.name, 'rb') as f:
                    image_data = f.read()
                os.unlink(tmp.name)

                size_mb = len(image_data) / (1024 * 1024)
                print(f'Image downloaded: {size_mb:.2f}MB')

                return image_data
        except Exception as e:
            print(f'Error downloading image: {e}')
            return None

    def _get_image_data(self) -> Optional[bytes]:
        return self.current_image_data

    def _set_image_data(self, data: bytes) -> None:
        self.current_image_data = data

    def _get_previous_context(self) -> str:
        return self.previous_context

    def _get_video_uri(self) -> str:
        return self.current_video_uri

    def _get_timecode(self) -> tuple[str, str]:
        return (self.current_start_timecode, self.current_end_timecode)

    def analyze(
        self,
        document_id: str,
        segment_id: str,
        segment_index: int,
        image_uri: Optional[str],
        context: str,
        file_type: str,
        language: str = 'en',
        user_instructions: str = '',
        segment_type: str = 'PAGE',
        video_uri: str = '',
        start_timecode: str = '',
        end_timecode: str = '',
        transcribe_segments: Optional[list] = None
    ) -> dict:
        self.analysis_steps = []
        self.previous_context = context

        is_video = segment_type in ('VIDEO', 'CHAPTER')
        # AUDIO has no page image: read it like text (from the transcript in the context).
        is_text = segment_type in ('TEXT', 'WEB', 'AUDIO')

        if is_video:
            self.current_video_uri = video_uri
            self.current_start_timecode = start_timecode
            self.current_end_timecode = end_timecode
            self.current_image_data = None
            print(f'Video segment: {video_uri}, timecode: {start_timecode} - {end_timecode}')
        elif image_uri:
            self.current_image_data = self._download_image(image_uri)
            self.current_video_uri = ''
        else:
            self.current_image_data = None
            self.current_video_uri = ''

        # Language display names for prompts
        language_names = {
            'ko': 'Korean',
            'en': 'English',
            'ja': 'Japanese',
            'zh': 'Chinese'
        }
        language_name = language_names.get(language, 'English')

        cache_kwargs = (
            {'cache_tools': 'default', 'cache_config': CacheConfig(strategy='auto')}
            if 'anthropic' in self.model_id
            else {}
        )
        tier = service_tier()
        model = BedrockModel(
            model_id=self.model_id,
            region_name=self.region,
            **cache_kwargs,
            **({'service_tier': tier} if tier else {})
        )

        tools = []

        if is_video:
            extract_script = create_script_extractor_tool(
                video_uri_getter=self._get_video_uri,
                timecode_getter=self._get_timecode,
                transcribe_segments=transcribe_segments or [],
                analysis_steps=self.analysis_steps,
                region=self.region,
                bucket_owner_account_id=self.bucket_owner_account_id,
                language=language_name
            )
            analyze_video = create_video_analyzer_tool(
                video_uri_getter=self._get_video_uri,
                analysis_steps=self.analysis_steps,
                model_id=self.video_model_id,
                bedrock_client=self.bedrock_client,
                bucket_owner_account_id=self.bucket_owner_account_id,
                language=language_name
            )
            tools.extend([extract_script, analyze_video])
        elif is_text:
            # Text-only analysis: no tools needed, just analyze from context
            pass
        else:
            analyze_image = create_image_analyzer_tool(
                image_data_getter=self._get_image_data,
                previous_context_getter=self._get_previous_context,
                analysis_steps=self.analysis_steps,
                model_id=self.model_id,
                bedrock_client=self.bedrock_client,
                language=language_name
            )

            rotate_image = create_image_rotator_tool(
                image_data_getter=self._get_image_data,
                image_data_setter=self._set_image_data,
                analysis_steps=self.analysis_steps
            )
            tools.extend([analyze_image, rotate_image])

        if is_video:
            system_prompt = self._load_prompt('video_system_prompt')
        elif is_text:
            system_prompt = self._load_prompt('text_system_prompt')
        else:
            system_prompt = self._load_prompt('system_prompt')

        if not system_prompt:
            if is_video:
                system_prompt = """You are a Video Analysis Agent. Extract structured, searchable information from video content.

Follow this workflow:
1. ASSESS: Determine if the segment has substantive content or is empty/dead air.
2. EXTRACT: Call extract_video_script first for speech, then analyze_video for visual content. Extract ALL content.
3. ANALYZE: Add analysis only if it provides value beyond the raw extraction.

Do NOT report STT corrections or upstream processor fixes. Just output the correct content silently.

{user_instructions}"""
            elif is_text:
                system_prompt = """You are a Text Document Analysis Agent. Extract structured, searchable information from text documents.

Follow this workflow:
1. ASSESS: Determine if the text has substantive content to extract.
2. EXTRACT: Reproduce all text exactly as provided. Do not summarize or paraphrase. Extract every element.
3. ANALYZE: Add analysis only if it provides value beyond the raw extraction.

{user_instructions}"""
            else:
                system_prompt = """You are a Document Analysis Agent. Extract structured, searchable information from document images.

You receive the document image directly. Use your vision as the primary source of truth.

Follow this workflow:
1. ASSESS: Look at the image. Determine if the page has content to extract or is blank/decorative.
2. EXTRACT: Extract ALL content from top to bottom. Compare against upstream processor results and correct errors. Do not skip any section.
3. ANALYZE: Add analysis only if it provides value beyond the raw extraction.

Use the analyze_image tool only when your direct vision cannot read specific content accurately (dense tables, small text, fine details).

{user_instructions}"""

        # Inject user instructions into system prompt
        user_instructions_block = ''
        if user_instructions:
            user_instructions_block = f"""<user_instructions>
{user_instructions}
</user_instructions>"""

        system_prompt = system_prompt.format(user_instructions=user_instructions_block)

        # Add language instruction to system prompt
        system_prompt = f"{system_prompt}\n\nIMPORTANT: You MUST use {language_name} for ALL output including: tool call questions (analyze_image, analyze_video, extract_video_script arguments), analysis text, section headers, and descriptions. The only exception is preserving original document text exactly as written."

        if is_video:
            user_query = self._load_prompt('video_user_query')
            if user_query:
                user_query = user_query.format(
                    segment_index=segment_index + 1,
                    context=context,
                    language=language_name
                )
            else:
                user_query = f"""Analyze video segment (chapter {segment_index + 1}).

Upstream processor results:
{context}

Step 1: ASSESS - Is this a segment with substantive content or dead air?
Step 2: EXTRACT - Call extract_video_script first, then analyze_video for visual content. Extract all speech, actions, text overlays, and events.
Step 3: ANALYZE - Add analysis only if needed.

Output as: ## Video Overview, ## Speech Content, ## Visual Content, ## Key Information

IMPORTANT: Provide all output in {language_name}."""
        elif is_text:
            user_query = self._load_prompt('text_user_query')
            if user_query:
                user_query = user_query.format(
                    segment_index=segment_index + 1,
                    context=context,
                    language=language_name
                )
            else:
                user_query = f"""Analyze text document segment (chunk {segment_index + 1}).

Text content:
{context}

Step 1: ASSESS - Is this a segment with substantive content or empty/boilerplate?
Step 2: EXTRACT - Reproduce all text exactly as provided with original structure and formatting.
Step 3: ANALYZE - Add analysis only if needed.

Output as: ## Original Text, ## Document Overview, ## Key Information

IMPORTANT: Provide all output in {language_name}."""
        else:
            user_query = self._load_prompt('user_query')
            if user_query:
                user_query = user_query.format(
                    segment_index=segment_index + 1,
                    context=context,
                    language=language_name
                )
            else:
                user_query = f"""Analyze document segment (page {segment_index + 1}).

Upstream processor results:
{context}

Step 1: ASSESS - Is this a content page or blank/decorative?
Step 2: EXTRACT - Go through the entire page top to bottom. Extract every text element, table, figure, label, header, footer. Compare against upstream results and correct errors.
Step 3: ANALYZE - Add analysis only if needed.

Output as: ## Original Text, ## Document Overview, ## Key Information, ## Analysis Notes

IMPORTANT: Provide all output in {language_name}."""

        agent = Agent(
            callback_handler=None,  # do not print document content to CloudWatch
            model=model,
            system_prompt=system_prompt,
            tools=tools
        )

        try:
            print(f'Starting analysis for document {document_id}, segment {segment_index}')
            print(f'Segment type: {segment_type}, Video: {is_video}, Text: {is_text}')

            # Build user message: multimodal for PAGE with image
            if self.current_image_data and not is_video and not is_text:
                prepared = self._prepare_image_for_agent(self.current_image_data)
                fmt = self._detect_image_format(prepared)
                user_message = [
                    {'image': {'format': fmt, 'source': {'bytes': prepared}}},
                    {'text': user_query}
                ]
                print(f'Sending multimodal message (image {fmt}, {len(prepared)} bytes)')
            else:
                user_message = user_query

            result = agent(user_message)
            response_text = str(result)

            print(f'Analysis completed. Steps: {len(self.analysis_steps)}')
            print(f'Response length: {len(response_text)} chars')

            return {
                'success': True,
                'response': response_text,
                'analysis_steps': self.analysis_steps,
                'iterations': len(self.analysis_steps)
            }

        except Exception as e:
            error_str = str(e)
            error_type = type(e).__name__
            print(f'Agent execution error ({error_type}): {error_str}')

            # Raise retryable errors so Step Functions can retry
            retryable_keywords = [
                'ThrottlingException',
                'TooManyRequestsException',
                'ServiceUnavailableException',
                'ModelTimeoutException',
                'modelStreamErrorException',
            ]
            if any(kw in error_str or kw in error_type for kw in retryable_keywords):
                raise

            # Raise access errors so they surface clearly
            access_keywords = [
                'AccessDeniedException',
                'UnauthorizedAccess',
                'ValidationException',
            ]
            if any(kw in error_str or kw in error_type for kw in access_keywords):
                raise

            return {
                'success': False,
                'response': f'Analysis failed: {e}',
                'analysis_steps': self.analysis_steps,
                'iterations': len(self.analysis_steps)
            }
