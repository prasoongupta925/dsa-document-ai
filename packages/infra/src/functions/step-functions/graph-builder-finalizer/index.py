"""Graph Builder Finalizer Lambda

Called after SendGraphBatches Map completes.
Records graph builder step as complete in DynamoDB.
Pre-computes Cluster nodes for large documents.
"""
import json
import os
import time

import boto3

from shared.ddb_client import record_step_complete, StepName

GRAPH_SERVICE_FUNCTION_NAME = os.environ.get('GRAPH_SERVICE_FUNCTION_NAME', '')

_lambda_client = None


def get_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = boto3.client('lambda')
    return _lambda_client


def invoke_graph_service(action: str, params: dict, max_retries: int = 3) -> dict:
    """Invoke the GraphService Lambda with retry on 5xx errors."""
    if not GRAPH_SERVICE_FUNCTION_NAME:
        return {'success': True, 'skipped': True}

    client = get_lambda_client()
    for attempt in range(max_retries + 1):
        response = client.invoke(
            FunctionName=GRAPH_SERVICE_FUNCTION_NAME,
            InvocationType='RequestResponse',
            Payload=json.dumps({'action': action, 'params': params}),
        )
        payload = json.loads(response['Payload'].read())
        if response.get('FunctionError') or payload.get('statusCode') != 200:
            error_msg = payload.get('error', 'Unknown')
            if attempt < max_retries and ('500' in str(error_msg) or '503' in str(error_msg)):
                wait = 2 ** attempt
                print(f'{action} retry {attempt + 1}/{max_retries} after {wait}s: {error_msg}')
                time.sleep(wait)
                continue
            raise RuntimeError(f'GraphService error: {error_msg}')
        return payload
    raise RuntimeError(f'GraphService error: max retries exceeded for {action}')


def handler(event, _context):
    print(f'Event: {json.dumps(event)}')

    workflow_id = event['workflow_id']
    document_id = event.get('document_id', '')
    project_id = event.get('project_id', '')
    entity_count = event.get('entity_count', 0)
    relationship_count = event.get('relationship_count', 0)

    # Pre-compute Cluster nodes for large documents
    if document_id and project_id:
        try:
            result = invoke_graph_service(
                'build_clusters',
                {'project_id': project_id, 'document_id': document_id},
            )
            clustered = result.get('clustered', False)
            cluster_count = result.get('cluster_count', 0)
            print(f'Cluster build: clustered={clustered}, clusters={cluster_count}')
        except Exception as e:
            print(f'Cluster build failed (non-fatal): {e}')

    record_step_complete(
        workflow_id,
        StepName.GRAPH_BUILDER,
        entity_count=entity_count,
        relationship_count=relationship_count,
    )
    print(f'Finalized graph builder: {entity_count} entities, {relationship_count} relationships')

    return {
        'workflow_id': event.get('workflow_id'),
        'document_id': event.get('document_id'),
        'project_id': event.get('project_id'),
        'file_uri': event.get('file_uri'),
        'file_type': event.get('file_type'),
        'segment_count': event.get('segment_count'),
    }
