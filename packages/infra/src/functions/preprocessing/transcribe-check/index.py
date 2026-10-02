"""Transcribe Check Lambda

Checks the status of a Transcribe job. On completion, downloads and saves transcript text.
Called by Step Functions polling loop.

Retention: once the job has finished (COMPLETED or FAILED) the job record is
deleted from Amazon Transcribe. The transcript JSON/text stay in the document
bucket under the document prefix, where the 7-day retention applies.
"""
import json
import os

import boto3

from shared.ddb_client import (
    update_preprocess_status,
    PreprocessStatus,
    PreprocessType,
    record_step_complete,
    record_step_error,
    StepName,
)
from shared.s3_analysis import get_s3_client, parse_s3_uri

transcribe_client = None


def get_transcribe_client():
    global transcribe_client
    if transcribe_client is None:
        transcribe_client = boto3.client(
            'transcribe',
            region_name=os.environ.get('AWS_REGION', 'us-east-1')
        )
    return transcribe_client


def _delete_job(client, job_name: str) -> None:
    """Delete a finished transcription job. Never raises."""
    if not job_name:
        return
    try:
        client.delete_transcription_job(TranscriptionJobName=job_name)
    except Exception as e:
        print(f'Warning: could not delete transcription job {job_name}: {e}')


def save_transcript_text(transcript_uri: str, project_id: str, document_id: str, workflow_id: str) -> str:
    s3_client = get_s3_client()

    if transcript_uri.startswith('https://'):
        parts = transcript_uri.replace('https://', '').split('/')
        if '.s3.' in parts[0]:
            bucket = parts[0].split('.s3.')[0]
            key = '/'.join(parts[1:])
        else:
            bucket = parts[1]
            key = '/'.join(parts[2:])
    else:
        bucket, key = parse_s3_uri(transcript_uri)

    response = s3_client.get_object(Bucket=bucket, Key=key)
    transcript_data = json.loads(response['Body'].read().decode('utf-8'))

    results = transcript_data.get('results', {})
    transcripts = results.get('transcripts', [])
    full_text = ' '.join([t.get('transcript', '') for t in transcripts])

    if document_id:
        text_key = f'projects/{project_id}/documents/{document_id}/transcribe/transcript.txt'
    else:
        text_key = f'transcribe/{workflow_id}/transcript.txt'

    s3_client.put_object(
        Bucket=bucket,
        Key=text_key,
        Body=full_text.encode('utf-8'),
        ContentType='text/plain'
    )

    return f's3://{bucket}/{text_key}'


def handler(event, context):
    print(f'Event: {json.dumps(event)}')

    workflow_id = event.get('workflow_id')
    document_id = event.get('document_id')
    project_id = event.get('project_id')
    job_name = event.get('transcribe_job_name')

    client = get_transcribe_client()
    response = client.get_transcription_job(TranscriptionJobName=job_name)

    job = response.get('TranscriptionJob', {})
    status = job.get('TranscriptionJobStatus', 'Unknown')
    print(f'Transcription status: {status}')

    if status == 'COMPLETED':
        transcript = job.get('Transcript', {})
        transcript_uri = transcript.get('TranscriptFileUri', '')

        text_uri = save_transcript_text(
            transcript_uri=transcript_uri,
            project_id=project_id,
            document_id=document_id,
            workflow_id=workflow_id
        )

        update_preprocess_status(
            document_id=document_id,
            workflow_id=workflow_id,
            processor=PreprocessType.TRANSCRIBE,
            status=PreprocessStatus.COMPLETED,
            output_uri=transcript_uri,
            text_uri=text_uri,
            job_name=job_name
        )
        record_step_complete(workflow_id, StepName.TRANSCRIBE)
        _delete_job(client, job_name)
        return {**event, 'transcribe_status': 'COMPLETED', 'transcribe_text_uri': text_uri}

    elif status == 'FAILED':
        failure_reason = job.get('FailureReason', 'Unknown error')

        # Skip non-recoverable audio parse errors (e.g. video without audio track)
        if 'Failed to parse audio file' in failure_reason:
            print(f'[{workflow_id}] Skipping transcription: {failure_reason}')
            update_preprocess_status(
                document_id=document_id,
                workflow_id=workflow_id,
                processor=PreprocessType.TRANSCRIBE,
                status=PreprocessStatus.SKIPPED,
                error=failure_reason,
                job_name=job_name
            )
            record_step_complete(workflow_id, StepName.TRANSCRIBE)
            _delete_job(client, job_name)
            return {**event, 'transcribe_status': 'SKIPPED', 'transcribe_skip_reason': failure_reason}

        update_preprocess_status(
            document_id=document_id,
            workflow_id=workflow_id,
            processor=PreprocessType.TRANSCRIBE,
            status=PreprocessStatus.FAILED,
            error=failure_reason,
            job_name=job_name
        )
        record_step_error(workflow_id, StepName.TRANSCRIBE, failure_reason)
        _delete_job(client, job_name)
        raise Exception(f'Transcription failed: {failure_reason}')

    # Still in progress
    return {**event, 'transcribe_status': 'IN_PROGRESS'}
