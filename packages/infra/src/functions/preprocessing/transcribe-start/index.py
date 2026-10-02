"""Transcribe Start Lambda

Starts an AWS Transcribe job. Called by Step Functions.
Returns the job name for status checking.
"""
import json
import os
from datetime import datetime, timezone

import boto3

from shared.ddb_client import (
    update_preprocess_status,
    PreprocessStatus,
    PreprocessType,
    record_step_start,
    record_step_skipped,
    StepName,
)

TRANSCRIBE_OUTPUT_BUCKET = os.environ.get('TRANSCRIBE_OUTPUT_BUCKET', '')

SUPPORTED_MIME_TYPES = {
    'video/mp4',
    'video/webm',
    'audio/mpeg',
    'audio/mp4',
    'audio/wav',
    'audio/x-wav',
    'audio/flac',
    'audio/amr',
    'audio/ogg',
    'audio/webm',
}

MEDIA_FORMAT_MAP = {
    'video/mp4': 'mp4',
    'video/webm': 'webm',
    'audio/mpeg': 'mp3',
    'audio/mp4': 'mp4',
    'audio/wav': 'wav',
    'audio/x-wav': 'wav',
    'audio/flac': 'flac',
    'audio/amr': 'amr',
    'audio/ogg': 'ogg',
    'audio/webm': 'webm',
}

# Calls and recordings for an Indian loan DSA: Indian English, Hindi, Marathi (plus US English).
IDENTIFY_LANGUAGE_OPTIONS = ['en-IN', 'hi-IN', 'mr-IN', 'en-US']

transcribe_client = None


def get_transcribe_client():
    global transcribe_client
    if transcribe_client is None:
        transcribe_client = boto3.client(
            'transcribe',
            region_name=os.environ.get('AWS_REGION', 'us-east-1')
        )
    return transcribe_client


def handler(event, context):
    print(f'Event: {json.dumps(event)}')

    workflow_id = event.get('workflow_id')
    document_id = event.get('document_id')
    project_id = event.get('project_id')
    file_uri = event.get('file_uri')
    file_type = event.get('file_type')
    transcribe_options = event.get('transcribe_options')

    if file_type not in SUPPORTED_MIME_TYPES:
        print(f'Skipping unsupported file type: {file_type}')
        update_preprocess_status(
            document_id=document_id,
            workflow_id=workflow_id,
            processor=PreprocessType.TRANSCRIBE,
            status=PreprocessStatus.SKIPPED,
            reason=f'File type {file_type} not supported'
        )
        record_step_skipped(workflow_id, StepName.TRANSCRIBE, f'File type {file_type} not supported')
        return {**event, 'transcribe_status': 'SKIPPED'}

    record_step_start(workflow_id, StepName.TRANSCRIBE)
    update_preprocess_status(
        document_id=document_id,
        workflow_id=workflow_id,
        processor=PreprocessType.TRANSCRIBE,
        status=PreprocessStatus.PROCESSING
    )

    client = get_transcribe_client()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    job_name = f'{workflow_id[:32]}-{timestamp}'

    if document_id:
        output_key = f'projects/{project_id}/documents/{document_id}/transcribe/{job_name}.json'
    else:
        output_key = f'transcribe/{workflow_id}/{job_name}.json'

    media_format = MEDIA_FORMAT_MAP.get(file_type, 'mp4')
    opts = transcribe_options or {}
    mode = opts.get('language_mode', 'auto')

    job_params = {
        'TranscriptionJobName': job_name,
        'MediaFormat': media_format,
        'Media': {'MediaFileUri': file_uri},
        'OutputBucketName': TRANSCRIBE_OUTPUT_BUCKET,
        'OutputKey': output_key,
    }

    if mode == 'direct':
        job_params['LanguageCode'] = opts.get('language_code', 'en-US')
    elif mode == 'multi':
        job_params['IdentifyMultipleLanguages'] = True
        lang_opts = opts.get('language_options')
        if lang_opts:
            job_params['LanguageOptions'] = lang_opts
    else:
        job_params['IdentifyLanguage'] = True
        job_params['LanguageOptions'] = IDENTIFY_LANGUAGE_OPTIONS

    client.start_transcription_job(**job_params)
    print(f'Started transcription job: {job_name}')

    return {
        **event,
        'transcribe_job_name': job_name,
        'transcribe_status': 'IN_PROGRESS',
    }
