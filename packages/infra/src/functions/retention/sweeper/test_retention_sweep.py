"""Tests for the retention sweeper (sweep.py / index.py).

No AWS calls: DynamoDB, S3, Lambda, SQS and Transcribe are in-memory fakes.
All data is synthetic.

Usage (from packages/infra/src/functions/retention):
    python -m pytest -q sweeper/test_retention_sweep.py
"""
import importlib.util
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault('AWS_DEFAULT_REGION', 'ap-south-1')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'testing')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'testing')
os.environ.setdefault('AWS_SESSION_TOKEN', 'testing')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sweep  # noqa: E402
from sweep import SweepConfig, parse_ts, run_sweep  # noqa: E402


def _load_index():
    spec = importlib.util.spec_from_file_location(
        'retention_sweeper_index', os.path.join(HERE, 'index.py')
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
DOC_BUCKET = 'idp-v2-document-storage-000000000000'
SESSION_BUCKET = 'idp-v2-session-storage-000000000000'
AGENT_BUCKET = 'idp-v2-agent-storage-000000000000'
QUEUE_URL = 'https://sqs.ap-south-1.amazonaws.com/000000000000/idp-v2-graph-delete-queue'
LANCE_FN = 'arn:aws:lambda:ap-south-1:000000000000:function:idp-v2-lance-service'
SECRET_NAMES = [
    'Rahul_Vijay_Deshmukh_salary_slip.pdf',
    'Sneha_bank_statement.pdf',
    'loan_letter_final.docx',
]


def ago(days=0, hours=0):
    return NOW - timedelta(days=days, hours=hours)


def iso(dt):
    return dt.isoformat()


def js_iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class Recorder:
    """Shared, ordered log of every mutating call across all fakes."""

    def __init__(self):
        self.log = []

    def add(self, *entry):
        self.log.append(entry)


def _evaluate(condition, item):
    expr = condition.get_expression()
    op = expr['operator']
    values = expr['values']
    if op == 'AND':
        return _evaluate(values[0], item) and _evaluate(values[1], item)
    if op == 'OR':
        return _evaluate(values[0], item) or _evaluate(values[1], item)
    actual = item.get(values[0].name)
    if op == '=':
        return actual == values[1]
    if op == 'begins_with':
        return isinstance(actual, str) and actual.startswith(values[1])
    if op == '<':
        return actual is not None and actual < values[1]
    raise NotImplementedError(op)


class _BatchWriter:
    def __init__(self, table):
        self.table = table

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def delete_item(self, Key):
        self.table.delete_item(Key=Key)

    def put_item(self, Item):
        raise AssertionError('sweeper must not put items')


class FakeTable:
    def __init__(self, recorder, page_size=2):
        self.items = {}
        self.recorder = recorder
        self.page_size = page_size

    def add(self, item):
        self.items[(item['PK'], item['SK'])] = item

    def has(self, pk, sk):
        return (pk, sk) in self.items

    def _page(self, matches, start_key):
        matches.sort(key=lambda i: (i['PK'], i['SK']))
        start = 0
        if start_key:
            keys = [(i['PK'], i['SK']) for i in matches]
            start = keys.index((start_key['PK'], start_key['SK'])) + 1
        page = matches[start:start + self.page_size]
        response = {'Items': [dict(i) for i in page]}
        if start + self.page_size < len(matches):
            last = page[-1]
            response['LastEvaluatedKey'] = {'PK': last['PK'], 'SK': last['SK']}
        return response

    def query(self, KeyConditionExpression, IndexName=None, ExclusiveStartKey=None, **_):
        matches = [i for i in self.items.values() if _evaluate(KeyConditionExpression, i)]
        return self._page(matches, ExclusiveStartKey)

    def scan(self, FilterExpression=None, ExclusiveStartKey=None, **_):
        matches = [
            i for i in self.items.values()
            if FilterExpression is None or _evaluate(FilterExpression, i)
        ]
        return self._page(matches, ExclusiveStartKey)

    def delete_item(self, Key):
        self.recorder.add('ddb.delete', Key['PK'], Key['SK'])
        self.items.pop((Key['PK'], Key['SK']), None)

    def put_item(self, Item):
        raise AssertionError('sweeper must not put items')

    def batch_writer(self):
        return _BatchWriter(self)


class _Paginator:
    def __init__(self, s3, page_size):
        self.s3 = s3
        self.page_size = page_size

    def paginate(self, Bucket, Prefix=''):
        keys = sorted(k for k in self.s3.buckets.get(Bucket, {}) if k.startswith(Prefix))
        if not keys:
            yield {}
            return
        for i in range(0, len(keys), self.page_size):
            yield {
                'Contents': [
                    {'Key': k, 'LastModified': self.s3.buckets[Bucket][k]['LastModified']}
                    for k in keys[i:i + self.page_size]
                ]
            }


class FakeS3:
    def __init__(self, recorder, page_size=3):
        self.buckets = {}
        self.recorder = recorder
        self.page_size = page_size

    def put(self, bucket, key, modified=None, body=b'x'):
        self.buckets.setdefault(bucket, {})[key] = {
            'LastModified': modified or NOW,
            'Body': body,
        }

    def has(self, bucket, key):
        return key in self.buckets.get(bucket, {})

    def get_paginator(self, name):
        assert name == 'list_objects_v2'
        return _Paginator(self, self.page_size)

    def get_object(self, Bucket, Key):
        obj = self.buckets.get(Bucket, {}).get(Key)
        if obj is None:
            raise KeyError(Key)
        return {'Body': io.BytesIO(obj['Body'])}

    def delete_object(self, Bucket, Key):
        self.recorder.add('s3.delete', Bucket, Key)
        self.buckets.get(Bucket, {}).pop(Key, None)
        return {}

    def delete_objects(self, Bucket, Delete):
        assert len(Delete['Objects']) <= 1000
        for obj in Delete['Objects']:
            self.recorder.add('s3.delete', Bucket, obj['Key'])
            self.buckets.get(Bucket, {}).pop(obj['Key'], None)
        return {}

    def put_object(self, **_):
        raise AssertionError('sweeper must not put objects')


class FakeLambda:
    def __init__(self, recorder, fail_actions=(), function_error_actions=()):
        self.recorder = recorder
        self.fail_actions = set(fail_actions)
        self.function_error_actions = set(function_error_actions)
        self.calls = []

    def invoke(self, FunctionName, InvocationType, Payload):
        body = json.loads(Payload)
        self.calls.append(body)
        self.recorder.add('lambda.invoke', body['action'], json.dumps(body['params'], sort_keys=True))
        action = body['action']
        if action in self.function_error_actions:
            return {
                'FunctionError': 'Unhandled',
                'Payload': io.BytesIO(json.dumps({'errorMessage': 'crash'}).encode()),
            }
        if action in self.fail_actions:
            payload = {'statusCode': 500, 'success': False, 'error': 'lance failure'}
        else:
            payload = {'statusCode': 200, 'success': True}
        return {'Payload': io.BytesIO(json.dumps(payload).encode())}

    def actions(self):
        return [(c['action'], c['params']) for c in self.calls]


class FakeSqs:
    def __init__(self, recorder):
        self.recorder = recorder
        self.messages = []

    def send_message(self, QueueUrl, MessageBody):
        self.recorder.add('sqs.send', QueueUrl, MessageBody)
        self.messages.append(json.loads(MessageBody))
        return {'MessageId': 'm-1'}


class FakeTranscribe:
    def __init__(self, recorder, jobs=(), page_size=2):
        # jobs: list of (name, status, media_uri, completion_time)
        self.recorder = recorder
        self.jobs = {j[0]: j for j in jobs}
        self.page_size = page_size
        self.deleted = []

    def list_transcription_jobs(self, Status, MaxResults=100, NextToken=None):
        names = sorted(n for n, j in self.jobs.items() if j[1] == Status)
        start = int(NextToken or 0)
        page = names[start:start + self.page_size]
        response = {
            'TranscriptionJobSummaries': [
                {
                    'TranscriptionJobName': n,
                    'TranscriptionJobStatus': Status,
                    'CompletionTime': self.jobs[n][3],
                }
                for n in page
            ]
        }
        if start + self.page_size < len(names):
            response['NextToken'] = str(start + self.page_size)
        return response

    def get_transcription_job(self, TranscriptionJobName):
        job = self.jobs[TranscriptionJobName]
        return {
            'TranscriptionJob': {
                'TranscriptionJobName': job[0],
                'TranscriptionJobStatus': job[1],
                'Media': {'MediaFileUri': job[2]},
            }
        }

    def delete_transcription_job(self, TranscriptionJobName):
        # Jobs stay listed: real NextTokens are stable while jobs are deleted.
        self.recorder.add('transcribe.delete', TranscriptionJobName)
        self.deleted.append(TranscriptionJobName)


class World:
    def __init__(self, fail_actions=(), function_error_actions=(), jobs=()):
        self.recorder = Recorder()
        self.table = FakeTable(self.recorder)
        self.s3 = FakeS3(self.recorder)
        self.lambda_client = FakeLambda(self.recorder, fail_actions, function_error_actions)
        self.sqs = FakeSqs(self.recorder)
        self.transcribe = FakeTranscribe(self.recorder, jobs)

    def run(self, dry_run=False, retention_days=7, now=NOW, delete_transcribe_jobs=True):
        cfg = SweepConfig(
            table_name='idp-v2-backend',
            document_bucket=DOC_BUCKET,
            session_bucket=SESSION_BUCKET,
            agent_bucket=AGENT_BUCKET,
            lancedb_function=LANCE_FN,
            graph_delete_queue_url=QUEUE_URL,
            retention_days=retention_days,
            dry_run=dry_run,
            delete_transcribe_jobs=delete_transcribe_jobs,
        )
        return run_sweep(
            self.table, self.s3, self.lambda_client, self.sqs, self.transcribe, cfg, now=now
        )


# ---------------------------------------------------------------------------
# World builders (synthetic data)
# ---------------------------------------------------------------------------


def add_project(world, pid='proj-1'):
    world.table.add({
        'PK': f'PROJ#{pid}', 'SK': 'META',
        'GSI1PK': 'PROJECTS', 'GSI1SK': iso(ago(30)),
        'data': {'project_id': pid, 'name': 'Loan files', 'status': 'active'},
        'created_at': iso(ago(30)), 'updated_at': iso(ago(1)),
    })


def add_document(world, pid, did, created, name, wid=None, entity='DOC'):
    s3_key = f'projects/{pid}/documents/{did}/{did}.pdf'
    world.table.add({
        'PK': f'PROJ#{pid}', 'SK': f'DOC#{did}',
        'GSI1PK': f'PROJ#{pid}#DOC', 'GSI1SK': created,
        'data': {
            'document_id': did, 'project_id': pid, 'name': name,
            'file_type': 'application/pdf', 'status': 'completed', 's3_key': s3_key,
        },
        'created_at': created, 'updated_at': created,
    })
    prefix = f'projects/{pid}/documents/{did}/'
    for key in (s3_key, prefix + 'analysis/segment_0000.json', prefix + 'analysis/summary.json',
                prefix + 'analysis/facts.json', prefix + 'transcribe/transcript.txt'):
        world.s3.put(DOC_BUCKET, key, modified=parse_ts(created))
    if wid:
        world.table.add({
            'PK': f'{entity}#{did}', 'SK': f'WF#{wid}',
            'data': {'file_name': name, 'file_uri': f's3://{DOC_BUCKET}/{s3_key}',
                     'project_id': pid, 'status': 'completed', 'execution_arn': 'arn:x',
                     'file_type': 'application/pdf'},
            'created_at': created, 'updated_at': created,
        })
        world.table.add({'PK': f'WF#{wid}', 'SK': 'STEP', 'GSI1PK': 'STEP#ANALYSIS_STATUS',
                         'data': {}, 'created_at': created})
        world.table.add({'PK': f'WF#{wid}', 'SK': 'SEG#0000', 'data': {}, 'created_at': created})
        world.table.add({'PK': f'WF#{wid}', 'SK': 'SEG#0001', 'data': {}, 'created_at': created})
    world.table.add({
        'PK': f'PROJ#{pid}', 'SK': f'FACTS#{did}',
        'data': {'document_id': did, 'document_name': name, 'doc_type': 'salary_slip',
                 'fields': {'applicant_name': 'Rahul Vijay Deshmukh', 'pan': 'BQXPD4821K'}},
        'created_at': created, 'updated_at': created,
    })


def add_dataset(world, pid, dsid, created, source_document_id=None):
    parquet = f'projects/{pid}/datasets/{dsid}.parquet'
    reference = f'projects/{pid}/datasets/{dsid}.txt'
    world.s3.put(DOC_BUCKET, parquet)
    world.s3.put(DOC_BUCKET, reference)
    world.table.add({
        'PK': f'PROJ#{pid}', 'SK': f'DATASET#{dsid}',
        'data': {
            'dataset_id': dsid, 'project_id': pid, 'name': 'Sneha_bank_statement.pdf / Sheet1',
            'dataset_s3_uri': f's3://{DOC_BUCKET}/{parquet}',
            'reference_s3_uri': f's3://{DOC_BUCKET}/{reference}',
            'source_document_id': source_document_id,
        },
        'created_at': created, 'updated_at': created,
    })


def doc_world(**kwargs):
    """proj-1 with an 8-day-old document (doc-old) and a 2-day-old one (doc-new)."""
    world = World(**kwargs)
    add_project(world)
    add_document(world, 'proj-1', 'doc-old', iso(ago(8)), SECRET_NAMES[0], wid='wf-old')
    add_document(world, 'proj-1', 'doc-new', iso(ago(2)), SECRET_NAMES[1], wid='wf-new')
    # Recent dataset created from the OLD document: removed with its source
    add_dataset(world, 'proj-1', 'ds-old', iso(ago(2)), source_document_id='doc-old')
    return world


def mutations(world):
    return list(world.recorder.log)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def test_expired_document_full_cascade_and_doc_item_last():
    world = doc_world()
    result = world.run()

    # 1. LanceDB delete_by_workflow for the old workflow only
    assert ('delete_by_workflow', {'project_id': 'proj-1', 'workflow_id': 'wf-old'}) in world.lambda_client.actions()
    assert all(p.get('workflow_id') != 'wf-new' for _, p in world.lambda_client.actions())
    # 2. graph delete starts at the Cluster phase
    assert world.sqs.messages == [
        {'project_id': 'proj-1', 'workflow_id': 'wf-old', 'phase': 'clusters', 'batch_size': 500}
    ]
    # 3 + 4. uploaded file and whole document folder
    assert not any(k.startswith('projects/proj-1/documents/doc-old/') for k in world.s3.buckets[DOC_BUCKET])
    assert world.s3.has(DOC_BUCKET, 'projects/proj-1/documents/doc-new/doc-new.pdf')
    assert world.s3.has(DOC_BUCKET, 'projects/proj-1/documents/doc-new/analysis/facts.json')
    # 5. workflow items
    assert not world.table.has('DOC#doc-old', 'WF#wf-old')
    assert not any(pk == 'WF#wf-old' for pk, _ in world.table.items)
    assert world.table.has('DOC#doc-new', 'WF#wf-new')
    assert world.table.has('WF#wf-new', 'STEP')
    # 6. facts
    assert not world.table.has('PROJ#proj-1', 'FACTS#doc-old')
    assert world.table.has('PROJ#proj-1', 'FACTS#doc-new')
    # 7. dataset built from the document, with its files
    assert not world.table.has('PROJ#proj-1', 'DATASET#ds-old')
    assert not world.s3.has(DOC_BUCKET, 'projects/proj-1/datasets/ds-old.parquet')
    assert not world.s3.has(DOC_BUCKET, 'projects/proj-1/datasets/ds-old.txt')
    # 8. document item deleted, and LAST among everything touching doc-old
    assert not world.table.has('PROJ#proj-1', 'DOC#doc-old')
    assert world.table.has('PROJ#proj-1', 'DOC#doc-new')
    log = mutations(world)
    doc_index = log.index(('ddb.delete', 'PROJ#proj-1', 'DOC#doc-old'))
    related = [
        i for i, entry in enumerate(log)
        if any(tag in ' '.join(entry) for tag in ('doc-old', 'wf-old', 'ds-old'))
    ]
    assert max(related) == doc_index
    # project still has a document: no LanceDB table drop
    assert not any(a == 'drop_table' for a, _ in world.lambda_client.actions())
    assert world.table.has('PROJ#proj-1', 'META')

    docs = result['documents']
    assert docs['expired'] == 1 and docs['deleted'] == 1 and docs['kept_for_retry'] == 0
    assert result['facts']['deleted'] == 1
    assert result['datasets']['deleted'] == 1
    assert result['errors'] == []


def test_recent_document_is_untouched():
    world = World()
    add_project(world)
    add_document(world, 'proj-1', 'doc-new', iso(ago(2)), SECRET_NAMES[1], wid='wf-new')
    result = world.run()
    assert mutations(world) == []
    assert world.lambda_client.calls == []
    assert world.sqs.messages == []
    assert result['documents']['expired'] == 0


def test_unparseable_created_at_is_not_expired():
    world = World()
    add_project(world)
    add_document(world, 'proj-1', 'doc-x', 'not-a-date', SECRET_NAMES[0], wid='wf-x')
    world.run()
    assert world.table.has('PROJ#proj-1', 'DOC#doc-x')
    assert mutations(world) == []


@pytest.mark.parametrize('mode', ['status', 'function_error'])
def test_lancedb_failure_keeps_doc_item_for_retry(mode):
    kwargs = {'fail_actions': ['delete_by_workflow']} if mode == 'status' else {
        'function_error_actions': ['delete_by_workflow']}
    world = doc_world(**kwargs)
    result = world.run()

    # DOC# (and the workflow link the retry needs) are kept ...
    assert world.table.has('PROJ#proj-1', 'DOC#doc-old')
    assert world.table.has('DOC#doc-old', 'WF#wf-old')
    # ... everything else is gone
    assert not any(k.startswith('projects/proj-1/documents/doc-old/') for k in world.s3.buckets[DOC_BUCKET])
    assert not any(pk == 'WF#wf-old' for pk, _ in world.table.items)
    assert not world.table.has('PROJ#proj-1', 'FACTS#doc-old')
    assert not world.table.has('PROJ#proj-1', 'DATASET#ds-old')
    assert world.sqs.messages[0]['workflow_id'] == 'wf-old'
    assert result['documents']['kept_for_retry'] == 1
    assert result['documents']['deleted'] == 0
    assert any('delete_by_workflow failed' in e and 'document=doc-old' in e for e in result['errors'])

    # Next day LanceDB works again: the retry completes the delete
    world.lambda_client.fail_actions.clear()
    world.lambda_client.function_error_actions.clear()
    retry = world.run(now=NOW + timedelta(days=1))
    assert not world.table.has('PROJ#proj-1', 'DOC#doc-old')
    assert not world.table.has('DOC#doc-old', 'WF#wf-old')
    assert retry['documents']['deleted'] == 1
    assert ('delete_by_workflow', {'project_id': 'proj-1', 'workflow_id': 'wf-old'}) in world.lambda_client.actions()


def test_emptied_project_drops_lancedb_tables_and_keeps_meta():
    world = World()
    add_project(world)
    add_document(world, 'proj-1', 'doc-old', iso(ago(8)), SECRET_NAMES[0], wid='wf-old')
    result = world.run()

    actions = world.lambda_client.actions()
    assert ('drop_table', {'project_id': 'proj-1'}) in actions
    assert ('drop_table', {'project_id': 'proj-1_datasets'}) in actions
    assert ('delete_graph_keywords_by_project_id', {'project_id': 'proj-1'}) in actions
    # drops happen after the document delete
    log = mutations(world)
    doc_index = log.index(('ddb.delete', 'PROJ#proj-1', 'DOC#doc-old'))
    drop_indexes = [i for i, e in enumerate(log) if e[0] == 'lambda.invoke' and e[1] != 'delete_by_workflow']
    assert drop_indexes and min(drop_indexes) > doc_index
    assert world.table.has('PROJ#proj-1', 'META')
    assert result['projects']['emptied'] == 1


def test_orphan_facts_and_old_datasets():
    world = World()
    add_project(world)
    add_document(world, 'proj-1', 'doc-new', iso(ago(1)), SECRET_NAMES[1])
    world.table.add({'PK': 'PROJ#proj-1', 'SK': 'FACTS#doc-gone', 'data': {},
                     'created_at': iso(ago(9)), 'updated_at': iso(ago(9))})
    world.table.add({'PK': 'PROJ#proj-1', 'SK': 'FACTS#doc-gone-recent', 'data': {},
                     'created_at': iso(ago(1)), 'updated_at': iso(ago(1))})
    add_dataset(world, 'proj-1', 'ds-ancient', iso(ago(10)))
    add_dataset(world, 'proj-1', 'ds-recent', iso(ago(1)))
    result = world.run()

    assert not world.table.has('PROJ#proj-1', 'FACTS#doc-gone')
    assert world.table.has('PROJ#proj-1', 'FACTS#doc-gone-recent')
    assert world.table.has('PROJ#proj-1', 'FACTS#doc-new')
    assert not world.table.has('PROJ#proj-1', 'DATASET#ds-ancient')
    assert not world.s3.has(DOC_BUCKET, 'projects/proj-1/datasets/ds-ancient.parquet')
    assert world.table.has('PROJ#proj-1', 'DATASET#ds-recent')
    assert world.s3.has(DOC_BUCKET, 'projects/proj-1/datasets/ds-recent.parquet')
    assert result['facts']['orphans_deleted'] == 1
    assert result['datasets']['deleted'] == 1


def test_web_document_workflow_is_found():
    world = World()
    add_project(world)
    add_document(world, 'proj-1', 'doc-web', iso(ago(8)), 'https://example.com', wid='wf-web', entity='WEB')
    add_document(world, 'proj-1', 'doc-keep', iso(ago(1)), SECRET_NAMES[1])
    world.run()
    assert ('delete_by_workflow', {'project_id': 'proj-1', 'workflow_id': 'wf-web'}) in world.lambda_client.actions()
    assert not world.table.has('WEB#doc-web', 'WF#wf-web')
    assert not world.table.has('PROJ#proj-1', 'DOC#doc-web')


def test_many_projects_are_paginated():
    world = World()
    for n in range(5):
        add_project(world, f'proj-{n}')
        add_document(world, f'proj-{n}', f'doc-{n}', iso(ago(8)), SECRET_NAMES[0], wid=f'wf-{n}')
    result = world.run()
    assert result['projects']['scanned'] == 5
    assert result['documents']['deleted'] == 5
    assert result['projects']['emptied'] == 5


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def add_session(world, sid, created, modified, with_session_json=True):
    prefix = f'sessions/user-1/proj-1/session_{sid}/'
    if with_session_json:
        world.s3.put(SESSION_BUCKET, prefix + 'session.json', modified=modified,
                     body=json.dumps({'session_id': sid, 'session_type': 'AGENT',
                                      'created_at': iso(created), 'updated_at': iso(modified),
                                      'session_name': 'Rahul loan file chat'}).encode())
    world.s3.put(SESSION_BUCKET, prefix + 'agents/agent_default/agent.json', modified=modified)
    world.s3.put(SESSION_BUCKET, prefix + 'agents/agent_default/messages/message_0.json', modified=modified)
    world.s3.put(SESSION_BUCKET, prefix + 'agents/agent_default/messages/message_1.json', modified=modified)
    return prefix


def test_sessions_older_than_cutoff_are_deleted():
    world = World()
    old = add_session(world, 'old', created=ago(9), modified=ago(1))  # still being written to
    new = add_session(world, 'new', created=ago(1), modified=ago(1))
    no_json = add_session(world, 'nojson', created=ago(10), modified=ago(10), with_session_json=False)
    no_json_new = add_session(world, 'nojsonnew', created=ago(1), modified=ago(1), with_session_json=False)
    result = world.run()

    keys = world.s3.buckets[SESSION_BUCKET]
    assert not any(k.startswith(old) for k in keys)
    assert not any(k.startswith(no_json) for k in keys)
    assert any(k.startswith(new) for k in keys)
    assert any(k.startswith(no_json_new) for k in keys)
    assert result['sessions']['expired'] == 2
    assert result['sessions']['s3_objects_deleted'] == 4 + 3


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def artifact_world():
    world = World()
    for art, created in (('art_old', ago(8)), ('art_new', ago(1))):
        key = f'user-1/proj-1/artifacts/{art}/{SECRET_NAMES[2]}'
        world.table.add({
            'PK': f'ART#{art}', 'SK': 'META', 'artifact_id': art,
            'GSI1PK': 'USR#user-1#ART', 'GSI1SK': js_iso(created),
            'data': {'filename': SECRET_NAMES[2], 's3_bucket': AGENT_BUCKET, 's3_key': key,
                     'user_id': 'user-1', 'project_id': 'proj-1', 'content_type': 'x', 'file_size': 1},
            'created_at': js_iso(created),
        })
        world.s3.put(AGENT_BUCKET, key, modified=created)
        world.s3.put(AGENT_BUCKET, f'user-1/proj-1/artifacts/{art}/preview.png', modified=created)
    # Stray artifact files (no ART# item), old and new
    world.s3.put(AGENT_BUCKET, 'user-1/proj-1/artifacts/art_orphan/chart.png', modified=ago(10))
    world.s3.put(AGENT_BUCKET, 'user-1/proj-1/artifacts/art_flat.pptx', modified=ago(10))
    world.s3.put(AGENT_BUCKET, 'user-1/proj-1/artifacts/art_fresh/chart.png', modified=ago(1))
    # App configuration: never deleted, however old
    world.s3.put(AGENT_BUCKET, '__prompts/analysis/system_prompt.md', modified=ago(300))
    world.s3.put(AGENT_BUCKET, '__prompts/proj/artifacts/x/y.md', modified=ago(300))
    world.s3.put(AGENT_BUCKET, '__prompts/builtin_agents/builtin-file-checker.json', modified=ago(300))
    world.s3.put(AGENT_BUCKET, 'user-1/proj-1/agents/credit_checker.md', modified=ago(300))
    return world


def test_artifacts_expire_but_prompts_and_agents_never():
    world = artifact_world()
    result = world.run()
    keys = world.s3.buckets[AGENT_BUCKET]

    assert not world.table.has('ART#art_old', 'META')
    assert not any(k.startswith('user-1/proj-1/artifacts/art_old/') for k in keys)
    assert world.table.has('ART#art_new', 'META')
    assert any(k.startswith('user-1/proj-1/artifacts/art_new/') for k in keys)
    assert 'user-1/proj-1/artifacts/art_orphan/chart.png' not in keys
    assert 'user-1/proj-1/artifacts/art_flat.pptx' not in keys
    assert 'user-1/proj-1/artifacts/art_fresh/chart.png' in keys
    assert '__prompts/analysis/system_prompt.md' in keys
    assert '__prompts/proj/artifacts/x/y.md' in keys
    assert '__prompts/builtin_agents/builtin-file-checker.json' in keys  # built-in agents
    assert 'user-1/proj-1/agents/credit_checker.md' in keys
    assert not any(e[2].startswith('__prompts/') or '/agents/' in e[2]
                   for e in mutations(world) if e[0] == 's3.delete')
    assert result['artifacts']['items_deleted'] == 1
    assert result['artifacts']['s3_objects_deleted'] == 2
    assert result['artifacts']['orphan_objects_deleted'] == 2


# ---------------------------------------------------------------------------
# Transcribe
# ---------------------------------------------------------------------------


JOBS = [
    ('job-a', 'COMPLETED', f's3://{DOC_BUCKET}/projects/proj-1/documents/d1/d1.mp3', ago(2)),
    ('job-b', 'COMPLETED', 's3://someone-elses-bucket/audio.mp3', ago(2)),
    ('job-c', 'FAILED', f's3://{DOC_BUCKET}/projects/proj-1/documents/d2/d2.mp4', ago(3)),
    ('job-d', 'COMPLETED', f's3://{DOC_BUCKET}/projects/proj-1/documents/d3/d3.mp3', ago(hours=0.1)),
    ('job-e', 'COMPLETED', f'https://{DOC_BUCKET}.s3.ap-south-1.amazonaws.com/x.wav', ago(1)),
    ('job-f', 'IN_PROGRESS', f's3://{DOC_BUCKET}/projects/proj-1/documents/d4/d4.mp3', None),
]


def test_only_document_bucket_transcribe_jobs_are_deleted():
    world = World(jobs=JOBS)
    result = world.run()
    assert sorted(world.transcribe.deleted) == ['job-a', 'job-c', 'job-e']
    counts = result['transcribe_jobs']
    assert counts['checked'] == 5  # IN_PROGRESS jobs are never listed
    assert counts['deleted'] == 3
    assert counts['skipped_recent'] == 1  # job-d finished minutes ago


def test_transcribe_sweep_can_be_disabled():
    world = World(jobs=JOBS)
    world.run(delete_transcribe_jobs=False)
    assert world.transcribe.deleted == []


# ---------------------------------------------------------------------------
# Dry run, isolation, privacy
# ---------------------------------------------------------------------------


def full_world():
    world = artifact_world()
    world.transcribe = FakeTranscribe(world.recorder, JOBS)
    add_project(world)
    add_document(world, 'proj-1', 'doc-old', iso(ago(8)), SECRET_NAMES[0], wid='wf-old')
    add_dataset(world, 'proj-1', 'ds-old', iso(ago(2)), source_document_id='doc-old')
    world.table.add({'PK': 'PROJ#proj-1', 'SK': 'FACTS#doc-gone', 'data': {},
                     'created_at': iso(ago(9))})
    add_session(world, 'old', created=ago(9), modified=ago(9))
    return world


def test_dry_run_makes_no_mutating_calls_but_counts():
    world = full_world()
    before_items = dict(world.table.items)
    before_objects = {b: dict(o) for b, o in world.s3.buckets.items()}
    result = world.run(dry_run=True)

    assert mutations(world) == []
    assert world.lambda_client.calls == []
    assert world.sqs.messages == []
    assert world.transcribe.deleted == []
    assert world.table.items == before_items
    assert world.s3.buckets == before_objects
    assert result['dry_run'] is True
    assert result['documents']['deleted'] == 1
    assert result['projects']['emptied'] == 1
    assert result['sessions']['expired'] == 1
    assert result['artifacts']['items_deleted'] == 1
    assert result['artifacts']['orphan_objects_deleted'] == 2
    assert result['transcribe_jobs']['deleted'] == 3
    assert result['facts']['orphans_deleted'] == 1


def test_result_contains_no_names_or_personal_data():
    world = full_world()
    result = world.run()
    text = json.dumps(result)
    for secret in SECRET_NAMES + ['Rahul', 'BQXPD4821K', 'Sneha', 'user-1', 'loan file chat']:
        assert secret not in text
    assert result['errors'] == []


def test_one_failing_step_does_not_stop_the_others():
    world = full_world()

    def broken_query(**_):
        raise RuntimeError('ddb down')

    world.table.query = broken_query
    result = world.run()
    assert any(e.startswith('documents:') for e in result['errors'])
    # sessions, artifacts and transcribe still ran
    assert result['sessions']['expired'] == 1
    assert result['artifacts']['orphan_objects_deleted'] == 2
    assert result['transcribe_jobs']['deleted'] == 3


def test_stops_early_when_lambda_time_runs_out():
    world = full_world()
    cfg = SweepConfig(DOC_BUCKET, DOC_BUCKET, SESSION_BUCKET, AGENT_BUCKET, LANCE_FN, QUEUE_URL)
    result = run_sweep(world.table, world.s3, world.lambda_client, world.sqs, world.transcribe,
                       cfg, now=NOW, time_left_ms=lambda: 1_000)
    assert result['stopped_early'] is True
    assert mutations(world) == []


def test_retention_days_moves_the_cutoff():
    world = doc_world()
    world.run(retention_days=30)
    assert world.table.has('PROJ#proj-1', 'DOC#doc-old')
    world.run(retention_days=1)
    assert not world.table.has('PROJ#proj-1', 'DOC#doc-new')


def test_parse_ts_accepts_z_and_offsets():
    assert parse_ts('2026-09-01T00:00:00Z') == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert parse_ts('2026-09-01T05:30:00+05:30') == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert parse_ts('2026-09-01T00:00:00') == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert parse_ts('garbage') is None
    assert parse_ts(None) is None
    assert parse_ts('') is None
    assert sweep.is_expired('garbage', NOW) is False


# ---------------------------------------------------------------------------
# index.py (no AWS calls: run_sweep is replaced)
# ---------------------------------------------------------------------------


ENV = {
    'BACKEND_TABLE_NAME': 'idp-v2-backend',
    'DOCUMENT_STORAGE_BUCKET_NAME': DOC_BUCKET,
    'SESSION_STORAGE_BUCKET_NAME': SESSION_BUCKET,
    'AGENT_STORAGE_BUCKET_NAME': AGENT_BUCKET,
    'LANCEDB_FUNCTION_NAME': LANCE_FN,
    'GRAPH_DELETE_QUEUE_URL': QUEUE_URL,
}


def test_load_config_defaults_and_missing_env():
    index = _load_index()
    cfg = index.load_config(dict(ENV))
    assert cfg.retention_days == 7 and cfg.dry_run is False and cfg.delete_transcribe_jobs is True
    cfg = index.load_config({**ENV, 'RETENTION_DAYS': '3', 'DRY_RUN': 'true', 'DELETE_TRANSCRIBE_JOBS': 'false'})
    assert cfg.retention_days == 3 and cfg.dry_run is True and cfg.delete_transcribe_jobs is False
    with pytest.raises(RuntimeError, match='GRAPH_DELETE_QUEUE_URL'):
        index.load_config({k: v for k, v in ENV.items() if k != 'GRAPH_DELETE_QUEUE_URL'})
    with pytest.raises(RuntimeError, match='RETENTION_DAYS'):
        index.load_config({**ENV, 'RETENTION_DAYS': '0'})


def test_handler_raises_on_missing_env(monkeypatch):
    index = _load_index()
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError):
        index.handler({}, None)


def test_handler_event_can_only_force_dry_run(monkeypatch):
    index = _load_index()
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('DRY_RUN', 'false')
    seen = []

    def fake_run_sweep(table, s3, lambda_client, sqs, transcribe, cfg, time_left_ms=None):
        seen.append(cfg.dry_run)
        return {'errors': []}

    monkeypatch.setattr(index, 'run_sweep', fake_run_sweep)
    assert index.handler({'dry_run': True}, None) == {'errors': []}
    monkeypatch.setenv('DRY_RUN', 'true')
    index.handler({'dry_run': False}, None)
    assert seen == [True, True]
