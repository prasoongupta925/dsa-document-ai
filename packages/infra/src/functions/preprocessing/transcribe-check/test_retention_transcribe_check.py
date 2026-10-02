"""Retention tests for transcribe-check: finished jobs are deleted from Transcribe.

No AWS calls: the Transcribe client and all DynamoDB/S3 helpers are faked.

Usage:
    python -m pytest -q test_retention_transcribe_check.py
"""
import importlib.util
import os
import sys

import pytest

os.environ.setdefault('AWS_DEFAULT_REGION', 'ap-south-1')
os.environ.setdefault('AWS_REGION', 'ap-south-1')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'testing')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'testing')
os.environ.setdefault('AWS_SESSION_TOKEN', 'testing')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))  # functions/ (for shared/)


def _load_index():
    # Unique module name: several Lambda folders have an index.py.
    spec = importlib.util.spec_from_file_location(
        'transcribe_check_index', os.path.join(HERE, 'index.py')
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


index = _load_index()


class FakeTranscribe:
    def __init__(self, job, delete_error=None):
        self.job = job
        self.delete_error = delete_error
        self.deleted = []

    def get_transcription_job(self, TranscriptionJobName):
        return {'TranscriptionJob': dict(self.job, TranscriptionJobName=TranscriptionJobName)}

    def delete_transcription_job(self, TranscriptionJobName):
        self.deleted.append(TranscriptionJobName)
        if self.delete_error:
            raise self.delete_error


EVENT = {
    'workflow_id': 'wf_test',
    'document_id': 'doc-1',
    'project_id': 'proj-1',
    'transcribe_job_name': 'wf_test-20260901000000',
}


@pytest.fixture
def calls(monkeypatch):
    recorded = {'saved': [], 'status': [], 'complete': [], 'error': []}

    def fake_save(**kwargs):
        recorded['saved'].append(kwargs)
        return 's3://doc-bucket/projects/proj-1/documents/doc-1/transcribe/transcript.txt'

    monkeypatch.setattr(index, 'save_transcript_text', fake_save)
    monkeypatch.setattr(
        index, 'update_preprocess_status', lambda **kw: recorded['status'].append(kw)
    )
    monkeypatch.setattr(
        index, 'record_step_complete', lambda *a, **kw: recorded['complete'].append(a)
    )
    monkeypatch.setattr(
        index, 'record_step_error', lambda *a, **kw: recorded['error'].append(a)
    )
    return recorded


def _use_client(monkeypatch, client):
    monkeypatch.setattr(index, 'get_transcribe_client', lambda: client)


def test_completed_deletes_job_once(monkeypatch, calls):
    client = FakeTranscribe({
        'TranscriptionJobStatus': 'COMPLETED',
        'Transcript': {'TranscriptFileUri': 's3://doc-bucket/projects/proj-1/documents/doc-1/transcribe/x.json'},
    })
    _use_client(monkeypatch, client)

    result = index.handler(dict(EVENT), None)

    assert result['transcribe_status'] == 'COMPLETED'
    assert client.deleted == [EVENT['transcribe_job_name']]
    # Transcript saved and status recorded before the job is deleted
    assert len(calls['saved']) == 1
    assert calls['status'][0]['status'] == index.PreprocessStatus.COMPLETED
    assert len(calls['complete']) == 1


def test_delete_error_is_swallowed(monkeypatch, calls):
    client = FakeTranscribe(
        {
            'TranscriptionJobStatus': 'COMPLETED',
            'Transcript': {'TranscriptFileUri': 's3://doc-bucket/k.json'},
        },
        delete_error=RuntimeError('AccessDenied'),
    )
    _use_client(monkeypatch, client)

    result = index.handler(dict(EVENT), None)

    assert result['transcribe_status'] == 'COMPLETED'
    assert client.deleted == [EVENT['transcribe_job_name']]


def test_failed_deletes_job_and_still_raises(monkeypatch, calls):
    client = FakeTranscribe({
        'TranscriptionJobStatus': 'FAILED',
        'FailureReason': 'Unsupported media',
    })
    _use_client(monkeypatch, client)

    with pytest.raises(Exception, match='Transcription failed'):
        index.handler(dict(EVENT), None)

    assert client.deleted == [EVENT['transcribe_job_name']]
    assert calls['status'][0]['status'] == index.PreprocessStatus.FAILED
    assert len(calls['error']) == 1


def test_failed_unparseable_audio_skips_and_deletes(monkeypatch, calls):
    client = FakeTranscribe({
        'TranscriptionJobStatus': 'FAILED',
        'FailureReason': 'Failed to parse audio file',
    })
    _use_client(monkeypatch, client)

    result = index.handler(dict(EVENT), None)

    assert result['transcribe_status'] == 'SKIPPED'
    assert client.deleted == [EVENT['transcribe_job_name']]


def test_in_progress_does_not_delete(monkeypatch, calls):
    client = FakeTranscribe({'TranscriptionJobStatus': 'IN_PROGRESS'})
    _use_client(monkeypatch, client)

    result = index.handler(dict(EVENT), None)

    assert result['transcribe_status'] == 'IN_PROGRESS'
    assert client.deleted == []
