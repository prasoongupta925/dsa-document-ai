"""Retention sweep: delete client data older than the retention window.

Pure logic. Every AWS client is injected (see index.py), so tests use fakes.

Order of work (each step is isolated, so one failure does not stop the others):
  1. sweep_documents       - documents older than the cutoff, deleted with the
                             SAME cascade as the backend's delete_document, plus
                             their facts/datasets; orphan FACTS#/old DATASET#
                             items; LanceDB tables of projects left empty.
  2. sweep_sessions        - chat session folders in the session bucket.
  3. sweep_artifacts       - ART# items + their files, and stray artifact files
                             in the agent bucket (never __prompts/ or agents/).
  4. sweep_transcribe_jobs - finished Amazon Transcribe jobs of our documents.

The result holds counts and error summaries only: no document names, file
names or other personal data.
"""
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from boto3.dynamodb.conditions import Attr, Key

GRAPH_DELETE_FIRST_PHASE = 'clusters'  # PHASE_ORDER[0] in graph-delete-consumer
GRAPH_DELETE_BATCH_SIZE = 500
S3_DELETE_BATCH = 1000
MAX_ERRORS = 50
# Stop starting new work when less than this is left of the Lambda timeout.
STOP_MARGIN_MS = 60_000
# A job that finished very recently may still be read by transcribe-check.
TRANSCRIBE_JOB_GRACE = timedelta(hours=1)

SESSION_FOLDER_RE = re.compile(r'^(sessions/[^/]+/[^/]+/session_[^/]+/)')
# {user_id}/{project_id}/artifacts/{artifact_id}/{file} (and flat
# {user_id}/{project_id}/artifacts/{file} written by the research agent).
ARTIFACT_KEY_RE = re.compile(r'^[^/]+/[^/]+/artifacts/[^/]+')
ARTIFACT_PREFIX_RE = re.compile(r'^[^/]+/[^/]+/artifacts/[^/]+/$')
PROTECTED_PREFIX = '__prompts/'
PROTECTED_SEGMENT = '/agents/'


@dataclass
class SweepConfig:
    table_name: str
    document_bucket: str
    session_bucket: str
    agent_bucket: str
    lancedb_function: str
    graph_delete_queue_url: str
    retention_days: int = 7
    dry_run: bool = False
    delete_transcribe_jobs: bool = True


class LanceDbError(Exception):
    pass


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def parse_ts(value):
    """Parse an ISO-8601 timestamp (accepts 'Z'); naive values are UTC.

    Returns None when the value cannot be parsed.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text[-1] in 'Zz':
            text = text[:-1] + '+00:00'
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_expired(value, cutoff) -> bool:
    """True only when value parses and is older than cutoff."""
    dt = parse_ts(value)
    return dt is not None and dt < cutoff


def compute_cutoff(retention_days: int, now=None) -> datetime:
    now = parse_ts(now) if now is not None else datetime.now(timezone.utc)
    return now - timedelta(days=retention_days)


# ---------------------------------------------------------------------------
# Run context
# ---------------------------------------------------------------------------


class _Run:
    def __init__(self, table, s3, lambda_client, sqs, transcribe, cfg, cutoff, now, time_left_ms):
        self.table = table
        self.s3 = s3
        self.lambda_client = lambda_client
        self.sqs = sqs
        self.transcribe = transcribe
        self.cfg = cfg
        self.cutoff = cutoff
        self.now = now
        self.dry_run = bool(cfg.dry_run)
        self._time_left_ms = time_left_ms
        self.result = _new_result(cfg, cutoff)

    def out_of_time(self) -> bool:
        if self._time_left_ms is None:
            return False
        try:
            left = self._time_left_ms()
        except Exception:
            return False
        if left is not None and left < STOP_MARGIN_MS:
            self.result['stopped_early'] = True
            return True
        return False

    def error(self, stage: str, detail: str, exc: Exception = None) -> None:
        """Record an error summary (ids and exception class only, never names)."""
        self.result['errors_total'] += 1
        if len(self.result['errors']) >= MAX_ERRORS:
            return
        text = f'{stage}: {detail}'
        if exc is not None:
            text += f' ({type(exc).__name__})'
        self.result['errors'].append(text)


def _new_result(cfg, cutoff) -> dict:
    return {
        'retention_days': cfg.retention_days,
        'dry_run': bool(cfg.dry_run),
        'cutoff': cutoff.isoformat(),
        'stopped_early': False,
        'projects': {'scanned': 0, 'emptied': 0},
        'documents': {
            'scanned': 0,
            'expired': 0,
            'deleted': 0,
            'kept_for_retry': 0,
            'workflows': 0,
            'lancedb_deleted': 0,
            'graph_delete_queued': 0,
            's3_objects_deleted': 0,
            'ddb_items_deleted': 0,
        },
        'facts': {'deleted': 0, 'orphans_deleted': 0},
        'datasets': {'deleted': 0, 's3_objects_deleted': 0},
        'sessions': {'scanned': 0, 'expired': 0, 's3_objects_deleted': 0},
        'artifacts': {
            'items_deleted': 0,
            's3_objects_deleted': 0,
            'orphan_objects_deleted': 0,
        },
        'transcribe_jobs': {'checked': 0, 'deleted': 0, 'skipped_recent': 0},
        'errors': [],
        'errors_total': 0,
    }


# ---------------------------------------------------------------------------
# AWS helpers
# ---------------------------------------------------------------------------


def _query_all(table, **kwargs) -> list:
    items = []
    while True:
        response = table.query(**kwargs)
        items.extend(response.get('Items', []))
        last_key = response.get('LastEvaluatedKey')
        if not last_key:
            return items
        kwargs['ExclusiveStartKey'] = last_key


def _scan_all(table, **kwargs):
    while True:
        response = table.scan(**kwargs)
        yield from response.get('Items', [])
        last_key = response.get('LastEvaluatedKey')
        if not last_key:
            return
        kwargs['ExclusiveStartKey'] = last_key


def _list_objects(s3, bucket: str, prefix: str = ''):
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        yield from page.get('Contents', []) or []


def _delete_keys(run: _Run, bucket: str, keys, stage: str) -> int:
    """Delete keys in batches of 1000. Returns the number deleted."""
    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys:
        return 0
    if run.dry_run:
        return len(keys)
    deleted = 0
    for i in range(0, len(keys), S3_DELETE_BATCH):
        batch = keys[i:i + S3_DELETE_BATCH]
        try:
            response = run.s3.delete_objects(
                Bucket=bucket,
                Delete={'Objects': [{'Key': k} for k in batch], 'Quiet': True},
            )
        except Exception as e:
            run.error(stage, f'delete_objects failed for {len(batch)} object(s)', e)
            continue
        errors = response.get('Errors') or []
        deleted += len(batch) - len(errors)
        if errors:
            run.error(stage, f'{len(errors)} object(s) could not be deleted')
    return deleted


def _delete_prefix(run: _Run, bucket: str, prefix: str, stage: str) -> int:
    if not prefix or not prefix.endswith('/'):
        raise ValueError('refusing to delete an empty or non-folder prefix')
    keys = [obj['Key'] for obj in _list_objects(run.s3, bucket, prefix)]
    return _delete_keys(run, bucket, keys, stage)


def _delete_object(run: _Run, bucket: str, key: str) -> int:
    if not key:
        return 0
    if not run.dry_run:
        run.s3.delete_object(Bucket=bucket, Key=key)
    return 1


def _delete_item(run: _Run, pk: str, sk: str) -> int:
    if not run.dry_run:
        run.table.delete_item(Key={'PK': pk, 'SK': sk})
    return 1


def _invoke_lancedb(run: _Run, action: str, params: dict) -> None:
    """Invoke the LanceDB service like the backend does; raise on failure."""
    if run.dry_run:
        return
    response = run.lambda_client.invoke(
        FunctionName=run.cfg.lancedb_function,
        InvocationType='RequestResponse',
        Payload=json.dumps({'action': action, 'params': params}),
    )
    raw = response['Payload'].read()
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {}
    if response.get('FunctionError'):
        raise LanceDbError(f'{action}: FunctionError')
    status = payload.get('statusCode') if isinstance(payload, dict) else None
    if status != 200:
        raise LanceDbError(f'{action}: statusCode={status}')


def _send_graph_delete(run: _Run, project_id: str, workflow_id: str) -> None:
    if run.dry_run:
        return
    run.sqs.send_message(
        QueueUrl=run.cfg.graph_delete_queue_url,
        MessageBody=json.dumps({
            'project_id': project_id,
            'workflow_id': workflow_id,
            'phase': GRAPH_DELETE_FIRST_PHASE,
            'batch_size': GRAPH_DELETE_BATCH_SIZE,
        }),
    )


def _parse_s3_uri(uri):
    if not isinstance(uri, str) or not uri.startswith('s3://'):
        return None, None
    bucket, _, key = uri[len('s3://'):].partition('/')
    return bucket, key


# ---------------------------------------------------------------------------
# 1. Documents
# ---------------------------------------------------------------------------


def _find_workflows(run: _Run, document_id: str) -> list:
    """Workflow link items DOC#{did}/WF# and WEB#{did}/WF# (backend query_workflows)."""
    links = []
    for entity in ('DOC', 'WEB'):
        items = _query_all(
            run.table,
            KeyConditionExpression=Key('PK').eq(f'{entity}#{document_id}')
            & Key('SK').begins_with('WF#'),
        )
        for item in items:
            links.append({
                'PK': item['PK'],
                'SK': item['SK'],
                'workflow_id': item['SK'][len('WF#'):],
            })
    return links


def _delete_dataset(run: _Run, project_id: str, item: dict) -> None:
    """Delete a PROJ#/DATASET# item and its Parquet/reference files."""
    data = item.get('data') or {}
    counts = run.result['datasets']
    for field in ('dataset_s3_uri', 'reference_s3_uri'):
        bucket, key = _parse_s3_uri(data.get(field))
        if bucket == run.cfg.document_bucket and key:
            try:
                counts['s3_objects_deleted'] += _delete_object(run, bucket, key)
            except Exception as e:
                run.error('datasets', f'delete {field} failed for project={project_id}', e)
    counts['deleted'] += _delete_item(run, item['PK'], item['SK'])


def delete_document_cascade(
    run: _Run, project_id: str, doc_item: dict, datasets: list, facts_ids=None
) -> bool:
    """Delete one document exactly like the backend's delete_document.

    Returns True when the PROJ#/DOC# item was deleted (or would be, in dry
    run). The DOC# item and the workflow link of a workflow whose LanceDB
    delete failed are kept, so the next run retries that delete.
    """
    cfg = run.cfg
    counts = run.result['documents']
    document_id = doc_item['SK'][len('DOC#'):]
    data = doc_item.get('data') or {}
    ids = f'project={project_id} document={document_id}'

    try:
        workflows = _find_workflows(run, document_id)
    except Exception as e:
        run.error('documents', f'workflow lookup failed for {ids}', e)
        counts['kept_for_retry'] += 1
        return False
    counts['workflows'] += len(workflows)

    # 1. LanceDB delete_by_workflow (must succeed for DOC# to be deleted)
    failed_workflows = set()
    for wf in workflows:
        try:
            _invoke_lancedb(run, 'delete_by_workflow', {
                'project_id': project_id,
                'workflow_id': wf['workflow_id'],
            })
            counts['lancedb_deleted'] += 1
        except Exception as e:
            failed_workflows.add(wf['workflow_id'])
            run.error('documents', f"delete_by_workflow failed for {ids} workflow={wf['workflow_id']}", e)

    # 2. Graph delete via SQS (async; starts at the Cluster phase)
    if cfg.graph_delete_queue_url:
        for wf in workflows:
            try:
                _send_graph_delete(run, project_id, wf['workflow_id'])
                counts['graph_delete_queued'] += 1
            except Exception as e:
                run.error('documents', f"graph delete queue failed for {ids} workflow={wf['workflow_id']}", e)

    # 3. Uploaded file
    s3_key = data.get('s3_key')
    if s3_key:
        try:
            counts['s3_objects_deleted'] += _delete_object(run, cfg.document_bucket, s3_key)
        except Exception as e:
            run.error('documents', f'delete file failed for {ids}', e)

    # 4. Whole document folder (derived analysis, facts.json, transcripts, ...)
    try:
        counts['s3_objects_deleted'] += _delete_prefix(
            run, cfg.document_bucket, f'projects/{project_id}/documents/{document_id}/', 'documents'
        )
    except Exception as e:
        run.error('documents', f'delete folder failed for {ids}', e)

    # 5. Workflow items: WF#{wid}/* always; the DOC#|WEB#/WF# link unless
    #    its LanceDB delete failed (the link is how the retry finds it).
    for wf in workflows:
        wid = wf['workflow_id']
        try:
            keys = [
                {'PK': item['PK'], 'SK': item['SK']}
                for item in _query_all(run.table, KeyConditionExpression=Key('PK').eq(f'WF#{wid}'))
            ]
            if wid not in failed_workflows:
                keys.append({'PK': wf['PK'], 'SK': wf['SK']})
            if keys and not run.dry_run:
                with run.table.batch_writer() as batch:
                    for key in keys:
                        batch.delete_item(Key=key)
            counts['ddb_items_deleted'] += len(keys)
        except Exception as e:
            run.error('documents', f'delete workflow items failed for {ids} workflow={wid}', e)

    # 6. Extracted document facts (deleted unconditionally, like the backend;
    #    counted when the item was seen in the project query)
    try:
        deleted = _delete_item(run, f'PROJ#{project_id}', f'FACTS#{document_id}')
        if facts_ids is None or document_id in facts_ids:
            run.result['facts']['deleted'] += deleted
    except Exception as e:
        run.error('documents', f'delete facts failed for {ids}', e)

    # 7. Datasets built from this document (xlsx/csv)
    for item in datasets:
        if (item.get('data') or {}).get('source_document_id') != document_id:
            continue
        try:
            _delete_dataset(run, project_id, item)
            item['_swept'] = True
        except Exception as e:
            run.error('datasets', f'delete dataset failed for {ids}', e)

    # 8. Document item LAST, only when every LanceDB delete succeeded
    if failed_workflows:
        counts['kept_for_retry'] += 1
        return False
    try:
        counts['ddb_items_deleted'] += _delete_item(run, doc_item['PK'], doc_item['SK'])
    except Exception as e:
        run.error('documents', f'delete document item failed for {ids}', e)
        counts['kept_for_retry'] += 1
        return False
    counts['deleted'] += 1
    return True


def _sweep_project(run: _Run, project_id: str) -> None:
    items = _query_all(run.table, KeyConditionExpression=Key('PK').eq(f'PROJ#{project_id}'))
    docs = [i for i in items if i.get('SK', '').startswith('DOC#')]
    facts = [i for i in items if i.get('SK', '').startswith('FACTS#')]
    datasets = [i for i in items if i.get('SK', '').startswith('DATASET#')]
    doc_ids = {d['SK'][len('DOC#'):] for d in docs}
    facts_ids = {f['SK'][len('FACTS#'):] for f in facts}
    counts = run.result['documents']
    counts['scanned'] += len(docs)

    remaining = len(docs)
    for doc in docs:
        if run.out_of_time():
            return
        if not is_expired(doc.get('created_at'), run.cutoff):
            continue
        counts['expired'] += 1
        if delete_document_cascade(run, project_id, doc, datasets, facts_ids):
            remaining -= 1

    # Orphan facts (document already gone) older than the cutoff
    for item in facts:
        document_id = item['SK'][len('FACTS#'):]
        if document_id in doc_ids or not is_expired(item.get('created_at'), run.cutoff):
            continue
        try:
            run.result['facts']['orphans_deleted'] += _delete_item(run, item['PK'], item['SK'])
        except Exception as e:
            run.error('documents', f'delete orphan facts failed for project={project_id}', e)

    # Any dataset older than the cutoff
    for item in datasets:
        if item.get('_swept') or not is_expired(item.get('created_at'), run.cutoff):
            continue
        try:
            _delete_dataset(run, project_id, item)
        except Exception as e:
            run.error('datasets', f'delete dataset failed for project={project_id}', e)

    # Project left without documents: drop its LanceDB data. The project META
    # (configuration) is kept. These calls are no-ops when nothing exists.
    if remaining == 0:
        run.result['projects']['emptied'] += 1
        for action, params in (
            ('drop_table', {'project_id': project_id}),
            ('drop_table', {'project_id': f'{project_id}_datasets'}),
            ('delete_graph_keywords_by_project_id', {'project_id': project_id}),
        ):
            try:
                _invoke_lancedb(run, action, params)
            except Exception as e:
                run.error('projects', f'{action} failed for project={project_id}', e)


def sweep_documents(run: _Run) -> None:
    projects = _query_all(
        run.table,
        IndexName='GSI1',
        KeyConditionExpression=Key('GSI1PK').eq('PROJECTS'),
    )
    for project in projects:
        if run.out_of_time():
            return
        pk = project.get('PK', '')
        project_id = pk[len('PROJ#'):] if pk.startswith('PROJ#') else ''
        if not project_id:
            continue
        run.result['projects']['scanned'] += 1
        try:
            _sweep_project(run, project_id)
        except Exception as e:
            run.error('documents', f'project sweep failed for project={project_id}', e)


# ---------------------------------------------------------------------------
# 2. Chat sessions
# ---------------------------------------------------------------------------


def _session_created_at(run: _Run, key: str):
    try:
        response = run.s3.get_object(Bucket=run.cfg.session_bucket, Key=key)
        body = json.loads(response['Body'].read())
        return parse_ts(body.get('created_at')) if isinstance(body, dict) else None
    except Exception:
        return None


def sweep_sessions(run: _Run) -> None:
    folders = {}
    for obj in _list_objects(run.s3, run.cfg.session_bucket, 'sessions/'):
        match = SESSION_FOLDER_RE.match(obj['Key'])
        if not match:
            continue
        folder = folders.setdefault(match.group(1), {'keys': [], 'oldest': None, 'has_session_json': False})
        folder['keys'].append(obj['Key'])
        modified = parse_ts(obj.get('LastModified'))
        if modified is not None and (folder['oldest'] is None or modified < folder['oldest']):
            folder['oldest'] = modified
        if obj['Key'] == match.group(1) + 'session.json':
            folder['has_session_json'] = True

    counts = run.result['sessions']
    for prefix, folder in folders.items():
        if run.out_of_time():
            return
        counts['scanned'] += 1
        created = None
        if folder['has_session_json']:
            created = _session_created_at(run, prefix + 'session.json')
        age_from = created or folder['oldest']
        if age_from is None or age_from >= run.cutoff:
            continue
        counts['expired'] += 1
        counts['s3_objects_deleted'] += _delete_keys(run, run.cfg.session_bucket, folder['keys'], 'sessions')


# ---------------------------------------------------------------------------
# 3. Artifacts
# ---------------------------------------------------------------------------


def _is_protected(key: str) -> bool:
    return key.startswith(PROTECTED_PREFIX) or PROTECTED_SEGMENT in key


def sweep_artifacts(run: _Run) -> None:
    cfg = run.cfg
    counts = run.result['artifacts']

    # (a) ART# metadata items older than the cutoff, with their files
    swept_prefixes = set()
    swept_keys = set()
    scan = _scan_all(
        run.table,
        FilterExpression=Attr('PK').begins_with('ART#') & Attr('created_at').lt(run.cutoff.isoformat()),
    )
    for item in scan:
        if run.out_of_time():
            return
        if not is_expired(item.get('created_at'), run.cutoff):
            continue
        data = item.get('data') or {}
        bucket = data.get('s3_bucket') or cfg.agent_bucket
        s3_key = data.get('s3_key') or ''
        artifact = item.get('PK', '')[len('ART#'):]
        if bucket != cfg.agent_bucket:
            run.error('artifacts', f'artifact={artifact} is not in the agent bucket; kept')
            continue
        try:
            if s3_key and not _is_protected(s3_key):
                prefix = s3_key.rsplit('/', 1)[0] + '/'
                if ARTIFACT_PREFIX_RE.match(prefix):
                    counts['s3_objects_deleted'] += _delete_prefix(run, bucket, prefix, 'artifacts')
                    swept_prefixes.add(prefix)
                elif ARTIFACT_KEY_RE.match(s3_key):
                    counts['s3_objects_deleted'] += _delete_object(run, bucket, s3_key)
                    swept_keys.add(s3_key)
            counts['items_deleted'] += _delete_item(run, item['PK'], item['SK'])
        except Exception as e:
            run.error('artifacts', f'delete failed for artifact={artifact}', e)

    # (b) Artifact files without (or beyond) their metadata item
    stale = []
    for obj in _list_objects(run.s3, cfg.agent_bucket, ''):
        key = obj['Key']
        if _is_protected(key) or not ARTIFACT_KEY_RE.match(key):
            continue
        if key in swept_keys or any(key.startswith(p) for p in swept_prefixes):
            continue  # already handled in (a) (only listed again in dry run)
        if is_expired(obj.get('LastModified'), run.cutoff):
            stale.append(key)
    counts['orphan_objects_deleted'] += _delete_keys(run, cfg.agent_bucket, stale, 'artifacts')


# ---------------------------------------------------------------------------
# 4. Amazon Transcribe jobs
# ---------------------------------------------------------------------------


def sweep_transcribe_jobs(run: _Run) -> None:
    counts = run.result['transcribe_jobs']
    bucket = run.cfg.document_bucket
    if not bucket:
        return
    grace_cutoff = run.now - TRANSCRIBE_JOB_GRACE
    for status in ('COMPLETED', 'FAILED'):
        kwargs = {'Status': status, 'MaxResults': 100}
        while True:
            if run.out_of_time():
                return
            response = run.transcribe.list_transcription_jobs(**kwargs)
            for summary in response.get('TranscriptionJobSummaries', []) or []:
                name = summary.get('TranscriptionJobName')
                if not name:
                    continue
                counts['checked'] += 1
                finished = parse_ts(summary.get('CompletionTime'))
                if finished is not None and finished > grace_cutoff:
                    counts['skipped_recent'] += 1
                    continue
                try:
                    job = run.transcribe.get_transcription_job(TranscriptionJobName=name)
                    uri = ((job.get('TranscriptionJob') or {}).get('Media') or {}).get('MediaFileUri') or ''
                    if bucket not in uri:
                        continue
                    if not run.dry_run:
                        run.transcribe.delete_transcription_job(TranscriptionJobName=name)
                    counts['deleted'] += 1
                except Exception as e:
                    run.error('transcribe', 'delete transcription job failed', e)
            token = response.get('NextToken')
            if not token:
                break
            kwargs['NextToken'] = token


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_sweep(table, s3, lambda_client, sqs, transcribe, cfg, now=None, time_left_ms=None) -> dict:
    """Run every sweep step; returns counts and error summaries."""
    now_dt = parse_ts(now) if now is not None else datetime.now(timezone.utc)
    cutoff = compute_cutoff(cfg.retention_days, now_dt)
    run = _Run(table, s3, lambda_client, sqs, transcribe, cfg, cutoff, now_dt, time_left_ms)

    steps = [
        ('documents', sweep_documents),
        ('sessions', sweep_sessions),
        ('artifacts', sweep_artifacts),
    ]
    if cfg.delete_transcribe_jobs:
        steps.append(('transcribe', sweep_transcribe_jobs))

    for stage, step in steps:
        if run.out_of_time():
            break
        try:
            step(run)
        except Exception as e:
            run.error(stage, 'step failed', e)
    return run.result
