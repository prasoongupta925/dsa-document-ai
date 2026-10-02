"""Page Description Generator Lambda

Generates a searchable page description from segment analysis results.
Runs in parallel with entity-extractor and analysis-finalizer in the Distributed Map.
"""
import json
import os

import yaml
from strands import Agent
from strands.models import BedrockModel

from shared.s3_analysis import (
    get_segment_analysis,
    update_segment_analysis,
)

PAGE_DESCRIPTION_MODEL_ID = os.environ.get('PAGE_DESCRIPTION_MODEL_ID', '')
# Bedrock service tier ('flex' for this background step); unset = standard.
SERVICE_TIER = os.environ.get('BEDROCK_SERVICE_TIER', '')
# Output cap: a description is one paragraph (~330 tokens on average); the
# model default may be lower and Gemma 3 12B allows at most 8K.
MAX_OUTPUT_TOKENS = 4096
PROMPTS = None


def get_prompts():
    global PROMPTS
    if PROMPTS is None:
        path = os.path.join(os.path.dirname(__file__), 'prompts', 'page_description.yaml')
        with open(path, 'r') as f:
            PROMPTS = yaml.safe_load(f)
    return PROMPTS


def build_page_content(segment_data: dict) -> str:
    """Build page content string from segment data."""
    parts = []

    bda_indexer = segment_data.get('bda_indexer', '')
    if bda_indexer:
        parts.append(f'[BDA]\n{bda_indexer}')

    format_parser = segment_data.get('format_parser', '')
    if format_parser:
        parts.append(f'[PDF Text]\n{format_parser}')

    webcrawler_content = segment_data.get('webcrawler_content', '')
    if webcrawler_content:
        parts.append(f'[Web]\n{webcrawler_content}')

    ai_analysis = segment_data.get('ai_analysis', [])
    for analysis in ai_analysis:
        query = analysis.get('analysis_query', '')
        content = analysis.get('content', '')
        if content:
            parts.append(f'[AI: {query}]\n{content}')

    return '\n\n'.join(parts)


def generate_page_description(segment_data: dict, page_number: int, language: str) -> str:
    """Generate page description using LLM."""
    if not PAGE_DESCRIPTION_MODEL_ID:
        print('PAGE_DESCRIPTION_MODEL_ID not set, skipping')
        return ''

    page_content = build_page_content(segment_data)
    if not page_content.strip():
        print(f'Page {page_number}: empty content, skipping')
        return ''

    prompts = get_prompts()
    system_text = prompts['page_description_system'].format(language=language)
    user_text = prompts['page_description_user'].format(
        page_number=page_number,
        page_content=page_content
    )

    try:
        region = os.environ.get('AWS_REGION', 'us-east-1')
        model = BedrockModel(
            model_id=PAGE_DESCRIPTION_MODEL_ID,
            region_name=region,
            max_tokens=MAX_OUTPUT_TOKENS,
            **({'service_tier': SERVICE_TIER} if SERVICE_TIER else {}),
        )
        agent = Agent(model=model, system_prompt=system_text, callback_handler=None)
        result = agent(user_text)
        description = str(result).strip()
        print(f'Page {page_number}: generated description ({len(description)} chars)')
        return description
    except Exception as e:
        print(f'Page {page_number}: description generation failed: {e}')
        return ''


def handler(event, _context):
    print(f'Event: {json.dumps(event)}')

    workflow_id = event.get('workflow_id')
    segment_index = event.get('segment_index', 0)
    file_uri = event.get('file_uri', '')
    language = event.get('language', 'en')

    if isinstance(segment_index, dict):
        segment_index = segment_index.get('segment_index', 0)

    segment_data = get_segment_analysis(file_uri, segment_index)
    if not segment_data:
        print(f'Segment not found: {file_uri}, segment {segment_index}')
        return {
            'workflow_id': workflow_id,
            'segment_index': segment_index,
            'status': 'not_found',
        }

    page_number = segment_index + 1
    page_description = generate_page_description(segment_data, page_number, language)

    if page_description:
        update_segment_analysis(file_uri, segment_index, page_description=page_description)

    return {
        'workflow_id': workflow_id,
        'segment_index': segment_index,
        'status': 'completed',
        'page_description_length': len(page_description),
    }
