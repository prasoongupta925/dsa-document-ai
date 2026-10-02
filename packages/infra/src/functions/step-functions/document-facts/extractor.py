"""Facts extraction: build the model input from segment data and call the facts model.

The model is FACTS_MODEL_ID (models.json `facts`: gpt-oss-120b, in-region in
ap-south-1). Pure module (importable without AWS): boto3 is imported lazily and
every Bedrock client is injectable, so tests pass fakes.

The model input is TEXT built from the pipeline's segment data. The original
file and its file name are never sent to the model.
"""
import json
import os
import re

import yaml

from tool_schema import TOOL_DESCRIPTION, TOOL_NAME, TOOL_SCHEMA

# Machine text of a page, in order of preference (PDF text layer first).
MACHINE_TEXT_FIELDS = ('format_parser', 'paddleocr', 'bda_indexer', 'text_content', 'webcrawler_content')
NON_DOCUMENT_TYPES = {'VIDEO', 'AUDIO', 'CHAPTER'}

# A page with less machine text than this falls back to the vision transcription.
MIN_MACHINE_CHARS = 50

# Output budget of the forced tool call. A 6-month statement lists ~40
# recurring debits (~45 tokens each) plus salary credits, and gpt-oss-120b
# reasons before the tool call (6.2K output tokens on a 2-page statement), so
# 8000 is too small; 16000 stays under the model's 16K output limit. A credit
# report with 30 tradelines (~110 tokens each) and 40 listed enquiries (~30
# each) fits too. FACTS_MAX_OUTPUT_TOKENS overrides it without a code change.
DEFAULT_MAX_OUTPUT_TOKENS = 16000

# Bedrock service tiers a call may request through BEDROCK_SERVICE_TIER (the
# pipeline sets 'flex': half the standard price for background work). Unset or
# any other value sends no tier, i.e. the standard tier.
SERVICE_TIERS = ('default', 'flex', 'priority')

# USD per million tokens (input, output) by model and service tier: Price List
# API, AmazonBedrock offer, ap-south-1, version 20260930230255. A model or tier
# missing here gives cost_usd None (unknown), never a wrong price. The Nova 2
# Lite standard price is the Ask feature's (packages/backend/app/file_check_ask.py);
# test_facts_extractor.py checks that the two stay equal.
PRICES_PER_MILLION_USD = {
    'openai.gpt-oss-120b-1:0': {
        'default': (0.18, 0.71),
        'flex': (0.09, 0.355),
        'priority': (0.315, 1.2425),
    },
    'global.amazon.nova-2-lite-v1:0': {
        'default': (0.35, 2.95),
        'flex': (0.175, 1.475),
        'priority': (0.6125, 5.1625),
    },
}

_PROMPTS = None
_bedrock_client = None


def _as_text(v) -> str:
    if v is None:
        return ''
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def _vision_text(segment: dict) -> str:
    """Content of the first ai_analysis entry (the page transcription/analysis)."""
    for entry in segment.get('ai_analysis') or []:
        if not isinstance(entry, dict) or entry.get('analysis_query') == 'Analysis error':
            continue
        content = _as_text(entry.get('content'))
        if content.strip():
            return content
    return ''


def build_texts(segments, max_chars):
    """Return (model_text, grounding_text, stats).

    - model_text: per page '--- Page N ---' + the page's machine text (or the
      vision transcription when the machine text has < 50 chars), capped at
      max_chars.
    - grounding_text: ALL non-empty machine text fields of every page, never
      ai_analysis (so grounding only trusts characters printed in the document).
    """
    max_chars = int(max_chars)
    model_parts = []
    grounding_parts = []
    used = 0
    stats = {'pages': 0, 'pages_with_machine_text': 0, 'text_chars': 0, 'truncated': False}

    ordered = sorted(segments or [], key=lambda s: s.get('segment_index', 0) or 0)
    for seg in ordered:
        if seg.get('segment_type') in NON_DOCUMENT_TYPES:
            continue
        stats['pages'] += 1
        index = seg.get('segment_index', 0) or 0

        machine_values = [t for t in (_as_text(seg.get(k)) for k in MACHINE_TEXT_FIELDS) if t.strip()]
        grounding_parts.extend(machine_values)
        machine = machine_values[0] if machine_values else ''
        if machine:
            stats['pages_with_machine_text'] += 1

        page_text = machine
        if len(machine.strip()) < MIN_MACHINE_CHARS:
            page_text = _vision_text(seg) or machine
        if not page_text.strip() or stats['truncated']:
            continue

        chunk = f'--- Page {index + 1} ---\n{page_text.strip()}'
        sep = '\n\n' if model_parts else ''
        room = max_chars - used - len(sep)
        if len(chunk) > room:
            if room > 0:
                model_parts.append(sep + chunk[:room])
                used += len(sep) + room
            stats['truncated'] = True
            continue
        model_parts.append(sep + chunk)
        used += len(sep) + len(chunk)

    grounding_text = '\n'.join(grounding_parts)
    stats['text_chars'] = len(grounding_text)
    return ''.join(model_parts), grounding_text, stats


def page_texts(segments) -> list:
    """[(page number, machine text)] of every document page, in page order.

    The text of a page is all its non-empty machine text fields (its share of
    build_texts' grounding text, never ai_analysis); '' for a page without a
    text layer. grounding.field_pages finds the page of each value in it.
    """
    out = []
    for seg in sorted(segments or [], key=lambda s: s.get('segment_index', 0) or 0):
        if seg.get('segment_type') in NON_DOCUMENT_TYPES:
            continue
        texts = [t for t in (_as_text(seg.get(k)) for k in MACHINE_TEXT_FIELDS) if t.strip()]
        out.append(((seg.get('segment_index', 0) or 0) + 1, '\n'.join(texts)))
    return out


def load_prompts() -> dict:
    global _PROMPTS
    if _PROMPTS is None:
        path = os.path.join(os.path.dirname(__file__), 'prompts', 'facts_extraction.yaml')
        with open(path, 'r', encoding='utf-8') as f:
            _PROMPTS = yaml.safe_load(f)
    return _PROMPTS


def build_user_prompt(model_text: str) -> str:
    # {tool_name} first, so a literal '{tool_name}' inside the document text is left untouched.
    template = load_prompts()['user_prompt']
    return template.replace('{tool_name}', TOOL_NAME).replace('{document_text}', model_text)


def max_output_tokens() -> int:
    try:
        value = int(os.environ.get('FACTS_MAX_OUTPUT_TOKENS') or DEFAULT_MAX_OUTPUT_TOKENS)
    except ValueError:
        value = DEFAULT_MAX_OUTPUT_TOKENS
    return value if value > 0 else DEFAULT_MAX_OUTPUT_TOKENS


def service_tier():
    """The service tier to request (BEDROCK_SERVICE_TIER), or None for the standard tier."""
    tier = (os.environ.get('BEDROCK_SERVICE_TIER') or '').strip().lower()
    return tier if tier in SERVICE_TIERS else None


def cost_usd(input_tokens: int, output_tokens: int, model_id=None, tier=None):
    """USD cost of one model call from its token usage (the Ask feature's formula and rounding).

    Priced at the model's rate for the service tier that served the call (None =
    standard); None when the model or tier has no price. No tokens cost 0.0.
    """
    if not input_tokens and not output_tokens:
        return 0.0
    price = PRICES_PER_MILLION_USD.get(model_id or '', {}).get(tier or 'default')
    if price is None:
        return None
    cost = input_tokens * price[0] / 1e6 + output_tokens * price[1] / 1e6
    return round(cost, 8)


def usage_record(model_id=None, input_tokens: int = 0, output_tokens: int = 0, tier=None) -> dict:
    """The facts record's `usage`: {model_id, service_tier, input_tokens, output_tokens, cost_usd}.

    No arguments: no model call was made (model_id and service_tier None, zero
    tokens, zero cost).
    """
    return {
        'model_id': model_id,
        'service_tier': tier,
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'cost_usd': cost_usd(input_tokens, output_tokens, model_id, tier),
    }


class NoStructuredOutputError(ValueError):
    """The model answered without the tool call or a JSON object.

    The call was still billed: `usage` is its usage record.
    """

    def __init__(self, usage: dict):
        super().__init__('model returned no structured output')
        self.usage = usage


def get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        import boto3
        from botocore.config import Config
        _bedrock_client = boto3.client(
            'bedrock-runtime',
            region_name=os.environ.get('AWS_REGION'),
            config=Config(retries={'max_attempts': 4, 'mode': 'adaptive'}, read_timeout=120),
        )
    return _bedrock_client


def _parse_json_text(text: str):
    m = re.search(r'\{.*\}', text or '', re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def _tool_input(block: dict):
    value = (block.get('toolUse') or {}).get('input')
    if isinstance(value, str):
        value = _parse_json_text(value)
    return value if isinstance(value, dict) else None


def call_model(model_text: str, model_id: str, client=None) -> tuple:
    """Forced-tool Converse call, on the BEDROCK_SERVICE_TIER service tier when set.

    Returns (raw fields dict, usage record {model_id, service_tier, input_tokens,
    output_tokens, cost_usd}); the usage dict also carries output_truncated=True
    when the model stopped at maxTokens. Raises NoStructuredOutputError (with the
    usage) when the answer holds no fields. reasoningContent blocks (gpt-oss
    reasons before the tool call) are never read: fields come only from the
    toolUse input or, failing that, a JSON object in a text block.
    """
    client = client or get_bedrock_client()
    tier = service_tier()
    request = {
        'modelId': model_id,
        'system': [{'text': load_prompts()['system_prompt']}],
        'messages': [{'role': 'user', 'content': [{'text': build_user_prompt(model_text)}]}],
        'toolConfig': {
            'tools': [{'toolSpec': {
                'name': TOOL_NAME,
                'description': TOOL_DESCRIPTION,
                'inputSchema': {'json': TOOL_SCHEMA},
            }}],
            'toolChoice': {'tool': {'name': TOOL_NAME}},
        },
        'inferenceConfig': {'maxTokens': max_output_tokens(), 'temperature': 0},
    }
    if tier:
        request['serviceTier'] = {'type': tier}
    resp = client.converse(**request)
    usage = resp.get('usage') or {}
    # The response names the tier that served (and bills) the call.
    served = (resp.get('serviceTier') or {}).get('type') or tier or 'default'
    out = usage_record(
        model_id,
        int(usage.get('inputTokens', 0) or 0),
        int(usage.get('outputTokens', 0) or 0),
        served,
    )
    content = ((resp.get('output') or {}).get('message') or {}).get('content') or []

    fields = None
    for block in content:
        if isinstance(block, dict) and 'toolUse' in block:
            fields = _tool_input(block)
            if fields is not None:
                break
    if fields is None:
        for block in content:
            if isinstance(block, dict) and 'text' in block:
                parsed = _parse_json_text(block['text'])
                if isinstance(parsed, dict):
                    fields = parsed
                    break
    if not isinstance(fields, dict):
        raise NoStructuredOutputError(out)
    if resp.get('stopReason') == 'max_tokens':
        # the tool input parsed but the model stopped at maxTokens: its lists
        # (debits, credits) may be cut short; the handler records it in grounding
        out['output_truncated'] = True
    return fields, out
