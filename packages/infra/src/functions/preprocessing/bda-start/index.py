"""BDA Start Lambda

Starts a Bedrock Data Automation async job. Called by Step Functions.
Returns the invocation ARN for status checking.
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

BDA_PROJECT_NAME = os.environ.get('BDA_PROJECT_NAME', 'idp-v2-bda-project')
BDA_OUTPUT_BUCKET = os.environ.get('BDA_OUTPUT_BUCKET', '')

SUPPORTED_MIME_TYPES = {
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/msword',
    'text/plain',
    'text/csv',
    'image/jpeg',
    'image/png',
    'image/tiff',
    'image/gif',
    'image/bmp',
    'image/webp',
    'video/mp4',
    'video/quicktime',
    'video/x-msvideo',
    'video/x-matroska',
    'video/webm',
    'audio/mp4',
    'audio/mpeg',
    'audio/flac',
    'audio/ogg',
    'audio/wav',
}

bda_client = None
bda_runtime_client = None


def get_bda_client():
    global bda_client
    if bda_client is None:
        bda_client = boto3.client(
            'bedrock-data-automation',
            region_name=os.environ.get('AWS_REGION', 'us-east-1')
        )
    return bda_client


def get_bda_runtime_client():
    global bda_runtime_client
    if bda_runtime_client is None:
        bda_runtime_client = boto3.client(
            'bedrock-data-automation-runtime',
            region_name=os.environ.get('AWS_REGION', 'us-east-1')
        )
    return bda_runtime_client


def _bda_profile_id(region: str) -> str:
    """BDA cross-region profile for the deploy region (geo prefix us./eu./apac.).

    BDA_PROFILE_ID overrides the derived id.
    """
    override = os.environ.get('BDA_PROFILE_ID')
    if override:
        return override
    region = region or ''
    if region.startswith('us-'):
        return 'us.data-automation-v1'
    if region.startswith('eu-'):
        return 'eu.data-automation-v1'
    if region.startswith('ap-'):
        return 'apac.data-automation-v1'
    return 'us.data-automation-v1'


def get_standard_output_config():
    return {
        'document': {
            'extraction': {
                'granularity': {'types': ['DOCUMENT', 'PAGE', 'ELEMENT']},
                'boundingBox': {'state': 'ENABLED'}
            },
            'generativeField': {'state': 'ENABLED'},
            'outputFormat': {
                'textFormat': {'types': ['MARKDOWN']},
                'additionalFileFormat': {'state': 'ENABLED'}
            }
        }
    }


def get_or_create_bda_project(client) -> str:
    try:
        projects = client.list_data_automation_projects()
        for project in projects.get('projects', []):
            if project['projectName'] == BDA_PROJECT_NAME:
                project_arn = project['projectArn']
                print(f'Using existing BDA project: {project_arn}')
                return project_arn

        print(f'Creating new BDA project: {BDA_PROJECT_NAME}')
        response = client.create_data_automation_project(
            projectName=BDA_PROJECT_NAME,
            projectDescription='IDP-v2 document analysis project',
            standardOutputConfiguration=get_standard_output_config()
        )
        return response['projectArn']

    except Exception as e:
        print(f'Error creating BDA project: {e}')
        raise


def handler(event, context):
    print(f'Event: {json.dumps(event)}')

    workflow_id = event.get('workflow_id')
    document_id = event.get('document_id')
    project_id = event.get('project_id')
    file_uri = event.get('file_uri')
    file_type = event.get('file_type')

    # Check if file type is supported
    if file_type not in SUPPORTED_MIME_TYPES:
        print(f'Skipping unsupported file type: {file_type}')
        update_preprocess_status(
            document_id=document_id,
            workflow_id=workflow_id,
            processor=PreprocessType.BDA,
            status=PreprocessStatus.SKIPPED,
            reason=f'File type {file_type} not supported'
        )
        record_step_skipped(workflow_id, StepName.BDA_PROCESSOR, f'File type {file_type} not supported')
        return {**event, 'bda_status': 'SKIPPED'}

    record_step_start(workflow_id, StepName.BDA_PROCESSOR)
    update_preprocess_status(
        document_id=document_id,
        workflow_id=workflow_id,
        processor=PreprocessType.BDA,
        status=PreprocessStatus.PROCESSING
    )

    client = get_bda_client()
    runtime_client = get_bda_runtime_client()
    project_arn = get_or_create_bda_project(client)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if document_id:
        output_prefix = f'projects/{project_id}/documents/{document_id}/bda-output/{timestamp}'
    else:
        output_prefix = f'bda-output/{workflow_id}/{timestamp}'
    output_uri = f's3://{BDA_OUTPUT_BUCKET}/{output_prefix}'

    session = boto3.Session()
    region = os.environ.get('AWS_REGION') or session.region_name or 'us-east-1'
    profile_id = _bda_profile_id(region)
    sts_client = boto3.client('sts')
    account_id = sts_client.get_caller_identity()['Account']

    response = runtime_client.invoke_data_automation_async(
        inputConfiguration={'s3Uri': file_uri},
        outputConfiguration={'s3Uri': output_uri},
        dataAutomationConfiguration={
            'dataAutomationProjectArn': project_arn,
            'stage': 'LIVE'
        },
        dataAutomationProfileArn=f'arn:aws:bedrock:{region}:{account_id}:data-automation-profile/{profile_id}'
    )

    invocation_arn = response['invocationArn']
    print(f'BDA invocation started: {invocation_arn}')

    return {
        **event,
        'bda_invocation_arn': invocation_arn,
        'bda_output_uri': output_uri,
        'bda_status': 'IN_PROGRESS',
    }
