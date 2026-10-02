"""Workflow Failure Catcher Lambda

Triggered by EventBridge when Step Functions execution reaches
FAILED, TIMED_OUT, or ABORTED status. Handles cases where addCatch
cannot fire (e.g., 25000 history event limit, execution timeout).

Retrieves the original SFN input to get workflow_id/document_id,
then updates DDB status to failed.
"""
import json
import os

import boto3

from shared.ddb_client import (
    WorkflowStatus,
    update_workflow_status,
    get_entity_prefix,
)

sfn_client = None


def get_sfn_client():
    global sfn_client
    if sfn_client is None:
        sfn_client = boto3.client('stepfunctions')
    return sfn_client


def handler(event, context):
    print(f'Event: {json.dumps(event)}')

    detail = event.get('detail', {})
    execution_arn = detail.get('executionArn', '')
    status = detail.get('status', '')

    if not execution_arn:
        print('Missing executionArn')
        return {'handled': False}

    # Describe execution to get the original input
    client = get_sfn_client()
    try:
        response = client.describe_execution(executionArn=execution_arn)
    except Exception as e:
        print(f'Failed to describe execution: {e}')
        return {'handled': False}

    # Parse original input
    sfn_input = json.loads(response.get('input', '{}'))
    workflow_id = sfn_input.get('workflow_id', '')
    document_id = sfn_input.get('document_id', '')
    file_type = sfn_input.get('file_type', '')

    if not workflow_id or not document_id:
        print(f'Missing workflow_id or document_id in SFN input')
        return {'handled': False}

    error_message = f'Step Functions execution {status}: {execution_arn}'
    if status == 'FAILED':
        sfn_error = response.get('error', '')
        sfn_cause = response.get('cause', '')
        if sfn_error:
            error_message = f'{sfn_error}: {sfn_cause}' if sfn_cause else sfn_error

    print(f'[{workflow_id}] Execution {status}, updating DDB')

    try:
        entity_type = get_entity_prefix(file_type)
        update_workflow_status(
            document_id,
            workflow_id,
            WorkflowStatus.FAILED,
            entity_type=entity_type,
            error=error_message,
        )
        print(f'[{workflow_id}] Updated workflow status to failed')
    except Exception as e:
        print(f'[{workflow_id}] Failed to update workflow status: {e}')
        return {'handled': False}

    return {'handled': True, 'workflow_id': workflow_id, 'status': status}
