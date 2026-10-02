"""Unit tests for extractor.py and tool_schema.py (synthetic data, fake Bedrock client).

Run: python -m pytest -q   (from this folder)
"""
import os
import sys

os.environ['BACKEND_TABLE_NAME'] = 'test-table'
os.environ['AWS_DEFAULT_REGION'] = 'ap-south-1'
os.environ['AWS_REGION'] = 'ap-south-1'
os.environ['AWS_ACCESS_KEY_ID'] = 'testing'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'testing'

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', '..')))
sys.path.insert(0, HERE)

import pytest  # noqa: E402

import extractor  # noqa: E402
from extractor import (  # noqa: E402
    PRICES_PER_MILLION_USD, NoStructuredOutputError, build_texts, build_user_prompt, call_model, cost_usd,
    load_prompts, page_texts, service_tier, usage_record)
from tool_schema import (  # noqa: E402
    DEBIT_CATEGORIES, DEBIT_CHANNELS, DOC_TYPE_FIELDS, DOC_TYPES, EMPLOYMENT_TYPES, ENQUIRY_WINDOWS, FIELD_NAMES,
    LIST_FIELDS, NUMERIC_FIELDS, TOOL_NAME, TOOL_SCHEMA, TRADELINE_AMOUNT_FIELDS)

PLAN_B_SYSTEM_PROMPT = (
    'You extract fields from Indian loan-file documents for a DSA (loan agent) file check. '
    'Classify the document and fill the tool fields using ONLY what is printed in the document. '
    'Copy PAN, names and employer names character-for-character (do not correct typos). '
    'Numbers must be plain numbers without commas or currency symbols (e.g. 82500). '
    'Omit any field that is not present on this document. Never guess.'
)
OBLIGATIONS_PROMPT = (
    ' Bank statements: list every salary credit and every obligation debit (loan EMI, rent, '
    'SIP / mutual fund / RD, insurance premium, utility or phone bill, credit-card payment) as its '
    "own entry with that row's date and withdrawal amount, in every month of the statement; skip "
    'everyday spending. Loan applications: copy each existing loan / EMI from the existing-obligations '
    'section; a credit card paid monthly is not an EMI.'
)
CIBIL_PAGE_PROMPT = (
    ' Loan applications and identity details: also copy the mobile number, date of birth, current and '
    'permanent address with their pincodes, house ownership, employment type and company (the employer). '
    'Salary slips: bonus and incentive are the amounts paid in that month. Form-16 / ITR: other and rental '
    "income are the year's income from other sources and from house property. Rent agreements: the applicant "
    "is the landlord; applicant_name and PAN are the landlord's. Pension slips: monthly_pension is the net "
    'pension of the month. Credit reports: copy the bureau, report date and score; give an enquiry count only '
    'when the report prints the count for that exact window, and list every enquiry with its date; one '
    'tradelines entry per account, with only the last 4 characters of its account number.'
)

PDF_TEXT = ('Salary Slip for the month of August 2026 - Konkan Softworks Pvt Ltd - '
            'Employee Rahul Vijay Deshmukh PAN BQXPD4821K Gross 95,000 Net 82,500')
OCR_TEXT = 'OCR READING OF THE SAME PAGE: Salary S1ip August 2026 Konkan Softw0rks Gross 95,000'
VISION_TEXT = 'VISION TRANSCRIPTION: identity sheet of Sneha Anil Kulkarni, PAN CKRPK7314M'

FACTS_MODEL = 'openai.gpt-oss-120b-1:0'
NOVA_2_LITE = 'global.amazon.nova-2-lite-v1:0'
MODELS_JSON = os.path.abspath(os.path.join(HERE, '..', '..', '..', 'models.json'))


@pytest.fixture(autouse=True)
def _standard_tier(monkeypatch):
    # Tests opt in to a service tier explicitly.
    monkeypatch.delenv('BEDROCK_SERVICE_TIER', raising=False)


def _page(index, **kw):
    seg = {'segment_index': index, 'segment_type': 'PAGE'}
    seg.update(kw)
    return seg


# --------------------------------------------------------------------------- #
# tool schema
# --------------------------------------------------------------------------- #
def _types(node):
    if isinstance(node, dict):
        if 'type' in node:
            yield node['type']
        for v in node.values():
            yield from _types(v)
    elif isinstance(node, list):
        for v in node:
            yield from _types(v)


def test_tool_schema_shape():
    assert DOC_TYPES == ['loan_application', 'identity_details', 'salary_slip', 'bank_statement', 'form16_itr',
                         'credit_report', 'rent_agreement', 'pension_slip', 'other']
    assert TOOL_NAME == 'record_loan_document'
    assert TOOL_SCHEMA['required'] == ['doc_type']
    assert TOOL_SCHEMA['properties']['doc_type']['enum'] == DOC_TYPES
    assert TOOL_SCHEMA['properties']['financial_year']['type'] == 'string'
    assert NUMERIC_FIELDS == ('gross_salary', 'net_salary', 'declared_net_salary',
                              'declared_total_existing_emi', 'loan_amount', 'loan_tenure_months',
                              'bonus', 'incentive', 'other_income_annual', 'rental_income_annual',
                              'monthly_rent', 'monthly_pension', 'credit_score')
    assert LIST_FIELDS == ('salary_credits', 'recurring_debits', 'declared_existing_emis', 'enquiries', 'tradelines')
    assert FIELD_NAMES == [
        'doc_type', 'applicant_name', 'pan', 'masked_aadhaar_last4', 'employer', 'month',
        'gross_salary', 'net_salary', 'statement_from', 'statement_to', 'salary_credits',
        'recurring_debits', 'declared_net_salary', 'declared_existing_emis',
        'declared_total_existing_emi', 'loan_amount', 'loan_tenure_months', 'product', 'financial_year',
        # CIBIL page fields, after the file-check fields
        'mobile', 'dob', 'current_address', 'current_pincode', 'permanent_address', 'permanent_pincode',
        'house_ownership', 'employment_type', 'company', 'bonus', 'incentive', 'other_income_annual',
        'rental_income_annual', 'landlord_name', 'tenant_name', 'monthly_rent', 'registration',
        'agreement_from', 'agreement_to', 'monthly_pension', 'bureau', 'report_date', 'credit_score',
        'enquiries_30d', 'enquiries_60d', 'enquiries_90d', 'enquiries_120d', 'enquiries', 'tradelines']
    for t in _types(TOOL_SCHEMA):
        assert isinstance(t, str), f'union type found: {t}'
        assert t != 'null'


def test_cibil_page_fields_in_tool_schema():
    props = TOOL_SCHEMA['properties']
    # the eligibility page's employment-type ids (packages/backend/app/eligibility.py)
    assert props['employment_type']['enum'] == EMPLOYMENT_TYPES == [
        'defence', 'government', 'grade_4', 'llp', 'merchant_navy', 'partnership_proprietorship',
        'private_limited', 'public_limited']
    assert props['house_ownership']['enum'] == ['owned', 'rented', 'parental', 'company_provided']
    assert props['registration']['enum'] == ['notarised', 'registered']
    assert props['bureau']['enum'] == ['CIBIL', 'Experian', 'Equifax', 'CRIF']
    assert list(ENQUIRY_WINDOWS) == ['enquiries_30d', 'enquiries_60d', 'enquiries_90d', 'enquiries_120d']
    assert all(props[k]['type'] == 'number' for k in ENQUIRY_WINDOWS)
    for k in ('mobile', 'current_pincode', 'permanent_pincode'):
        assert props[k]['type'] == 'string'  # digits as printed, never a number
    tradeline = props['tradelines']['items']
    assert tradeline['required'] == ['loan_type', 'lender']
    assert list(tradeline['properties']) == [
        'loan_type', 'lender', 'sanction_amount', 'outstanding', 'emi', 'status', 'account_last4', 'overdue',
        'emis_paid', 'emis_pending', 'open_date', 'last_payment_date']
    assert tradeline['properties']['loan_type']['enum'] == [
        'personal_loan', 'home_loan', 'mortgage_loan', 'car_loan', 'education_loan', 'application_loan',
        'consumer_loan', 'credit_card', 'other']
    assert tradeline['properties']['status']['enum'] == ['active', 'closed', 'written_off', 'settled', 'other']
    assert all(tradeline['properties'][k]['type'] == 'number' for k in TRADELINE_AMOUNT_FIELDS)
    assert 'account_number' not in tradeline['properties']  # the model is asked for the last 4 only
    enquiry = props['enquiries']['items']
    assert set(enquiry['properties']) == {'date', 'lender', 'purpose'} and enquiry['required'] == ['date']


def test_every_cibil_page_field_belongs_to_document_types():
    new_fields = FIELD_NAMES[FIELD_NAMES.index('mobile'):]
    assert set(DOC_TYPE_FIELDS) == set(new_fields)
    assert all(set(types) <= set(DOC_TYPES) - {'other'} for types in DOC_TYPE_FIELDS.values())
    assert DOC_TYPE_FIELDS['mobile'] == ('loan_application', 'identity_details')
    assert DOC_TYPE_FIELDS['tradelines'] == ('credit_report',)
    assert DOC_TYPE_FIELDS['monthly_rent'] == ('rent_agreement',)


def test_obligation_fields_in_tool_schema():
    props = TOOL_SCHEMA['properties']
    debit = props['recurring_debits']['items']
    assert debit['required'] == ['date', 'amount', 'narration', 'category']
    assert debit['properties']['channel']['enum'] == DEBIT_CHANNELS == ['ACH', 'NACH', 'ECS', 'SI', 'UPI', 'other']
    assert debit['properties']['category']['enum'] == DEBIT_CATEGORIES == [
        'loan_emi', 'rent', 'investment', 'utility', 'credit_card', 'insurance', 'other']
    emi = props['declared_existing_emis']['items']
    assert set(emi['properties']) == {'lender', 'loan_type', 'amount'}
    assert emi['required'] == ['amount']
    assert props['declared_total_existing_emi']['type'] == 'number'
    assert props['loan_tenure_months']['type'] == 'number'


# --------------------------------------------------------------------------- #
# build_texts
# --------------------------------------------------------------------------- #
def test_prefers_format_parser_over_paddleocr():
    model_text, grounding_text, stats = build_texts(
        [_page(0, format_parser=PDF_TEXT, paddleocr=OCR_TEXT)], 60000)
    assert model_text == f'--- Page 1 ---\n{PDF_TEXT}'
    assert OCR_TEXT not in model_text
    assert PDF_TEXT in grounding_text and OCR_TEXT in grounding_text
    assert stats == {'pages': 1, 'pages_with_machine_text': 1,
                     'text_chars': len(grounding_text), 'truncated': False}


def test_uses_first_non_empty_machine_field_in_order():
    model_text, _, _ = build_texts([_page(0, format_parser='', paddleocr=OCR_TEXT, bda_indexer='B' * 80)], 60000)
    assert OCR_TEXT in model_text
    assert 'B' * 80 not in model_text


def test_falls_back_to_vision_when_machine_text_is_short():
    seg = _page(0, format_parser='  short  ', ai_analysis=[
        {'analysis_query': 'Page 1 Analysis', 'content': VISION_TEXT},
        {'analysis_query': 'Q2', 'content': 'second answer'}])
    model_text, grounding_text, stats = build_texts([seg], 60000)
    assert model_text == f'--- Page 1 ---\n{VISION_TEXT}'
    assert 'second answer' not in model_text
    assert grounding_text == '  short  '
    assert stats['pages_with_machine_text'] == 1


def test_grounding_text_never_contains_ai_analysis():
    segs = [
        _page(0, format_parser=PDF_TEXT, ai_analysis=[{'analysis_query': 'a', 'content': 'AI-ONLY-ONE'}]),
        _page(1, ai_analysis=[{'analysis_query': 'a', 'content': 'AI-ONLY-TWO ' + VISION_TEXT}]),
    ]
    model_text, grounding_text, stats = build_texts(segs, 60000)
    assert 'AI-ONLY' not in grounding_text
    assert 'AI-ONLY-TWO' in model_text  # page 2 has no machine text -> vision text is model input
    assert stats['pages'] == 2 and stats['pages_with_machine_text'] == 1


def test_analysis_error_entries_are_not_used():
    seg = _page(0, ai_analysis=[{'analysis_query': 'Analysis error', 'content': 'Analysis failed: boom'}])
    model_text, _, stats = build_texts([seg], 60000)
    assert model_text == ''
    assert stats['pages'] == 1


def test_pages_sorted_and_media_segments_skipped():
    segs = [
        _page(2, format_parser='third page ' * 6),
        {'segment_index': 0, 'segment_type': 'VIDEO', 'text_content': 'VIDEO TEXT ' * 10},
        _page(1, format_parser='second page ' * 6),
        {'segment_index': 3, 'segment_type': 'CHAPTER', 'ai_analysis': [{'content': 'chapter'}]},
        {'segment_index': 4, 'segment_type': 'AUDIO', 'paddleocr': 'AUDIO TEXT ' * 10},
    ]
    model_text, grounding_text, stats = build_texts(segs, 60000)
    assert model_text.index('--- Page 2 ---') < model_text.index('--- Page 3 ---')
    assert 'VIDEO' not in model_text + grounding_text
    assert 'AUDIO' not in model_text + grounding_text
    assert stats['pages'] == 2


def test_only_media_segments_give_empty_texts():
    model_text, grounding_text, stats = build_texts(
        [{'segment_index': 0, 'segment_type': 'VIDEO', 'ai_analysis': [{'content': 'scene'}]}], 60000)
    assert (model_text, grounding_text) == ('', '')
    assert stats['pages'] == 0


def test_truncation_sets_flag_and_caps_length():
    segs = [_page(i, format_parser=f'page {i} ' + 'x' * 400) for i in range(10)]
    model_text, grounding_text, stats = build_texts(segs, 1000)
    assert stats['truncated'] is True
    assert len(model_text) <= 1000
    assert '--- Page 1 ---' in model_text
    assert '--- Page 10 ---' not in model_text
    # grounding still covers every page
    assert all(f'page {i} ' in grounding_text for i in range(10))


def test_single_huge_page_is_truncated_not_dropped():
    model_text, _, stats = build_texts([_page(0, text_content='y' * 5000)], 1000)
    assert len(model_text) == 1000
    assert model_text.startswith('--- Page 1 ---\n')
    assert stats['truncated'] is True


def test_no_truncation_when_it_fits():
    _, _, stats = build_texts([_page(0, format_parser=PDF_TEXT)], 60000)
    assert stats['truncated'] is False


# --------------------------------------------------------------------------- #
# page_texts (field_pages input)
# --------------------------------------------------------------------------- #
def test_page_texts_number_pages_and_skip_media():
    segs = [
        _page(1, format_parser='second page', paddleocr='second page OCR'),
        {'segment_index': 2, 'segment_type': 'VIDEO', 'text_content': 'VIDEO TEXT'},
        _page(0, format_parser=PDF_TEXT, ai_analysis=[{'analysis_query': 'a', 'content': 'AI-ONLY'}]),
        _page(3, ai_analysis=[{'analysis_query': 'a', 'content': VISION_TEXT}]),
    ]
    assert page_texts(segs) == [(1, PDF_TEXT), (2, 'second page\nsecond page OCR'), (4, '')]
    assert page_texts(None) == []


def test_page_texts_are_the_grounding_text_split_by_page():
    segs = [_page(0, format_parser=PDF_TEXT), _page(1, paddleocr=OCR_TEXT)]
    _, grounding_text, _ = build_texts(segs, 60000)
    assert '\n'.join(t for _, t in page_texts(segs)) == grounding_text


# --------------------------------------------------------------------------- #
# prompts
# --------------------------------------------------------------------------- #
def test_prompt_yaml_loads_with_placeholders():
    prompts = load_prompts()
    assert '{document_text}' in prompts['user_prompt']
    assert '{tool_name}' in prompts['user_prompt']
    assert prompts['system_prompt'] == (
        PLAN_B_SYSTEM_PROMPT + ' The input is the machine-extracted text of one document; '
        'page markers are not part of the document.' + OBLIGATIONS_PROMPT + CIBIL_PAGE_PROMPT)


def test_user_prompt_uses_replace_not_format():
    text = 'Literal braces {not_a_key} {0} and a planted {tool_name} token'
    prompt = build_user_prompt(text)
    assert text in prompt
    assert f'by calling {TOOL_NAME}.' in prompt
    assert '{document_text}' not in prompt


# --------------------------------------------------------------------------- #
# call_model with a fake Bedrock client
# --------------------------------------------------------------------------- #
class FakeBedrock:
    def __init__(self, content, usage=None, served_tier=None):
        self.content = content
        self.usage = usage if usage is not None else {'inputTokens': 1200, 'outputTokens': 150}
        self.served_tier = served_tier
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        resp = {'output': {'message': {'role': 'assistant', 'content': self.content}},
                'usage': self.usage, 'stopReason': 'tool_use'}
        if self.served_tier:
            resp['serviceTier'] = {'type': self.served_tier}
        return resp


RAW = {'doc_type': 'salary_slip', 'applicant_name': 'Rahul Vijay Deshmukh', 'pan': 'BQXP4821K',
       'gross_salary': 95000, 'net_salary': 82500}
# gpt-oss answers with its reasoning first, then the tool call.
REASONING = {'reasoningContent': {'reasoningText': {
    'text': 'We need to call record_loan_document. Draft: {"doc_type": "other", "pan": "AAAAA0000A"}'}}}


def test_call_model_request_and_tool_use_response():
    fake = FakeBedrock([REASONING, {'toolUse': {'toolUseId': 't1', 'name': TOOL_NAME, 'input': RAW}}])
    fields, usage = call_model('--- Page 1 ---\nhello', FACTS_MODEL, client=fake)
    assert fields == RAW
    # 1,200 x $0.18/M + 150 x $0.71/M = $0.000216 + $0.0001065 (standard tier)
    assert usage == {'model_id': FACTS_MODEL, 'service_tier': 'default', 'input_tokens': 1200,
                     'output_tokens': 150, 'cost_usd': 0.0003225}

    (req,) = fake.calls
    assert req['modelId'] == FACTS_MODEL
    assert 'serviceTier' not in req  # BEDROCK_SERVICE_TIER unset: standard tier
    assert req['system'] == [{'text': load_prompts()['system_prompt']}]
    assert req['inferenceConfig'] == {'maxTokens': 16000, 'temperature': 0}
    tool_config = req['toolConfig']
    assert tool_config['toolChoice'] == {'tool': {'name': 'record_loan_document'}}
    (tool,) = tool_config['tools']
    spec = tool['toolSpec']
    assert spec['name'] == 'record_loan_document'
    assert spec['description'] == 'Record the structured fields of one loan-file document.'
    assert spec['inputSchema']['json'] is TOOL_SCHEMA
    for t in _types(spec['inputSchema']['json']):
        assert isinstance(t, str) and t != 'null'
    (message,) = req['messages']
    assert message['role'] == 'user'
    assert message['content'] == [{'text': build_user_prompt('--- Page 1 ---\nhello')}]
    assert all('document' not in block for block in message['content'])  # text only, no file bytes


def test_call_model_requests_the_flex_tier_and_prices_it(monkeypatch):
    monkeypatch.setenv('BEDROCK_SERVICE_TIER', 'flex')
    fake = FakeBedrock([REASONING, {'toolUse': {'input': RAW}}], served_tier='flex')
    fields, usage = call_model('text', FACTS_MODEL, client=fake)
    assert fields == RAW
    assert fake.calls[0]['serviceTier'] == {'type': 'flex'}
    # 1,200 x $0.09/M + 150 x $0.355/M: half the standard price
    assert usage == usage_record(FACTS_MODEL, 1200, 150, 'flex')
    assert usage['service_tier'] == 'flex' and usage['cost_usd'] == 0.00016125


def test_call_model_prices_the_tier_that_served_the_call(monkeypatch):
    monkeypatch.setenv('BEDROCK_SERVICE_TIER', 'flex')
    fake = FakeBedrock([{'toolUse': {'input': RAW}}], served_tier='default')
    _, usage = call_model('text', FACTS_MODEL, client=fake)
    assert usage['service_tier'] == 'default' and usage['cost_usd'] == 0.0003225
    # no serviceTier in the response: the requested tier is billed
    _, usage = call_model('text', FACTS_MODEL, client=FakeBedrock([{'toolUse': {'input': RAW}}]))
    assert usage['service_tier'] == 'flex'


@pytest.mark.parametrize('env_value, expected', [
    (None, None), ('', None), ('flex', 'flex'), (' Flex ', 'flex'), ('default', 'default'),
    ('priority', 'priority'), ('turbo', None), ('reserved', None)])
def test_service_tier_env(monkeypatch, env_value, expected):
    if env_value is not None:
        monkeypatch.setenv('BEDROCK_SERVICE_TIER', env_value)
    assert service_tier() == expected
    fake = FakeBedrock([{'toolUse': {'input': RAW}}])
    call_model('text', FACTS_MODEL, client=fake)
    assert fake.calls[0].get('serviceTier') == ({'type': expected} if expected else None)


def test_call_model_falls_back_to_json_in_text():
    fake = FakeBedrock([{'text': 'Here you go:\n```json\n{"doc_type": "bank_statement", "applicant_name": "X"}\n```'}],
                       usage={})
    fields, usage = call_model('text', 'm', client=fake)
    assert fields == {'doc_type': 'bank_statement', 'applicant_name': 'X'}
    assert usage == {'model_id': 'm', 'service_tier': 'default', 'input_tokens': 0, 'output_tokens': 0,
                     'cost_usd': 0.0}


def test_call_model_falls_back_to_json_in_text_after_reasoning():
    fake = FakeBedrock([REASONING, {'text': '{"doc_type": "bank_statement", "applicant_name": "X"}'}])
    fields, _ = call_model('text', FACTS_MODEL, client=fake)
    assert fields == {'doc_type': 'bank_statement', 'applicant_name': 'X'}


def test_call_model_tool_use_wins_over_earlier_text_json():
    fake = FakeBedrock([{'text': '{"doc_type": "other"}'}, {'toolUse': {'input': RAW}}])
    fields, _ = call_model('text', 'm', client=fake)
    assert fields == RAW


def test_call_model_raises_without_structured_output():
    with pytest.raises(ValueError, match='model returned no structured output'):
        call_model('text', 'm', client=FakeBedrock([{'text': 'I cannot help with that.'}]))
    with pytest.raises(ValueError, match='model returned no structured output'):
        call_model('text', 'm', client=FakeBedrock([]))


def test_reasoning_is_never_read_as_fields():
    # Output cut during the reasoning: the JSON drafted there is not a result.
    with pytest.raises(NoStructuredOutputError):
        call_model('text', FACTS_MODEL, client=FakeBedrock([REASONING]))


def test_answer_without_fields_carries_the_billed_usage():
    fake = FakeBedrock([{'text': 'I cannot help with that.'}], usage={'inputTokens': 800, 'outputTokens': 12})
    with pytest.raises(NoStructuredOutputError) as err:
        call_model('text', FACTS_MODEL, client=fake)
    assert err.value.usage == usage_record(FACTS_MODEL, 800, 12, 'default')
    assert err.value.usage['cost_usd'] == 0.00015252  # 800 x 0.18/M + 12 x 0.71/M


def test_default_client_is_lazy_and_uses_region_and_retries(monkeypatch):
    created = {}

    class FakeBoto3:
        @staticmethod
        def client(name, region_name=None, config=None):
            created.update(name=name, region_name=region_name, config=config)
            return FakeBedrock([{'toolUse': {'input': RAW}}])

    monkeypatch.setitem(sys.modules, 'boto3', FakeBoto3)
    monkeypatch.setattr(extractor, '_bedrock_client', None)
    fields, _ = call_model('text', 'm')
    assert fields == RAW
    assert created['name'] == 'bedrock-runtime'
    assert created['region_name'] == 'ap-south-1'
    assert created['config'].retries == {'max_attempts': 4, 'mode': 'adaptive'}
    assert created['config'].read_timeout == 120


@pytest.mark.parametrize('env_value, expected', [
    (None, 16000), ('12000', 12000), ('0', 16000), ('-5', 16000), ('lots', 16000)])
def test_max_output_tokens_env_override(monkeypatch, env_value, expected):
    if env_value is None:
        monkeypatch.delenv('FACTS_MAX_OUTPUT_TOKENS', raising=False)
    else:
        monkeypatch.setenv('FACTS_MAX_OUTPUT_TOKENS', env_value)
    fake = FakeBedrock([{'toolUse': {'input': RAW}}])
    call_model('text', 'm', client=fake)
    assert fake.calls[0]['inferenceConfig']['maxTokens'] == expected


class TruncatingBedrock(FakeBedrock):
    def converse(self, **kwargs):
        resp = super().converse(**kwargs)
        resp['stopReason'] = 'max_tokens'
        return resp


def test_call_model_flags_output_cut_at_max_tokens():
    fake = TruncatingBedrock([{'toolUse': {'toolUseId': 't1', 'name': TOOL_NAME, 'input': RAW}}])
    fields, usage = call_model('text', FACTS_MODEL, client=fake)
    assert fields == RAW
    assert usage == {**usage_record(FACTS_MODEL, 1200, 150, 'default'), 'output_truncated': True}


# --------------------------------------------------------------------------- #
# cost per document: the model's price for the tier that served the call
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('model_id, tier, tokens_in, tokens_out, expected', [
    (FACTS_MODEL, 'default', 0, 0, 0.0),
    (FACTS_MODEL, None, 1_000_000, 0, 0.18),
    (FACTS_MODEL, 'default', 0, 1_000_000, 0.71),
    (FACTS_MODEL, 'flex', 1_000_000, 1_000_000, 0.445),
    (FACTS_MODEL, 'flex', 10_000, 500, 0.0010775),
    (FACTS_MODEL, 'default', 12345, 678, 0.00270348),  # rounded to 8 decimals like the Ask feature
    (NOVA_2_LITE, 'default', 10_000, 500, 0.004975),  # the Ask API contract's worked example
    (NOVA_2_LITE, 'flex', 10_000, 500, 0.0024875),
    ('m', 'default', 10, 10, None),  # no price for the model: unknown, not guessed
    (FACTS_MODEL, 'reserved', 10, 10, None),
    (None, None, 0, 0, 0.0),  # no call
])
def test_cost_usd(model_id, tier, tokens_in, tokens_out, expected):
    assert cost_usd(tokens_in, tokens_out, model_id, tier) == expected


def test_usage_record_shape():
    assert usage_record() == {'model_id': None, 'service_tier': None, 'input_tokens': 0, 'output_tokens': 0,
                              'cost_usd': 0.0}
    assert usage_record(FACTS_MODEL, 2000, 400, 'flex') == {
        'model_id': FACTS_MODEL, 'service_tier': 'flex', 'input_tokens': 2000, 'output_tokens': 400,
        'cost_usd': 0.000322}


def test_flex_is_half_the_standard_price():
    for model_id, tiers in PRICES_PER_MILLION_USD.items():
        assert tiers['flex'] == (tiers['default'][0] / 2, tiers['default'][1] / 2), model_id


def test_the_facts_model_of_models_json_is_priced():
    """FACTS_MODEL_ID comes from models.json: its calls are never recorded at an unknown cost."""
    import json

    with open(MODELS_JSON, encoding='utf-8') as fh:
        facts_model = json.load(fh)['facts']
    assert facts_model == FACTS_MODEL
    assert {'default', 'flex'} <= set(PRICES_PER_MILLION_USD[facts_model])


BACKEND_ASK = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..', '..', 'backend', 'app', 'file_check_ask.py'))


@pytest.mark.skipif(not os.path.exists(BACKEND_ASK), reason='backend source not in this checkout')
def test_nova_price_equals_the_ask_feature():
    """Nova 2 Lite is costed like the Ask feature (packages/backend/app/file_check_ask.py)."""
    import ast

    with open(BACKEND_ASK, encoding='utf-8') as fh:
        tree = ast.parse(fh.read())
    prices = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id.endswith('_PRICE_PER_MILLION_USD')
    }
    assert PRICES_PER_MILLION_USD[NOVA_2_LITE]['default'] == (
        prices['INPUT_PRICE_PER_MILLION_USD'], prices['OUTPUT_PRICE_PER_MILLION_USD'])
