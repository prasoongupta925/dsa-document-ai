"""Tests for the shared storage helpers used by the facts step (fake Table / S3 client).

Covers shared/ddb_client.py (FACTS# item, STEP nested-path writes, skip
conditions) and shared/s3_analysis.py (analysis/facts.json). No AWS calls.

Run: python -m pytest -q   (from this folder)
"""
import copy
import io
import os
import sys
from decimal import Decimal

os.environ['BACKEND_TABLE_NAME'] = 'test-table'
os.environ['AWS_DEFAULT_REGION'] = 'ap-south-1'
os.environ['AWS_REGION'] = 'ap-south-1'
os.environ['AWS_ACCESS_KEY_ID'] = 'testing'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'testing'

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', '..')))
sys.path.insert(0, HERE)

import pytest  # noqa: E402

from shared import ddb_client, s3_analysis  # noqa: E402
from shared.ddb_client import StepName  # noqa: E402


class FakeTable:
    """Records calls; applies the SET forms used by ddb_client to an in-memory STEP item."""

    def __init__(self, items=None):
        self.items = items or {}
        self.put_calls = []
        self.update_calls = []

    def put_item(self, Item):
        self.put_calls.append(Item)
        self.items[(Item['PK'], Item['SK'])] = copy.deepcopy(Item)
        return {}

    def get_item(self, Key):
        item = self.items.get((Key['PK'], Key['SK']))
        return {'Item': copy.deepcopy(item)} if item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues,
                    ReturnValues=None):
        self.update_calls.append({'Key': Key, 'UpdateExpression': UpdateExpression,
                                  'ExpressionAttributeNames': ExpressionAttributeNames,
                                  'ExpressionAttributeValues': ExpressionAttributeValues})
        item = self.items.setdefault((Key['PK'], Key['SK']), {'PK': Key['PK'], 'SK': Key['SK']})
        names, values = ExpressionAttributeNames, ExpressionAttributeValues
        assert UpdateExpression.startswith('SET ')
        for clause in UpdateExpression[4:].split(', '):
            path, value_ref = [p.strip() for p in clause.split('=')]
            value = copy.deepcopy(values[value_ref])
            parts = [names.get(p, p) for p in path.split('.')]
            target = item
            for p in parts[:-1]:
                target = target[p]  # nested SET requires the parent map to exist
            target[parts[-1]] = value
        return {'Attributes': copy.deepcopy(item)}

    def batch_writer(self):
        table = self

        class _Batch:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def put_item(self, Item):
                table.put_item(Item)

        return _Batch()


def _steps_item(workflow_id='wf_1', **step_states):
    data = {'project_id': 'p1', 'document_id': 'd1', 'current_step': ''}
    for name in StepName.ORDER:
        data[name] = {'status': 'pending', 'label': StepName.LABELS[name]}
    for name, status in step_states.items():
        data[name]['status'] = status
    return {'PK': f'WF#{workflow_id}', 'SK': 'STEP', 'GSI1PK': 'STEP#ANALYSIS_STATUS',
            'GSI1SK': 'pending', 'data': data}


@pytest.fixture
def table(monkeypatch):
    t = FakeTable()
    monkeypatch.setattr(ddb_client, 'get_table', lambda: t)
    return t


# --------------------------------------------------------------------------- #
# StepName
# --------------------------------------------------------------------------- #
def test_step_order_and_label():
    assert StepName.DOCUMENT_FACTS == 'document_facts'
    assert StepName.ORDER.index('document_facts') == StepName.ORDER.index('document_summarizer') + 1
    assert StepName.LABELS['document_facts'] == 'Document Facts'


# --------------------------------------------------------------------------- #
# FACTS# item
# --------------------------------------------------------------------------- #
def test_save_document_facts_uses_decimal_and_keys(table):
    record = {'doc_type': 'loan_application',
              'fields': {'declared_net_salary': 82500.5, 'loan_amount': 600000,
                         'salary_credits': [{'amount': 71200.0}]},
              'grounding': {'notes': ['n'], 'unverified_fields': []}}
    result = ddb_client.save_document_facts('p1', 'd1', record)
    (item,) = table.put_calls
    assert item['PK'] == 'PROJ#p1'
    assert item['SK'] == 'FACTS#d1'
    assert set(item) == {'PK', 'SK', 'data', 'created_at', 'updated_at'}, 'no GSI keys'
    assert item['data']['fields']['declared_net_salary'] == Decimal('82500.5')
    assert isinstance(item['data']['fields']['declared_net_salary'], Decimal)
    assert isinstance(item['data']['fields']['salary_credits'][0]['amount'], Decimal)
    assert item['data']['fields']['loan_amount'] == 600000
    assert result['data']['fields']['declared_net_salary'] == 82500.5
    assert result['SK'] == 'FACTS#d1'


def test_get_document_facts_roundtrip(table):
    assert ddb_client.get_document_facts('p1', 'd1') is None
    ddb_client.save_document_facts('p1', 'd1', {'doc_type': 'salary_slip', 'fields': {'net_salary': 82500.0}})
    data = ddb_client.get_document_facts('p1', 'd1')
    assert data == {'doc_type': 'salary_slip', 'fields': {'net_salary': 82500}}


def test_usage_record_survives_the_decimal_roundtrip(table):
    from extractor import usage_record

    usage = usage_record('global.amazon.nova-2-lite-v1:0', 900, 120)
    ddb_client.save_document_facts('p1', 'd1', {'doc_type': 'salary_slip', 'usage': usage})
    (item,) = table.put_calls
    assert item['data']['usage']['cost_usd'] == Decimal('0.000669')  # exact, not a binary float
    assert ddb_client.get_document_facts('p1', 'd1')['usage'] == usage


# --------------------------------------------------------------------------- #
# STEP writes: nested paths only
# --------------------------------------------------------------------------- #
def test_record_step_complete_writes_nested_path(table):
    table.items[('WF#wf_1', 'STEP')] = _steps_item(graph_builder='in_progress', document_facts='in_progress')
    result = ddb_client.record_step_complete('wf_1', 'document_facts', doc_type='salary_slip')
    (call,) = table.update_calls
    expr = call['UpdateExpression']
    assert '#data.#step' in expr
    assert '#data.current_step' in expr
    assert 'updated_at' in expr
    assert 'SET #data =' not in expr and '#data = :data' not in expr
    assert 'GSI1SK' not in expr
    assert call['ExpressionAttributeNames'] == {'#data': 'data', '#step': 'document_facts'}
    step = call['ExpressionAttributeValues'][':step']
    assert step['status'] == 'completed' and step['doc_type'] == 'salary_slip' and 'ended_at' in step
    assert step['label'] == 'Document Facts'
    assert call['ExpressionAttributeValues'][':cs'] == 'graph_builder'
    assert result['data']['document_facts']['status'] == 'completed'


def test_record_step_complete_segment_analyzer_sets_gsi(table):
    table.items[('WF#wf_1', 'STEP')] = _steps_item(segment_analyzer='in_progress')
    ddb_client.record_step_complete('wf_1', 'segment_analyzer', segment_count=3)
    (call,) = table.update_calls
    assert '#data.#step' in call['UpdateExpression']
    assert 'GSI1SK = :gsi1sk' in call['UpdateExpression']
    assert call['ExpressionAttributeNames']['#step'] == 'segment_analyzer'
    assert call['ExpressionAttributeValues'][':gsi1sk'] == 'completed'
    assert call['ExpressionAttributeValues'][':cs'] == ''
    assert table.items[('WF#wf_1', 'STEP')]['GSI1SK'] == 'completed'


def test_record_step_start_and_error(table):
    table.items[('WF#wf_1', 'STEP')] = _steps_item()
    ddb_client.record_step_start('wf_1', 'segment_analyzer')
    start = table.update_calls[-1]
    assert '#data.#step' in start['UpdateExpression']
    assert start['ExpressionAttributeValues'][':cs'] == 'segment_analyzer'
    assert start['ExpressionAttributeValues'][':gsi1sk'] == 'in_progress'
    assert start['ExpressionAttributeValues'][':step']['status'] == 'in_progress'

    ddb_client.record_step_error('wf_1', 'document_facts', 'boom')
    err = table.update_calls[-1]
    assert '#data.#step' in err['UpdateExpression'] and 'GSI1SK' not in err['UpdateExpression']
    assert err['ExpressionAttributeNames']['#step'] == 'document_facts'
    assert err['ExpressionAttributeValues'][':cs'] == ''
    assert err['ExpressionAttributeValues'][':step']['status'] == 'failed'
    assert err['ExpressionAttributeValues'][':step']['error'] == 'boom'

    ddb_client.record_step_error('wf_1', 'segment_analyzer', 'boom')
    assert table.update_calls[-1]['ExpressionAttributeValues'][':gsi1sk'] == 'failed'


def test_record_step_start_creates_missing_step_with_label(table):
    item = _steps_item()
    del item['data']['document_facts']  # workflow created before this step existed
    table.items[('WF#wf_1', 'STEP')] = item
    ddb_client.record_step_start('wf_1', 'document_facts')
    step = table.items[('WF#wf_1', 'STEP')]['data']['document_facts']
    assert step['status'] == 'in_progress' and step['label'] == 'Document Facts'
    assert table.items[('WF#wf_1', 'STEP')]['data']['current_step'] == 'document_facts'


def test_record_step_skipped_leaves_current_step(table):
    item = _steps_item(graph_builder='in_progress')
    item['data']['current_step'] = 'graph_builder'
    table.items[('WF#wf_1', 'STEP')] = item
    ddb_client.record_step_skipped('wf_1', 'graph_builder', reason='no entities')
    (call,) = table.update_calls
    assert '#data.#step' in call['UpdateExpression']
    assert 'current_step' not in call['UpdateExpression']
    assert call['ExpressionAttributeValues'][':step']['status'] == 'skipped'
    assert call['ExpressionAttributeValues'][':step']['reason'] == 'no entities'


def test_missing_step_row_returns_empty(table):
    assert ddb_client.record_step_complete('wf_missing', 'document_facts') == {}
    assert table.update_calls == []


def test_parallel_branches_do_not_lose_updates(table, monkeypatch):
    """Three branches read the same STEP snapshot, then write: every update must survive."""
    table.items[('WF#wf_1', 'STEP')] = _steps_item(
        segment_analyzer='completed', graph_builder='in_progress',
        document_summarizer='in_progress', document_facts='in_progress')
    stale = copy.deepcopy(table.items[('WF#wf_1', 'STEP')])
    monkeypatch.setattr(ddb_client, 'get_steps', lambda wf: copy.deepcopy(stale))

    ddb_client.record_step_complete('wf_1', 'graph_builder')
    ddb_client.record_step_complete('wf_1', 'document_summarizer')
    ddb_client.record_step_error('wf_1', 'document_facts', 'boom')

    data = table.items[('WF#wf_1', 'STEP')]['data']
    assert data['graph_builder']['status'] == 'completed'
    assert data['document_summarizer']['status'] == 'completed'
    assert data['document_facts']['status'] == 'failed'


# --------------------------------------------------------------------------- #
# create_workflow skip conditions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('file_type,expected', [
    ('application/pdf', 'pending'),
    ('image/png', 'pending'),
    ('text/plain', 'pending'),
    ('video/mp4', 'skipped'),
    ('audio/mpeg', 'skipped'),
    ('application/x-webreq', 'skipped'),
    ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'skipped'),
])
def test_create_workflow_document_facts_status(table, file_type, expected):
    ddb_client.create_workflow('wf_1', 'd1', 'p1', 's3://b/projects/p1/documents/d1/d1.bin', 'x.bin',
                               file_type, 'arn:exec')
    steps = table.items[('WF#wf_1', 'STEP')]['data']
    assert steps['document_facts'] == {'status': expected, 'label': 'Document Facts'}


# --------------------------------------------------------------------------- #
# S3: analysis/facts.json
# --------------------------------------------------------------------------- #
class FakeS3:
    class exceptions:  # noqa: N801 - mirrors boto3 client.exceptions
        class NoSuchKey(Exception):
            pass

    def __init__(self):
        self.objects = {}
        self.put_calls = []
        self.fail_get = None

    def put_object(self, Bucket, Key, Body, ContentType):
        self.put_calls.append({'Bucket': Bucket, 'Key': Key, 'Body': Body, 'ContentType': ContentType})
        self.objects[(Bucket, Key)] = Body.encode('utf-8')
        return {}

    def get_object(self, Bucket, Key):
        if self.fail_get:
            raise self.fail_get
        if (Bucket, Key) not in self.objects:
            raise self.exceptions.NoSuchKey('missing')
        return {'Body': io.BytesIO(self.objects[(Bucket, Key)])}


@pytest.fixture
def s3(monkeypatch):
    client = FakeS3()
    monkeypatch.setattr(s3_analysis, 'get_s3_client', lambda: client)
    return client


def test_get_facts_s3_key():
    assert s3_analysis.get_facts_s3_key('s3://b/projects/p/documents/d/d.pdf') == \
        'projects/p/documents/d/analysis/facts.json'
    assert s3_analysis.get_facts_s3_key('s3://b/projects/p/documents/d/analysis/segment_0000.json') == \
        'projects/p/documents/d/analysis/facts.json'


def test_save_and_get_facts(s3):
    key = s3_analysis.save_facts('s3://b/projects/p/documents/d/d.pdf',
                                 {'doc_type': 'salary_slip', 'note': 'model read ₹82,500'})
    assert key == 'projects/p/documents/d/analysis/facts.json'
    (call,) = s3.put_calls
    assert call['Bucket'] == 'b'
    assert call['ContentType'] == 'application/json'
    assert '₹82,500' in call['Body'], 'ensure_ascii=False'
    assert call['Body'].startswith('{\n  "doc_type"'), 'indent=2'
    assert s3_analysis.get_facts('s3://b/projects/p/documents/d/d.pdf') == \
        {'doc_type': 'salary_slip', 'note': 'model read ₹82,500'}


def test_get_facts_missing_or_error_is_none(s3, capsys):
    assert s3_analysis.get_facts('s3://b/projects/p/documents/none/none.pdf') is None
    s3.fail_get = RuntimeError('AccessDenied')
    assert s3_analysis.get_facts('s3://b/projects/p/documents/d/d.pdf') is None
    assert 'Error getting facts' in capsys.readouterr().out
