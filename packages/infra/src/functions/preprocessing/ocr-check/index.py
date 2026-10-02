"""OCR Check Lambda

Checks if OCR processing is complete by looking at the preprocess status in DDB.
OCR completion is recorded by either:
- Lambda processor (writes directly to DDB on completion)
- SNS complete handler (triggered by SageMaker async inference)
- Chunk merger (merges all chunks and writes final status)

Called by Step Functions polling loop.
"""
import json

from shared.ddb_client import (
    get_entity_prefix,
    is_preprocess_complete,
    PreprocessType,
)


def handler(event, context):
    print(f'Event: {json.dumps(event)}')

    workflow_id = event.get('workflow_id')
    document_id = event.get('document_id')
    file_type = event.get('file_type', '')

    entity_type = get_entity_prefix(file_type)
    result = is_preprocess_complete(document_id, workflow_id, entity_type)

    ocr_status = result['status'].get(PreprocessType.OCR, {})
    proc_status = ocr_status.get('status', 'skipped')

    print(f'OCR preprocess status: {proc_status}')

    if proc_status in ('completed', 'skipped'):
        return {**event, 'ocr_status': 'COMPLETED'}
    elif proc_status == 'failed':
        raise Exception(f'OCR processing failed')

    return {**event, 'ocr_status': 'IN_PROGRESS'}
