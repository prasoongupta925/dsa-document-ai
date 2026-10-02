"""Workflow Finalizer Lambda

Called after PostAnalysisParallel (GraphBuilder + Summarizer + DocumentFacts) completes.
Records workflow as COMPLETED in DynamoDB, then queues the project's CRM webhook
(an asynchronous invoke of the webhook delivery Lambda, WEBHOOK_FUNCTION_NAME) when
the project's webhook is enabled. The webhook never fails the workflow.
"""
import json
import os

from shared.ddb_client import (
    get_table,
    update_workflow_status,
    get_entity_prefix,
    WorkflowStatus,
)

WEBHOOK_EVENT = 'file_check.completed'

_lambda_client = None


def _get_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        import boto3
        from botocore.config import Config

        # An asynchronous invoke only queues the event: keep it quick.
        _lambda_client = boto3.client(
            'lambda',
            region_name=os.environ.get('AWS_REGION', 'us-east-1'),
            config=Config(
                connect_timeout=3,
                read_timeout=5,
                retries={'max_attempts': 2, 'mode': 'standard'},
            ),
        )
    return _lambda_client


def _webhook_enabled(project_id: str) -> bool:
    """Cheap META read: two small attributes, never the secret."""
    response = get_table().get_item(
        Key={'PK': f'PROJ#{project_id}', 'SK': 'META'},
        ProjectionExpression='webhook_enabled, webhook_url',
    )
    item = response.get('Item') or {}
    return item.get('webhook_enabled') is True and bool(item.get('webhook_url'))


def _project_of(updated):
    """project_id from update_workflow_status's result, or None."""
    data = updated.get('data') if isinstance(updated, dict) else None
    return data.get('project_id') if isinstance(data, dict) else None


def notify_webhook(project_id, document_id) -> bool:
    """Queue the webhook delivery for this document; True when queued. Never raises."""
    function_name = os.environ.get('WEBHOOK_FUNCTION_NAME', '')
    if not function_name or not project_id:
        return False
    try:
        if not _webhook_enabled(project_id):
            return False
        _get_lambda_client().invoke(
            FunctionName=function_name,
            InvocationType='Event',
            Payload=json.dumps({
                'project_id': project_id,
                'document_id': document_id,
                'event': WEBHOOK_EVENT,
            }).encode('utf-8'),
        )
    except Exception as e:  # noqa: BLE001 - a webhook problem must never fail the workflow
        print(f'Webhook not queued for project {project_id}: {type(e).__name__}')
        return False
    print(f'Webhook queued for project {project_id}')
    return True


def handler(event, _context):
    print(f'Event: {json.dumps(event)}')

    workflow_id = event['workflow_id']
    document_id = event.get('document_id', '')
    file_type = event.get('file_type', '')

    entity_type = get_entity_prefix(file_type)
    updated = update_workflow_status(
        document_id,
        workflow_id,
        WorkflowStatus.COMPLETED,
        entity_type=entity_type,
    )

    print(f'Workflow {workflow_id} marked as COMPLETED')

    notify_webhook(event.get('project_id') or _project_of(updated), document_id)

    return {
        'workflow_id': workflow_id,
        'status': 'completed',
    }
