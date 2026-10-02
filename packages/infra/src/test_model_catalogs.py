"""Model catalog, AWS-sold-only and region guards (stdlib + pytest only).

Run from the repo root:
    python -m pytest -q packages/infra/src/test_model_catalogs.py

No AWS calls: the bda-start module is imported with dummy credentials and its
boto3 clients are created lazily (never in these tests).
"""
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

os.environ.setdefault('AWS_DEFAULT_REGION', 'ap-south-1')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'testing')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'testing')
os.environ.setdefault('AWS_SESSION_TOKEN', 'testing')

INFRA_SRC = Path(__file__).resolve().parent
PACKAGES = INFRA_SRC.parents[1]

CHAT_MODELS_JSON = INFRA_SRC / 'chat-models.json'
MODELS_JSON = INFRA_SRC / 'models.json'
FRONTEND_MODELS_TS = (
    PACKAGES / 'frontend' / 'src' / 'components' / 'ChatPanel' / 'models.ts'
)
BACKEND_CHAT_PY = PACKAGES / 'backend' / 'app' / 'routers' / 'chat.py'
AGENT_PY = PACKAGES / 'agents' / 'idp-agent' / 'agents' / 'idp_agent.py'
BDA_START_DIR = INFRA_SRC / 'functions' / 'preprocessing' / 'bda-start'

EXPECTED_CHAT_IDS = [
    'zai.glm-5',
    'global.amazon.nova-2-lite-v1:0',
    'deepseek.v3.2',
    'moonshotai.kimi-k2.5',
]
# Pipeline models (models.json): AWS-sold, credit-paid Bedrock models only.
AWS_SOLD_PIPELINE_PREFIXES = (
    'amazon.',
    'global.amazon.',
    'apac.amazon.',
    'openai.gpt-oss-',
    'google.gemma-3-',
    'qwen.',
    'moonshotai.',
    'zai.',
)
NOVA_2_LITE = 'global.amazon.nova-2-lite-v1:0'

FORBIDDEN_MODEL_ID = re.compile(
    r'\b(?:global\.|us\.|eu\.|apac\.)?'
    r'(?:(?:anthropic|cohere|twelvelabs|stability|writer|luma)\.[a-z0-9]|openai\.gpt-[5-9])'
)
SCAN_ROOTS = [
    'agents',
    'backend/app',
    'infra/src',
    'lambda',
    'frontend/src',
    'common/constructs/src',
]
SCAN_SKIP_DIRS = {
    'node_modules',
    'lambda-layers',
    '.venv',
    '__pycache__',
    'dist',
    'target',
}
SCAN_EXTENSIONS = {'.py', '.ts', '.tsx', '.json', '.rs', '.txt', '.md', '.yaml'}
# The deny list is the only place blocked provider names may appear.
SCAN_ALLOWLIST = {
    PACKAGES / 'common' / 'constructs' / 'src' / 'constants' / 'bedrock.ts',
}


def _load_json(path: Path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# chat-models.json (source of the CDK-managed SSM catalog)
# ---------------------------------------------------------------------------


def test_chat_models_json_ids_and_order():
    models = _load_json(CHAT_MODELS_JSON)
    assert isinstance(models, list)
    assert [m['value'] for m in models] == EXPECTED_CHAT_IDS


def test_chat_models_json_entries_complete():
    required = {
        'value',
        'label',
        'description',
        'contextWindow',
        'inputPrice',
        'outputPrice',
        'metrics',
        'supportsReasoning',
    }
    for entry in _load_json(CHAT_MODELS_JSON):
        assert required <= set(entry), entry.get('value')
        for key in required - {'metrics', 'supportsReasoning'}:
            assert isinstance(entry[key], str) and entry[key], (entry, key)
        metrics = entry['metrics']
        assert set(metrics) >= {'intelligence', 'speed', 'context', 'cost'}
        for key in ('intelligence', 'speed', 'context', 'cost'):
            assert isinstance(metrics[key], int), (entry['value'], key)
        assert entry['supportsReasoning'] is False, entry['value']


def test_chat_models_json_fits_ssm_standard_parameter():
    # CDK stores JSON.stringify(chatModels); a standard parameter holds 4 KB.
    raw = json.dumps(_load_json(CHAT_MODELS_JSON), separators=(',', ':'))
    assert len(raw.encode('utf-8')) < 4096


# ---------------------------------------------------------------------------
# models.json (pipeline models): AWS-sold models only
# ---------------------------------------------------------------------------


def test_pipeline_models_are_aws_sold():
    models = _load_json(MODELS_JSON)
    assert isinstance(models, dict) and models
    for key, value in models.items():
        assert isinstance(value, str), key
        assert value.startswith(AWS_SOLD_PIPELINE_PREFIXES), (key, value)
        assert not FORBIDDEN_MODEL_ID.search(value), (key, value)


def test_pipeline_model_choices():
    models = _load_json(MODELS_JSON)
    # Page images and video need a vision/video model: Nova 2 Lite.
    for key in ('analysis', 'videoAnalysis', 'scriptExtractor'):
        assert models[key] == NOVA_2_LITE, key
    # Text-only background steps: in-region (ap-south-1) open-weight models.
    assert models['facts'] == 'openai.gpt-oss-120b-1:0'
    assert models['describer'] == models['docSummarizer'] == 'google.gemma-3-12b-it'


@pytest.mark.parametrize(
    'model_id',
    [
        'openai.gpt-oss-120b-1:0',
        'google.gemma-3-12b-it',
        'qwen.qwen3-235b-a22b-2507-v1:0',
        'moonshotai.kimi-k2.5',
        'zai.glm-5',
        NOVA_2_LITE,
    ],
)
def test_aws_sold_models_pass_both_guards(model_id):
    assert model_id.startswith(AWS_SOLD_PIPELINE_PREFIXES)
    assert not FORBIDDEN_MODEL_ID.search(model_id)


@pytest.mark.parametrize(
    'provider, model',
    [
        ('anthropic', 'claude-x-v1:0'),
        ('cohere', 'embed-v4:0'),
        ('openai', 'gpt-5.4'),
        ('openai', 'gpt-6-x'),
        ('writer', 'palmyra-x5-v1:0'),
    ],
)
def test_marketplace_models_fail_both_guards(provider, model):
    # Built at run time so the repo scan below does not flag this file.
    model_id = f'{provider}.{model}'
    assert not model_id.startswith(AWS_SOLD_PIPELINE_PREFIXES)
    assert FORBIDDEN_MODEL_ID.search(model_id)


# ---------------------------------------------------------------------------
# Repo scan: no non-AWS-sold model IDs anywhere
# ---------------------------------------------------------------------------


def _iter_scanned_files():
    for root in SCAN_ROOTS:
        base = PACKAGES / root
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            parts = Path(dirpath).parts
            # Skip vendored office-skill helper scripts (.skills/<name>/scripts).
            if '.skills' in parts:
                idx = parts.index('.skills')
                if len(parts) > idx + 2 and parts[idx + 2] == 'scripts':
                    dirnames[:] = []
                    continue
            dirnames[:] = [d for d in dirnames if d not in SCAN_SKIP_DIRS]
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix in SCAN_EXTENSIONS and path not in SCAN_ALLOWLIST:
                    yield path


def test_repo_has_no_non_aws_sold_model_ids():
    hits = []
    scanned = 0
    for path in _iter_scanned_files():
        scanned += 1
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for match in FORBIDDEN_MODEL_ID.finditer(text):
            line_no = text.count('\n', 0, match.start()) + 1
            hits.append(f'{path.relative_to(PACKAGES)}:{line_no}: {match.group(0)}')
    assert scanned > 100, 'repo scan found too few files; wrong root?'
    assert not hits, 'non-AWS-sold model IDs found:\n' + '\n'.join(hits)


def test_deny_list_covers_blocked_providers():
    text = next(iter(SCAN_ALLOWLIST)).read_text(encoding='utf-8')
    for provider in ('anthropic', 'cohere', 'twelvelabs', 'stability'):
        assert f"'{provider}'" in text, provider
    assert 'foundation-model/${p}.*' in text
    assert 'inference-profile/*${p}.*' in text


# ---------------------------------------------------------------------------
# Parity: built-in fallbacks == chat-models.json
# ---------------------------------------------------------------------------


def test_frontend_fallback_matches_chat_models_json():
    text = FRONTEND_MODELS_TS.read_text(encoding='utf-8')
    ids = re.findall(r"^\s*value:\s*'([^']+)'", text, re.MULTILINE)
    assert ids == [m['value'] for m in _load_json(CHAT_MODELS_JSON)]


def test_backend_fallback_matches_chat_models_json():
    text = BACKEND_CHAT_PY.read_text(encoding='utf-8')
    ids = re.findall(r'^\s*"value":\s*"([^"]+)"', text, re.MULTILINE)
    assert ids == [m['value'] for m in _load_json(CHAT_MODELS_JSON)]


def test_agent_fallback_matches_chat_models_json():
    text = AGENT_PY.read_text(encoding='utf-8')
    block = re.search(
        r'_DEFAULT_MODEL_CATALOG: dict\[str, bool\] = \{(.*?)\n\}', text, re.DOTALL
    )
    assert block, '_DEFAULT_MODEL_CATALOG not found'
    entries = re.findall(r'"([^"]+)":\s*(True|False)', block.group(1))
    assert [k for k, _ in entries] == [
        m['value'] for m in _load_json(CHAT_MODELS_JSON)
    ]
    assert all(v == 'False' for _, v in entries)


# ---------------------------------------------------------------------------
# bda-start: BDA profile geo prefix from the deploy region
# ---------------------------------------------------------------------------


@pytest.fixture(scope='module')
def bda_start():
    source = (BDA_START_DIR / 'index.py').read_text(encoding='utf-8')
    # The module must not create boto3 clients at import time.
    module_level = [
        line
        for line in source.splitlines()
        if line and not line[0].isspace() and 'boto3.' in line
    ]
    assert not module_level, module_level

    for extra in (str(INFRA_SRC / 'functions'), str(BDA_START_DIR)):
        if extra not in sys.path:
            sys.path.insert(0, extra)
    spec = importlib.util.spec_from_file_location(
        'bda_start_index_under_test', BDA_START_DIR / 'index.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.bda_client is None and module.bda_runtime_client is None
    return module


@pytest.mark.parametrize(
    'region, expected',
    [
        ('ap-south-1', 'apac.data-automation-v1'),
        ('ap-northeast-1', 'apac.data-automation-v1'),
        ('us-east-1', 'us.data-automation-v1'),
        ('us-west-2', 'us.data-automation-v1'),
        ('eu-west-1', 'eu.data-automation-v1'),
        ('ca-central-1', 'us.data-automation-v1'),
        ('', 'us.data-automation-v1'),
    ],
)
def test_bda_profile_id_from_region(bda_start, monkeypatch, region, expected):
    monkeypatch.delenv('BDA_PROFILE_ID', raising=False)
    assert bda_start._bda_profile_id(region) == expected


def test_bda_profile_id_env_override_wins(bda_start, monkeypatch):
    monkeypatch.setenv('BDA_PROFILE_ID', 'custom.data-automation-v1')
    assert bda_start._bda_profile_id('ap-south-1') == 'custom.data-automation-v1'
    assert bda_start._bda_profile_id('us-east-1') == 'custom.data-automation-v1'


# ---------------------------------------------------------------------------
# Agent prompt / skill wiring for the deterministic file check
# ---------------------------------------------------------------------------


def test_system_prompts_route_file_checks_to_the_tool():
    txt = (INFRA_SRC / 'prompts' / 'chat' / 'system_prompt.txt').read_text(
        encoding='utf-8'
    )
    py = (PACKAGES / 'agents' / 'idp-agent' / 'prompts.py').read_text(
        encoding='utf-8'
    )
    heading = '## Loan-file checks (deterministic: AI reads, rules decide)'
    for text in (txt, py):
        assert heading in text
        assert 'filecheck___run_file_check' in text
        assert 'missing_items' in text
        assert 'Exception: loan-file readiness/completeness/consistency' in text
        # The section sits after "Structured vs. Unstructured Data".
        assert text.index('## Structured vs. Unstructured Data') < text.index(
            heading
        )
    # Same section text in both copies.
    def section(text):
        start = text.index(heading)
        return text[start : text.index('\n## ', start + 1)]

    assert section(txt) == section(py)


def test_file_check_skill_front_matter():
    skill = (
        PACKAGES / 'agents' / 'idp-agent' / '.skills' / 'file-check' / 'SKILL.md'
    ).read_text(encoding='utf-8')
    assert skill.startswith('---\nname: file-check\n')
    assert 'filecheck___run_file_check' in skill
    assert 'filecheck___list_checklists' in skill
    assert 'AI reads, rules decide, you explain.' in skill
