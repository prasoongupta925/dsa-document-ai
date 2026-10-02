"""Entity extraction logic using LLM with structured output."""
import os
from typing import TypedDict

import yaml
from pydantic import BaseModel, Field
from strands import Agent
from strands.models import BedrockModel

ENTITY_EXTRACTION_MODEL_ID = os.environ.get('ENTITY_EXTRACTION_MODEL_ID', '')
PROMPTS = None


def get_prompts():
    global PROMPTS
    if PROMPTS is None:
        path = os.path.join(os.path.dirname(__file__), 'prompts', 'entity_extraction.yaml')
        with open(path, 'r') as f:
            PROMPTS = yaml.safe_load(f)
    return PROMPTS


class EntityMention(BaseModel):
    segment_index: int = Field(description='Segment index where entity is mentioned')
    qa_index: int = Field(description='QA pair index (0=analysis, 1+=extra)')
    context: str = Field(description='Entity role or domain in one short phrase')


class ExtractedEntity(BaseModel):
    name: str = Field(description='Entity name as it appears in the document')
    mentioned_in: list[EntityMention] = Field(description='List of mentions')


class EntityExtractionResult(BaseModel):
    entities: list[ExtractedEntity] = Field(default_factory=list)


class MentionDict(TypedDict):
    segment_index: int
    qa_index: int
    context: str


class EntityDict(TypedDict):
    name: str
    mentioned_in: list[MentionDict]


def build_segments_text(segment_data: dict, segment_index: int) -> str:
    """Build text input for entity extraction from segment analysis data.

    Args:
        segment_data: Segment analysis data from S3
        segment_index: Index of the segment

    Returns:
        Formatted text containing QA pairs and page description
    """
    parts = []
    for qa_idx, analysis in enumerate(segment_data.get('ai_analysis', [])):
        content = analysis.get('content', '')
        if content:
            parts.append(f'--- Segment {segment_index}, QA {qa_idx} ---\n{content}')

    page_desc = segment_data.get('page_description', '')
    if page_desc:
        parts.append(f'--- Segment {segment_index}, Page Description ---\n{page_desc}')

    return '\n\n'.join(parts)


def invoke_extraction(segments_text: str, language: str) -> list[EntityDict]:
    """Invoke LLM to extract entities from prepared text.

    Args:
        segments_text: Prepared text from build_segments_text
        language: Language code for context field

    Returns:
        List of entity dicts with name, entity_type, anchor, mentioned_in
    """
    if not ENTITY_EXTRACTION_MODEL_ID or not segments_text.strip():
        return []

    prompts = get_prompts()
    system_prompt = prompts['system']
    user_text = prompts['user'].format(
        language=language,
        segments=segments_text,
    )

    region = os.environ.get('AWS_REGION', 'us-east-1')
    bedrock_model = BedrockModel(model_id=ENTITY_EXTRACTION_MODEL_ID, region_name=region)
    agent = Agent(model=bedrock_model, system_prompt=system_prompt)

    try:
        result = agent(user_text, structured_output_model=EntityExtractionResult)
        extraction = result.structured_output
        if not isinstance(extraction, EntityExtractionResult):
            print('Entity extraction: unexpected output type')
            return []

        return extraction.model_dump().get('entities', [])
    except Exception as e:
        print(f'Entity extraction error: {e}')
        return []


def extract_entities(segment_data: dict, segment_index: int, language: str) -> list[EntityDict]:
    """Extract entities from segment analysis data.

    Args:
        segment_data: Segment analysis data from S3
        segment_index: Index of the segment
        language: Language code for context field

    Returns:
        List of entity dicts
    """
    segments_text = build_segments_text(segment_data, segment_index)
    return invoke_extraction(segments_text, language)
