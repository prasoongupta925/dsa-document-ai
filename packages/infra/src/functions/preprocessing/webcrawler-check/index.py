"""WebCrawler Check Lambda

Checks webcrawler agent completion status via DDB. Called by Step Functions polling loop.
The webcrawler agent updates DDB preprocess status when it finishes crawling.
"""
import json

from shared.ddb_client import (
    get_workflow,
    EntityType,
    PreprocessType,
    record_step_complete,
    record_step_error,
    StepName,
)


def handler(event, context):
    print(f'Event: {json.dumps(event)}')

    workflow_id = event.get('workflow_id')
    document_id = event.get('document_id')

    # Web documents use WEB# entity prefix, not DOC#
    workflow = get_workflow(document_id, workflow_id, entity_type=EntityType.WEB)
    if not workflow:
        raise Exception(f'Workflow not found: {document_id}/{workflow_id}')

    data = workflow.get('data', {})
    preprocess = data.get('preprocess', {})
    webcrawler = preprocess.get(PreprocessType.WEBCRAWLER, {})
    status = webcrawler.get('status', 'pending')

    print(f'WebCrawler status: {status}')

    if status == 'completed':
        record_step_complete(workflow_id, StepName.WEBCRAWLER)
        return {**event, 'webcrawler_status': 'COMPLETED'}

    elif status == 'failed':
        error = webcrawler.get('error', 'Unknown error')
        record_step_error(workflow_id, StepName.WEBCRAWLER, error)
        raise Exception(f'WebCrawler failed: {error}')

    # Still processing
    return {**event, 'webcrawler_status': 'IN_PROGRESS'}
