"""Entity normalization logic using LLM with structured output."""
import os
from collections import defaultdict
from typing import TypedDict

import yaml
from pydantic import BaseModel, Field
from strands import Agent
from strands.models import BedrockModel


class MentionDict(TypedDict):
    segment_index: int
    qa_index: int
    context: str


class Entity(TypedDict):
    name: str
    mentioned_in: list[MentionDict]

ENTITY_NORMALIZATION_MODEL_ID = os.environ.get('ENTITY_NORMALIZATION_MODEL_ID', '')
PROMPTS = None


def get_normalization_prompts():
    global PROMPTS
    if PROMPTS is None:
        path = os.path.join(
            os.path.dirname(__file__), 'prompts', 'entity_normalization.yaml'
        )
        with open(path, 'r') as f:
            PROMPTS = yaml.safe_load(f)
    return PROMPTS


class CoreEntity(BaseModel):
    """A core entity that groups related concepts."""
    name: str = Field(description='Broadest, most reusable name for this core entity')
    members: list[str] = Field(description='Entity names that belong to this core entity')


class NormalizationResult(BaseModel):
    """Result of core entity building."""
    core_entities: list[CoreEntity] = Field(
        default_factory=list,
        description='Core entities with their member entities. One member can appear in multiple core entities.',
    )


def deduplicate_entities(all_entities: list[Entity]) -> list[Entity]:
    """Deduplicate entities by name (case-insensitive), merging mentioned_in."""
    names: dict[str, str] = {}
    mentions: dict[str, list[MentionDict]] = defaultdict(list)
    for ent in all_entities:
        key = ent['name'].lower().strip()
        names.setdefault(key, ent['name'])
        mentions[key].extend(ent.get('mentioned_in', []))
    return [{'name': names[k], 'mentioned_in': mentions[k]} for k in mentions]


def normalize_entities(entities: list[Entity], existing_keywords: list[str] | None = None) -> list[Entity]:
    """Build core entities from deduplicated entities using LLM.

    Groups related entities into core entities and merges their mentioned_in.
    One entity can belong to multiple core entities.
    Existing keywords from other documents are provided for cross-document matching.
    Returns core entities only (members are absorbed).
    """
    if not ENTITY_NORMALIZATION_MODEL_ID or len(entities) < 2:
        return entities

    # Build entity list with contexts for LLM
    entity_entries = []
    for ent in entities:
        contexts = []
        for mention in ent.get('mentioned_in', []):
            ctx = mention.get('context', '')
            if ctx and ctx not in contexts:
                contexts.append(ctx)
        entry = f'- {ent["name"]}'
        if contexts:
            entry += f' (context: {contexts[0]})'
        entity_entries.append(entry)

    entities_text = '\n'.join(entity_entries)

    # Add existing keywords for cross-document matching
    existing_text = ''
    if existing_keywords:
        existing_text = '\n'.join(f'- {kw}' for kw in existing_keywords)

    prompts = get_normalization_prompts()
    system_prompt = prompts['system']
    user_text = prompts['user'].format(
        entities=entities_text,
        existing_keywords=existing_text or 'None',
    )

    region = os.environ.get('AWS_REGION', 'us-east-1')
    bedrock_model = BedrockModel(model_id=ENTITY_NORMALIZATION_MODEL_ID, region_name=region)
    agent = Agent(model=bedrock_model, system_prompt=system_prompt)

    try:
        result = agent(user_text, structured_output_model=NormalizationResult)
        # Log token usage
        try:
            usage = result.metrics.accumulated_usage
            input_tokens = usage.get('inputTokens', 0)
            output_tokens = usage.get('outputTokens', 0)
            print(f'Entity normalization tokens - input: {input_tokens}, output: {output_tokens}, total: {input_tokens + output_tokens}')
        except Exception as te:
            print(f'Entity normalization: could not read token usage: {te}')

        normalization = result.structured_output
        if not isinstance(normalization, NormalizationResult):
            print('Entity normalization: unexpected output type, skipping')
            return entities

        # Log core entity groupings
        for core in normalization.core_entities:
            print(f'  Core: {core.name} -> {core.members}')

        if not normalization.core_entities:
            return entities

        # Build name -> entity lookup
        entity_by_name: dict[str, Entity] = {
            ent['name'].lower().strip(): ent for ent in entities
        }

        # Create core entities by merging members' mentioned_in
        core_results: list[Entity] = []
        for core in normalization.core_entities:
            merged_mentions: list[MentionDict] = []
            for member_name in core.members:
                member = entity_by_name.get(member_name.lower().strip())
                if member:
                    merged_mentions.extend(member.get('mentioned_in', []))
            if merged_mentions:
                core_results.append({
                    'name': core.name,
                    'mentioned_in': merged_mentions,
                })

        print(f'Entity normalization: {len(entities)} entities -> {len(core_results)} core entities')
        return core_results

    except Exception as e:
        print(f'Entity normalization failed, using deduplicated entities: {e}')
        return entities
