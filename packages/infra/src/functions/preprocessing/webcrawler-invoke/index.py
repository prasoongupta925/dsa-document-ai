"""WebCrawler Invoke Lambda

Invokes WebCrawler AgentCore Runtime. Called by Step Functions.
Only triggers the agent - completion is tracked via DDB polling in webcrawler-check.
"""
import json
import os

import boto3

from shared.ddb_client import (
    update_preprocess_status,
    PreprocessStatus,
    PreprocessType,
    record_step_start,
    record_step_error,
    StepName,
)

WEBCRAWLER_AGENT_RUNTIME_ARN = os.environ.get('WEBCRAWLER_AGENT_RUNTIME_ARN', '')

agentcore_client = None


def get_agentcore_client():
    global agentcore_client
    if agentcore_client is None:
        agentcore_client = boto3.client(
            'bedrock-agentcore',
            region_name=os.environ.get('AWS_REGION', 'us-east-1'),
        )
    return agentcore_client


def handler(event, context):
    print(f'Event: {json.dumps(event)}')

    workflow_id = event.get('workflow_id')
    document_id = event.get('document_id')

    if not WEBCRAWLER_AGENT_RUNTIME_ARN:
        print('WEBCRAWLER_AGENT_RUNTIME_ARN not configured, skipping')
        return {**event, 'webcrawler_status': 'SKIPPED'}

    record_step_start(workflow_id, StepName.WEBCRAWLER)
    update_preprocess_status(
        document_id=document_id,
        workflow_id=workflow_id,
        processor=PreprocessType.WEBCRAWLER,
        status=PreprocessStatus.PROCESSING,
    )

    try:
        client = get_agentcore_client()
        client.invoke_agent_runtime(
            agentRuntimeArn=WEBCRAWLER_AGENT_RUNTIME_ARN,
            payload=json.dumps(event).encode('utf-8'),
            contentType='application/json',
        )
        print(f'Invoked WebCrawler Agent: {workflow_id}')

        # Agent runs async in AgentCore - do NOT mark as completed here.
        # webcrawler-check Lambda will poll DDB for actual completion.
        return {**event, 'webcrawler_status': 'IN_PROGRESS'}

    except Exception as e:
        print(f'Error invoking WebCrawler: {e}')
        update_preprocess_status(
            document_id=document_id,
            workflow_id=workflow_id,
            processor=PreprocessType.WEBCRAWLER,
            status=PreprocessStatus.FAILED,
            error=str(e),
        )
        record_step_error(workflow_id, StepName.WEBCRAWLER, str(e))
        raise
