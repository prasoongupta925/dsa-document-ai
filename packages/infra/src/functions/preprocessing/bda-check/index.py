"""BDA Check Lambda

Checks the status of a BDA async invocation. Called by Step Functions polling loop.
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

bda_runtime_client = None


def get_bda_runtime_client():
    global bda_runtime_client
    if bda_runtime_client is None:
        bda_runtime_client = boto3.client(
            'bedrock-data-automation-runtime',
            region_name=os.environ.get('AWS_REGION', 'us-east-1')
        )
    return bda_runtime_client


def handler(event, context):
    print(f'Event: {json.dumps(event)}')

    workflow_id = event.get('workflow_id')
    document_id = event.get('document_id')
    invocation_arn = event.get('bda_invocation_arn')

    runtime_client = get_bda_runtime_client()
    response = runtime_client.get_data_automation_status(
        invocationArn=invocation_arn
    )

    bda_status = response.get('status', 'Unknown')
    print(f'BDA status: {bda_status}')

    if bda_status == 'Success':
        output_config = response.get('outputConfiguration', {})
        s3_uri = output_config.get('s3Uri', '').rstrip('/')
        if s3_uri.endswith('job_metadata.json'):
            output_dir = s3_uri.rsplit('/job_metadata.json', 1)[0]
        else:
            output_dir = s3_uri

        update_preprocess_status(
            document_id=document_id,
            workflow_id=workflow_id,
            processor=PreprocessType.BDA,
            status=PreprocessStatus.COMPLETED,
            output_uri=output_dir,
            invocation_arn=invocation_arn
        )
        record_step_complete(workflow_id, StepName.BDA_PROCESSOR)
        return {**event, 'bda_status': 'COMPLETED', 'bda_output_uri': output_dir}

    elif bda_status in ['ServiceError', 'ClientError', 'Failed']:
        error_message = response.get('errorMessage', 'Unknown error')
        update_preprocess_status(
            document_id=document_id,
            workflow_id=workflow_id,
            processor=PreprocessType.BDA,
            status=PreprocessStatus.FAILED,
            error=error_message,
            invocation_arn=invocation_arn
        )
        record_step_error(workflow_id, StepName.BDA_PROCESSOR, error_message)
        raise Exception(f'BDA failed: {error_message}')

    # Still in progress
    return {**event, 'bda_status': 'IN_PROGRESS'}
