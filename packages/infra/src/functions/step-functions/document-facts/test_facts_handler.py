"""Handler tests for the document-facts Lambda (index.py).

All AWS-touching module-level functions are monkeypatched with recorders;
synthetic data only.

Run: python -m pytest -q   (from this folder)
"""
import json
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

import index  # noqa: E402
from extractor import NoStructuredOutputError, usage_record  # noqa: E402
from tool_schema import FIELD_NAMES, LIST_FIELDS  # noqa: E402

MODEL_ID = 'openai.gpt-oss-120b-1:0'
DOC_NAME = '05_salary_slip_2026-08_aug.pdf'
FILE_URI = 's3://doc-bucket/projects/p1/documents/d1/d1.pdf'

SLIP_PAGE_TEXT = (
    'Salary Slip - Page 1\nKonkan Softworks Pvt Ltd\n'
    'Salary slip for the month of August 2026\n'
    'Employee name\nRahul Vijay Deshmukh\nPAN\nBQXPD4821K\n'
    'Gross Earnings\n95,000\nNET PAY (take-home)\n₹82,500\n'
)

RAW_FIELDS = {
    'doc_type': 'salary_slip',
    'applicant_name': 'Rahul Vijay Deshmukh',
    'pan': 'BQXP4821K',  # model misread (section 12)
    'employer': 'Konkan Softworks Pvt Ltd',
    'month': 'August 2026',
    'gross_salary': '95,000',
    'net_salary': 82500,
    'product': 'null',
}

EVENT = {
    'workflow_id': 'wf_1',
    'document_id': 'd1',
    'project_id': 'p1',
    'file_uri': FILE_URI,
    'file_type': 'application/pdf',
    'segment_count': 1,
    'language': 'en',
}


class Recorder:
    def __init__(self, result=None, exc=None):
        self.calls = []
        self.result = result
        self.exc = exc

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv('FACTS_MODEL_ID', MODEL_ID)
    monkeypatch.setenv('FACTS_MAX_CHARS', '60000')
    rec = {
        'get_document': Recorder({'name': DOC_NAME, 'status': 'in_progress'}),
        'get_all_segment_analyses': Recorder([{
            'segment_index': 0,
            'segment_type': 'PAGE',
            'format_parser': SLIP_PAGE_TEXT,
            'paddleocr': '',
            'ai_analysis': [{'analysis_query': 'Page 1 Analysis', 'content': 'A salary slip.'}],
        }]),
        'call_model': Recorder((RAW_FIELDS, usage_record(MODEL_ID, 900, 120, 'flex'))),
        'save_facts': Recorder('projects/p1/documents/d1/analysis/facts.json'),
        'save_document_facts': Recorder({}),
        'record_step_start': Recorder({}),
        'record_step_complete': Recorder({}),
        'record_step_error': Recorder({}),
    }
    for name, fn in rec.items():
        monkeypatch.setattr(index, name, fn)
    yield rec
    for name in ('record_step_start', 'record_step_complete', 'record_step_error'):
        for args, _ in rec[name].calls:
            assert args[1] != 'segment_analyzer', 'only the summarizer completes segment_analyzer'


def _only_call(recorder):
    assert len(recorder.calls) == 1, recorder.calls
    return recorder.calls[0]


def test_completed_path(env, capsys):
    result = index.handler(dict(EVENT), None)
    assert result == {'workflow_id': 'wf_1', 'status': 'completed'}
    assert set(result) == {'workflow_id', 'status'}

    assert _only_call(env['record_step_start'])[0] == ('wf_1', 'document_facts')
    args, kwargs = _only_call(env['get_all_segment_analyses'])
    assert args == (FILE_URI, 1)
    assert set(kwargs['fields']) == {'segment_type', 'format_parser', 'paddleocr', 'bda_indexer',
                                     'text_content', 'webcrawler_content', 'ai_analysis'}

    (model_text, model_id), _ = _only_call(env['call_model'])
    assert model_id == MODEL_ID
    assert model_text.startswith('--- Page 1 ---\n')
    assert DOC_NAME not in model_text, 'the file name is never sent to the model'

    (pid, did, record), _ = _only_call(env['save_document_facts'])
    assert (pid, did) == ('p1', 'd1')
    assert record['schema_version'] == 1
    assert record['document_name'] == DOC_NAME
    assert record['document_id'] == 'd1' and record['project_id'] == 'p1' and record['workflow_id'] == 'wf_1'
    assert record['file_type'] == 'application/pdf'
    assert record['status'] == 'completed'
    assert record['source'] == 'model'
    assert record['model_id'] == MODEL_ID
    assert record['doc_type'] == 'salary_slip'
    assert record['reason'] is None and record['error'] is None
    # gpt-oss-120b on the Flex tier: 900 x $0.09/M + 120 x $0.355/M = $0.000081 + $0.0000426
    assert record['usage'] == {'model_id': MODEL_ID, 'service_tier': 'flex', 'input_tokens': 900,
                               'output_tokens': 120, 'cost_usd': 0.0001236}
    assert record['extracted_at'].endswith('+00:00')
    fields = record['fields']
    assert list(fields) == FIELD_NAMES
    assert fields['pan'] == 'BQXPD4821K'  # grounded against the text layer
    assert fields['month'] == '2026-08'
    assert fields['gross_salary'] == 95000.0 and fields['net_salary'] == 82500.0
    assert fields['product'] is None
    grounding = record['grounding']
    assert grounding['grounded'] is True
    assert grounding['truncated'] is False
    assert grounding['unverified_fields'] == []
    assert grounding['text_chars'] == len(SLIP_PAGE_TEXT)
    assert any('PAN corrected' in n for n in grounding['notes'])
    # one page: every value is on page 1
    assert record['field_pages'] == {'applicant_name': 1, 'pan': 1, 'employer': 1, 'month': 1,
                                     'gross_salary': 1, 'net_salary': 1}
    assert 'model_fields' not in record
    assert 'Salary slip for the month' not in json.dumps(record), 'DDB copy stores no document text'

    (uri, s3_record), _ = _only_call(env['save_facts'])
    assert uri == FILE_URI
    assert s3_record['model_fields']['pan'] == 'BQXP4821K'  # before grounding, for audit
    assert {k: v for k, v in s3_record.items() if k != 'model_fields'} == record

    args, kwargs = _only_call(env['record_step_complete'])
    assert args == ('wf_1', 'document_facts')
    assert kwargs == {'doc_type': 'salary_slip'}
    assert env['record_step_error'].calls == []

    out = capsys.readouterr().out
    for secret in ('Rahul', 'BQXPD4821K', 'BQXP4821K', '82500', '82,500', '95,000', DOC_NAME):
        assert secret not in out, f'{secret!r} leaked into logs'


def test_document_name_falls_back_to_file_basename(env):
    env['get_document'].result = None
    index.handler(dict(EVENT), None)
    (_, _, record), _ = _only_call(env['save_document_facts'])
    assert record['document_name'] == 'd1.pdf'


def test_document_id_falls_back_to_file_uri(env):
    event = dict(EVENT)
    event.pop('document_id')
    index.handler(event, None)
    (_, did, record), _ = _only_call(env['save_document_facts'])
    assert did == 'd1' and record['document_id'] == 'd1'


def test_truncation_and_ungrounded_notes(env, monkeypatch):
    monkeypatch.setenv('FACTS_MAX_CHARS', '30')
    env['get_all_segment_analyses'].result = [{
        'segment_index': 0, 'segment_type': 'PAGE', 'format_parser': 'tiny',
        'ai_analysis': [{'analysis_query': 'Page 1 Analysis', 'content': 'Vision transcription ' * 10}],
    }]
    env['call_model'].result = ({'doc_type': 'identity_details', 'pan': 'bqxpd 4821k'}, usage_record(MODEL_ID, 5, 5))
    assert index.handler(dict(EVENT), None)['status'] == 'completed'
    (model_text, _), _ = _only_call(env['call_model'])
    assert len(model_text) <= 30
    (_, _, record), _ = _only_call(env['save_document_facts'])
    assert record['grounding']['grounded'] is False
    assert record['grounding']['truncated'] is True
    assert 'input truncated to 30 chars' in record['grounding']['notes']
    assert 'no machine text layer; values not verified' in record['grounding']['notes']
    assert record['grounding']['unverified_fields'] == []
    assert record['fields']['pan'] == 'BQXPD4821K'


def test_failure_is_non_fatal(env):
    env['call_model'].exc = RuntimeError('ThrottlingException: slow down')
    result = index.handler(dict(EVENT), None)
    assert result == {'workflow_id': 'wf_1', 'status': 'failed'}

    args, _ = _only_call(env['record_step_error'])
    assert args == ('wf_1', 'document_facts', 'ThrottlingException: slow down')
    assert env['record_step_complete'].calls == []
    assert env['save_facts'].calls == []

    (pid, did, record), _ = _only_call(env['save_document_facts'])
    assert (pid, did) == ('p1', 'd1')
    assert record['status'] == 'failed'
    assert record['doc_type'] == 'other'
    assert record['error'] == 'ThrottlingException: slow down'
    assert record['document_name'] == DOC_NAME
    assert record['source'] == 'none'


def test_failure_error_is_capped_and_bookkeeping_errors_are_swallowed(env):
    env['call_model'].exc = ValueError('x' * 2000)
    env['record_step_error'].exc = RuntimeError('ddb down')
    env['save_document_facts'].exc = RuntimeError('ddb down')
    assert index.handler(dict(EVENT), None) == {'workflow_id': 'wf_1', 'status': 'failed'}
    (_, _, record), _ = env['save_document_facts'].calls[0]
    assert len(record['error']) == 500


def test_step_start_failure_does_not_stop_extraction(env):
    env['record_step_start'].exc = RuntimeError('ddb blip')
    assert index.handler(dict(EVENT), None)['status'] == 'completed'


def test_missing_model_env_fails_softly(env, monkeypatch):
    monkeypatch.delenv('FACTS_MODEL_ID')
    assert index.handler(dict(EVENT), None) == {'workflow_id': 'wf_1', 'status': 'failed'}
    assert env['call_model'].calls == []


def test_media_only_is_skipped(env):
    env['get_all_segment_analyses'].result = [
        {'segment_index': 0, 'segment_type': 'VIDEO', 'ai_analysis': [{'content': 'a scene'}]},
        {'segment_index': 1, 'segment_type': 'CHAPTER', 'ai_analysis': [{'content': 'chapter 1'}]},
    ]
    event = dict(EVENT, file_type='video/mp4', segment_count=2)
    assert index.handler(event, None) == {'workflow_id': 'wf_1', 'status': 'skipped'}
    assert env['call_model'].calls == []

    (_, _, record), _ = _only_call(env['save_document_facts'])
    assert record['status'] == 'skipped'
    assert record['reason'] == 'media'
    assert record['doc_type'] == 'other'
    assert record['source'] == 'none'
    assert all(record['fields'][k] is None for k in FIELD_NAMES if k != 'doc_type' and k not in LIST_FIELDS)
    assert all(record['fields'][k] == [] for k in LIST_FIELDS)
    assert record['field_pages'] == {}
    (_, s3_record), _ = _only_call(env['save_facts'])
    assert s3_record['status'] == 'skipped'

    args, kwargs = _only_call(env['record_step_complete'])
    assert args == ('wf_1', 'document_facts')
    assert kwargs == {'skipped': True, 'reason': 'media'}


def test_web_page_is_skipped_without_reading_s3(env):
    # create_workflow marks document_facts skipped for web pages; keep it so.
    event = dict(EVENT, file_type='application/x-webreq', segment_count=3)
    assert index.handler(event, None) == {'workflow_id': 'wf_1', 'status': 'skipped'}
    assert env['get_all_segment_analyses'].calls == []
    assert env['call_model'].calls == []
    (_, _, record), _ = _only_call(env['save_document_facts'])
    assert record['status'] == 'skipped' and record['reason'] == 'webreq'
    args, kwargs = _only_call(env['record_step_complete'])
    assert args == ('wf_1', 'document_facts')
    assert kwargs == {'skipped': True, 'reason': 'webreq'}


def test_no_text_is_skipped(env):
    env['get_all_segment_analyses'].result = [
        {'segment_index': 0, 'segment_type': 'PAGE', 'format_parser': '', 'ai_analysis': []}]
    assert index.handler(dict(EVENT), None) == {'workflow_id': 'wf_1', 'status': 'skipped'}
    (_, _, record), _ = _only_call(env['save_document_facts'])
    assert record['reason'] == 'no_text'
    _, kwargs = _only_call(env['record_step_complete'])
    assert kwargs == {'skipped': True, 'reason': 'no_text'}


def test_zero_segments_does_not_read_s3(env):
    event = dict(EVENT, segment_count=0)
    assert index.handler(event, None) == {'workflow_id': 'wf_1', 'status': 'skipped'}
    assert env['get_all_segment_analyses'].calls == []
    (_, _, record), _ = _only_call(env['save_document_facts'])
    assert record['reason'] == 'no_text'


BANK_PAGE_TEXT = (
    'STATEMENT OF ACCOUNT\nAccount holder RAHUL VIJAY DESHMUKH\nPeriod: 01-Mar-2026 to 31-Mar-2026\n'
    '01-Mar-2026 NEFT CR/SAL KONKAN SOFTWORKS/MAR26 SAHYN7438010963 82,500.00 2,01,142.35\n'
    '03-Mar-2026 NEFT DR/RENT MAR26/VASANT JOSHI SAHYN7467648334 18,000.00 1,83,142.35\n'
    '05-Mar-2026 ACH DR/MULSHI AUTO FINANCE/CAR LOAN EMI ACH11261866 8,200.00 1,74,942.35\n'
)


def test_bank_statement_obligations_are_stored_without_leaking_to_logs(env, capsys):
    env['get_all_segment_analyses'].result = [
        {'segment_index': 0, 'segment_type': 'PAGE', 'format_parser': BANK_PAGE_TEXT, 'ai_analysis': []}]
    env['call_model'].result = ({
        'doc_type': 'bank_statement', 'applicant_name': 'RAHUL VIJAY DESHMUKH',
        'statement_from': '01-Mar-2026', 'statement_to': '31-Mar-2026',
        'salary_credits': [{'date': '01-Mar-2026', 'amount': 82500,
                            'narration': 'NEFT CR/SAL KONKAN SOFTWORKS/MAR26'}],
        'recurring_debits': [
            {'date': '03-Mar-2026', 'amount': 18000, 'narration': 'NEFT DR/RENT MAR26/VASANT JOSHI',
             'channel': 'other', 'category': 'rent'},
            {'date': '05-Mar-2026', 'amount': 820, 'narration': 'ACH DR/MULSHI AUTO FINANCE/CAR LOAN EMI',
             'channel': 'ACH', 'category': 'loan_emi'},
            {'date': '06-Mar-2026', 'amount': 4321, 'narration': 'NACH DR/UNKNOWN', 'category': 'loan_emi'},
        ],
    }, usage_record(MODEL_ID, 2000, 400))
    assert index.handler(dict(EVENT), None)['status'] == 'completed'
    (_, _, record), _ = _only_call(env['save_document_facts'])
    debits = record['fields']['recurring_debits']
    assert [d['amount'] for d in debits] == [18000.0, 8200.0, 4321.0]
    assert debits[1] == {'date': '2026-03-05', 'amount': 8200.0,
                         'narration': 'ACH DR/MULSHI AUTO FINANCE/CAR LOAN EMI',
                         'channel': 'ACH', 'category': 'loan_emi'}
    assert debits[2]['channel'] == 'NACH'  # inferred from the narration
    assert record['fields']['declared_existing_emis'] == []
    assert record['grounding']['unverified_fields'] == ['recurring_debits[2].amount']
    assert any(n.startswith('debit 2026-03-05 corrected') for n in record['grounding']['notes'])
    (_, s3_record), _ = _only_call(env['save_facts'])
    assert s3_record['model_fields']['recurring_debits'][1]['amount'] == 820.0  # before grounding
    out = capsys.readouterr().out
    assert 'debits=3 declared_emis=0' in out
    for secret in ('MULSHI', 'VASANT', '8200', '8,200', '18000', 'RAHUL'):
        assert secret not in out, f'{secret!r} leaked into logs'


def test_output_cut_at_max_tokens_is_recorded_in_grounding(env):
    env['get_all_segment_analyses'].result = [
        {'segment_index': 0, 'segment_type': 'PAGE', 'format_parser': BANK_PAGE_TEXT, 'ai_analysis': []}]
    env['call_model'].result = ({
        'doc_type': 'bank_statement',
        'recurring_debits': [{'date': '03-Mar-2026', 'amount': 18000, 'category': 'rent',
                              'narration': 'NEFT DR/RENT MAR26/VASANT JOSHI'}],
    }, {**usage_record(MODEL_ID, 2000, 8000), 'output_truncated': True})
    assert index.handler(dict(EVENT), None)['status'] == 'completed'
    (_, _, record), _ = _only_call(env['save_document_facts'])
    assert record['grounding']['output_truncated'] is True
    assert 'model output hit the token limit; lists may be incomplete' in record['grounding']['notes']
    assert record['usage'] == usage_record(MODEL_ID, 2000, 8000)  # flag not in usage


def test_output_not_truncated_by_default(env):
    assert index.handler(dict(EVENT), None)['status'] == 'completed'
    (_, _, record), _ = _only_call(env['save_document_facts'])
    assert record['grounding']['output_truncated'] is False


# --------------------------------------------------------------------------- #
# usage: tokens and cost of the one model call (cost per document)
# --------------------------------------------------------------------------- #
NO_CALL_USAGE = {'model_id': None, 'service_tier': None, 'input_tokens': 0, 'output_tokens': 0, 'cost_usd': 0.0}


def test_skipped_document_records_zero_usage(env):
    env['get_all_segment_analyses'].result = [
        {'segment_index': 0, 'segment_type': 'PAGE', 'format_parser': '', 'ai_analysis': []}]
    assert index.handler(dict(EVENT), None)['status'] == 'skipped'
    assert env['call_model'].calls == []
    (_, _, record), _ = _only_call(env['save_document_facts'])
    assert record['usage'] == NO_CALL_USAGE
    (_, s3_record), _ = _only_call(env['save_facts'])
    assert s3_record['usage'] == NO_CALL_USAGE


def test_failure_before_the_model_call_records_zero_usage(env):
    env['call_model'].exc = RuntimeError('ThrottlingException: slow down')
    assert index.handler(dict(EVENT), None)['status'] == 'failed'
    (_, _, record), _ = _only_call(env['save_document_facts'])
    assert record['usage'] == NO_CALL_USAGE


def test_answer_without_fields_is_failed_but_its_usage_is_recorded(env):
    # The model answered (and was billed) but gave no structured output.
    env['call_model'].exc = NoStructuredOutputError(usage_record(MODEL_ID, 700, 40, 'flex'))
    assert index.handler(dict(EVENT), None) == {'workflow_id': 'wf_1', 'status': 'failed'}
    (_, _, record), _ = _only_call(env['save_document_facts'])
    assert record['status'] == 'failed'
    assert record['error'] == 'model returned no structured output'
    assert record['usage'] == {'model_id': MODEL_ID, 'service_tier': 'flex', 'input_tokens': 700,
                               'output_tokens': 40, 'cost_usd': 0.0000772}


def test_failure_after_the_model_call_keeps_its_usage(env, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError('grounding bug')

    monkeypatch.setattr(index, 'ground_fields', boom)
    assert index.handler(dict(EVENT), None)['status'] == 'failed'
    (_, _, record), _ = _only_call(env['save_document_facts'])
    assert record['status'] == 'failed'
    assert record['usage'] == usage_record(MODEL_ID, 900, 120, 'flex')


def test_usage_is_logged_as_counts_only(env, capsys):
    index.handler(dict(EVENT), None)
    out = capsys.readouterr().out
    assert 'input_tokens=900 output_tokens=120 service_tier=flex cost_usd=0.0001236' in out
    for secret in ('Rahul', 'BQXPD4821K', '82500', DOC_NAME):
        assert secret not in out


# --------------------------------------------------------------------------- #
# credit report: CIBIL page fields, pages, account numbers (synthetic)
# --------------------------------------------------------------------------- #
CREDIT_PAGE_1 = (
    'Sample credit report (synthetic, for demo only)\n'
    'Bureau CIBIL (sample format) Report date 18-Sep-2026\n'
    'Name RAHUL VIJAY DESHMUKH Date of birth 14-02-1992 PAN BQXPD4821K\n'
    'Credit score 771 (range 300-900)\n'
    'ACCOUNTS\nCar loan Mulshi Auto Finance Ltd MAF2022070004512 4,50,000 1,65,115 8,200 0 Active 50 22 '
    '10-07-2022 05-Sep-2026\n'
)
CREDIT_PAGE_2 = (
    'Credit card Tamrapur Urban Co-op Bank 5243910000457731 1,50,000 12,400 – 0 Active 14-03-2019 16-Aug-2026\n'
    'ENQUIRIES\n02-Aug-2026 Konkan Finserv Ltd (sample) Credit Card\n'
    '14-Jun-2026 Godavari Credit Ltd (sample) Personal Loan\n11-Nov-2025 Tamrapur Urban Co-op Bank Credit Card\n'
)
CREDIT_RAW = {
    'doc_type': 'credit_report', 'applicant_name': 'RAHUL VIJAY DESHMUKH', 'pan': 'BQXPD4821K',
    'bureau': 'CIBIL', 'report_date': '2026-09-18', 'credit_score': 771, 'dob': '1992-02-14',
    'enquiries': [{'date': '2026-08-02', 'lender': 'Konkan Finserv Ltd (sample)', 'purpose': 'Credit Card'},
                  {'date': '2026-06-14', 'lender': 'Godavari Credit Ltd (sample)', 'purpose': 'Personal Loan'},
                  {'date': '2025-11-11', 'lender': 'Tamrapur Urban Co-op Bank', 'purpose': 'Credit Card'}],
    'tradelines': [
        # the model copied the whole account numbers despite the schema
        {'loan_type': 'car_loan', 'lender': 'Mulshi Auto Finance Ltd', 'sanction_amount': 450000,
         'outstanding': 165115, 'emi': 820, 'status': 'active', 'account_last4': 'MAF2022070004512',
         'overdue': 0, 'emis_paid': 50, 'emis_pending': 22, 'open_date': '2022-07-10',
         'last_payment_date': '2026-09-05'},
        {'loan_type': 'credit_card', 'lender': 'Tamrapur Urban Co-op Bank', 'sanction_amount': 150000,
         'outstanding': 12400, 'status': 'active', 'account_number': '5243910000457731', 'overdue': 0,
         'open_date': '2019-03-14', 'last_payment_date': '2026-08-16'},
    ],
}


def test_credit_report_is_stored_with_pages_and_last_4_only(env, capsys):
    env['get_all_segment_analyses'].result = [
        {'segment_index': 0, 'segment_type': 'PAGE', 'format_parser': CREDIT_PAGE_1, 'ai_analysis': []},
        {'segment_index': 1, 'segment_type': 'PAGE', 'format_parser': CREDIT_PAGE_2, 'ai_analysis': []},
    ]
    env['call_model'].result = (CREDIT_RAW, usage_record(MODEL_ID, 3000, 900, 'flex'))
    assert index.handler(dict(EVENT, segment_count=2), None)['status'] == 'completed'
    (_, _, record), _ = _only_call(env['save_document_facts'])
    fields = record['fields']
    assert record['doc_type'] == 'credit_report'
    assert (fields['bureau'], fields['report_date'], fields['credit_score']) == ('CIBIL', '2026-09-18', 771.0)
    assert fields['dob'] is None  # read from the application / identity details only
    assert [fields[k] for k in ('enquiries_30d', 'enquiries_60d', 'enquiries_90d', 'enquiries_120d')] == [0, 1, 1, 2]
    assert [t['account_last4'] for t in fields['tradelines']] == ['4512', '7731']
    assert [t['page'] for t in fields['tradelines']] == [1, 2]  # each row carries its page
    assert [e['page'] for e in fields['enquiries']] == [2, 2, 2]
    assert fields['tradelines'][0]['emi'] == 8200.0  # grounded: the model dropped a digit
    assert record['grounding']['unverified_fields'] == []
    assert record['field_pages'] == {
        'applicant_name': 1, 'pan': 1, 'bureau': 1, 'report_date': 1, 'credit_score': 1,
        'enquiries_30d': 2, 'enquiries_60d': 2, 'enquiries_90d': 2, 'enquiries_120d': 2, 'enquiries': 2,
        'tradelines': 1, 'enquiries[0]': 2, 'enquiries[1]': 2, 'enquiries[2]': 2, 'tradelines[0]': 1,
        'tradelines[1]': 2}
    (_, s3_record), _ = _only_call(env['save_facts'])
    assert 'page' not in s3_record['model_fields']['tradelines'][0]  # the model's fields, as normalised
    for stored in (record, s3_record):  # the audit copy (model_fields) is normalised too
        text = json.dumps(stored)
        for full in ('MAF2022070004512', '5243910000457731', '0004512', '000045'):
            assert full not in text, f'{full!r} of an account number stored'
    out = capsys.readouterr().out
    assert 'doc_type=credit_report' in out and 'tradelines=2 enquiries=3 pages_found=16' in out
    for secret in ('RAHUL', 'BQXPD4821K', 'Mulshi', '4512', '7731', '771', '165115', '1,65,115'):
        assert secret not in out, f'{secret!r} leaked into logs'


def test_application_cibil_page_fields_are_stored(env):
    page = (
        'PERSONAL LOAN APPLICATION FORM\nFull name Rahul Vijay Deshmukh PAN BQXPD4821K\n'
        'Mobile +91 90000 00101 Date of birth 14-02-1992\n'
        'Current address Flat B-702, Sample Heights, Baner Road, Pune, Maharashtra – 411045\n'
        'Permanent address House 21, Sample Wadi, Rajarampuri, Kolhapur, Maharashtra – 416008\n'
        'House ownership Rented Monthly rent ₹18,000\n'
        'Employment type Salaried – Private Limited company Employer name Konkan Softworks Pvt Ltd\n'
    )
    env['get_all_segment_analyses'].result = [
        {'segment_index': 0, 'segment_type': 'PAGE', 'format_parser': page, 'ai_analysis': []}]
    env['call_model'].result = ({
        'doc_type': 'loan_application', 'applicant_name': 'Rahul Vijay Deshmukh', 'pan': 'BQXPD4821K',
        'employer': 'Konkan Softworks Pvt Ltd', 'mobile': '+91 90000 00101', 'dob': '14-02-1992',
        'current_address': 'Flat B-702, Sample Heights, Baner Road, Pune, Maharashtra – 411045',
        'permanent_address': 'House 21, Sample Wadi, Rajarampuri, Kolhapur, Maharashtra – 416008',
        'house_ownership': 'rented', 'employment_type': 'private_limited',
        'monthly_rent': 18000,  # the tenant's rent: not rental income
    }, usage_record(MODEL_ID, 1500, 300, 'flex'))
    assert index.handler(dict(EVENT), None)['status'] == 'completed'
    (_, _, record), _ = _only_call(env['save_document_facts'])
    fields = record['fields']
    assert fields['mobile'] == '9000000101' and fields['dob'] == '1992-02-14'
    assert (fields['current_pincode'], fields['permanent_pincode']) == ('411045', '416008')
    assert (fields['house_ownership'], fields['employment_type']) == ('rented', 'private_limited')
    assert fields['company'] == 'Konkan Softworks Pvt Ltd'
    assert fields['monthly_rent'] is None
    assert record['grounding']['unverified_fields'] == []
    assert record['field_pages']['mobile'] == 1 and record['field_pages']['permanent_address'] == 1
