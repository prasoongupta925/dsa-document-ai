"""Built-in agent prompts and the chat prompt: EMI and eligibility figures come from tools.

Run from the repo root:
    python -m pytest -q packages/infra/src/prompts/test_builtin_agents.py

Stdlib + pytest only. Every filecheck___ tool a prompt names must be one the File Check
MCP Lambda offers the chat (its schema.json, which its own tests keep equal to the
handler); File Checker, the AI Assistant (the chat prompt) and the Loan Assistant
send every EMI and eligibility figure to those tools, and never compute one themselves.
"""
import json
import re
from pathlib import Path

import pytest

PROMPTS = Path(__file__).resolve().parent
PACKAGES = PROMPTS.parents[2]
BUILTIN_DIR = PROMPTS / 'builtin_agents'
CHAT_PROMPT = PROMPTS / 'chat' / 'system_prompt.txt'
AGENT_PROMPTS_PY = PACKAGES / 'agents' / 'idp-agent' / 'prompts.py'
SCHEMA = PACKAGES / 'lambda' / 'file-check-mcp' / 'schema.json'

CALCULATORS = ('filecheck___emi_calculator', 'filecheck___foir_eligibility')
EMI_HEADING = '## EMI and loan-eligibility numbers (deterministic: tools calculate)'
TOOL_RE = re.compile(r'filecheck___\w+')


def builtin(agent_id):
    return json.loads((BUILTIN_DIR / f'{agent_id}.json').read_text(encoding='utf-8'))


def section(text, heading):
    """From `heading` to the next heading of its level ('## ' or '### ')."""
    start = text.index(heading)
    return text[start : text.index(f"\n{heading.split(' ')[0]} ", start + 1)]


@pytest.mark.parametrize('path', sorted(BUILTIN_DIR.glob('*.json')), ids=lambda p: p.stem)
def test_each_builtin_file_is_what_the_agent_stack_uploads(path):
    raw = path.read_text(encoding='utf-8')
    agent = json.loads(raw)

    assert re.fullmatch(r'builtin-[a-z0-9-]{1,64}', path.stem)
    assert agent['agent_id'] == path.stem
    assert all(isinstance(agent[k], str) and agent[k].strip() for k in ('name', 'description', 'content'))
    assert len(agent['description']) <= 200  # as the backend's tests/test_agents.py requires
    # Written as json.dumps(indent=2, ensure_ascii=False): readable Hindi and Marathi, small diffs.
    assert raw == json.dumps(agent, indent=2, ensure_ascii=False) + '\n'


def test_every_tool_a_prompt_names_is_offered_to_the_chat():
    offered = {f"filecheck___{tool['name']}" for tool in json.loads(SCHEMA.read_text(encoding='utf-8'))}
    texts = {path.stem: builtin(path.stem)['content'] for path in BUILTIN_DIR.glob('*.json')}
    texts['chat system prompt'] = CHAT_PROMPT.read_text(encoding='utf-8')
    texts['idp-agent fallback prompt'] = AGENT_PROMPTS_PY.read_text(encoding='utf-8')

    named = {name: set(TOOL_RE.findall(text)) for name, text in texts.items()}

    assert {name: tools - offered for name, tools in named.items() if tools - offered} == {}
    assert {'filecheck___loan_eligibility', *CALCULATORS} <= offered


def test_file_checker_sends_emi_and_eligibility_to_the_tools():
    content = builtin('builtin-file-checker')['content']

    for tool in ('filecheck___run_file_check', 'filecheck___loan_eligibility', *CALCULATORS):
        assert f'`{tool}`' in content
    assert '### EMI and eligibility numbers' in content
    assert 'Never do the arithmetic yourself' in content
    assert 'sample policy — replace with your lender grid' in content
    # Rates and amounts are quoted from the tools only, never estimated.
    assert 'Never quote or estimate an interest rate, a loan amount or an EMI that no tool returned' in content
    assert 'Never quote or estimate an interest rate, a loan amount or a timeline.' not in content


def test_varunika_loans_calls_only_the_calculators():
    content = builtin('builtin-loan-assistant')['content']

    assert (
        'The only tools you call are `filecheck___emi_calculator` and `filecheck___foir_eligibility`' in content
    )
    # The tools that read the workspace's documents and saved applicants are named only to forbid them.
    never = next(line for line in content.splitlines() if 'Never call `filecheck___run_file_check`' in line)
    for tool in ('filecheck___run_file_check', 'filecheck___list_checklists', 'filecheck___loan_eligibility'):
        assert content.count(tool) == never.count(tool) == 1, tool


def test_varunika_loans_no_longer_computes_emis_itself():
    """The old prompt said "never do arithmetic", then had the model work the formulas out."""
    content = builtin('builtin-loan-assistant')['content']
    numbers = section(content, '### Numbers')

    assert 'Never do arithmetic yourself: not in your head, and not with the calculator or code interpreter' in numbers
    for formula in ('(1 + i)^n', '(1 + r)^−n', '0.5 × income', 'Round each result', 'Round both'):
        assert formula not in content, formula
    assert 'with the calculator or code interpreter tool,' not in content
    # The site's eligibility method, as tool inputs.
    assert '`foir_pct` 50, `annual_rate_pct` 11 and `tenure_years`' in numbers
    assert '"Calculated at 50% FOIR" and "Assumed ROI 11.00%"' in numbers
    assert '"Indicative figures only. The bank or NBFC decides the final rate, amount and EMI."' in numbers


def test_varunika_loans_facts_name_the_tools():
    content = builtin('builtin-loan-assistant')['content']
    facts = json.loads(content.split('<FACTS', 1)[1].split('>', 1)[1].rsplit('</FACTS>', 1)[0])

    assert facts['emi_calculator']['rule'].startswith('EMI numbers come only from the filecheck___emi_calculator tool')
    assert facts['eligibility_checker']['rule'].startswith(
        'Numbers come only from the filecheck___foir_eligibility tool at 50% FOIR and 11% ROI'
    )


def test_the_ai_assistant_sends_emi_and_eligibility_to_the_tools():
    chat = CHAT_PROMPT.read_text(encoding='utf-8')
    fallback = AGENT_PROMPTS_PY.read_text(encoding='utf-8')

    for text in (chat, fallback):
        emi = section(text, EMI_HEADING)
        for tool in ('filecheck___loan_eligibility', *CALCULATORS):
            assert f'`{tool}`' in emi
        assert 'NEVER do this arithmetic yourself' in emi
        assert 'Exception: EMI and loan-eligibility questions call the EMI and eligibility tools' in text
        assert text.index('## Loan-file checks') < text.index(EMI_HEADING) < text.index('## Response Guidelines')
    # Same section in the deployed prompt and the agent's fallback copy.
    assert section(chat, EMI_HEADING) == section(fallback, EMI_HEADING)
