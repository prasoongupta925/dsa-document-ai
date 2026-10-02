"""Segment Prep Finalizer Lambda

Called after RenderPagesInParallel Map completes.
Records segment prep step as complete in DynamoDB.
"""
import json

from shared.ddb_client import record_step_complete, StepName


def handler(event, _context):
    print(f'Event: {json.dumps(event)}')

    workflow_id = event['workflow_id']
    segment_count = event.get('segment_count', 0)

    record_step_complete(
        workflow_id,
        StepName.SEGMENT_PREP,
        segment_count=segment_count,
    )
    print(f'Finalized segment prep: {segment_count} segments')

    return {
        'workflow_id': event.get('workflow_id'),
        'project_id': event.get('project_id'),
        'document_id': event.get('document_id'),
        'file_uri': event.get('file_uri'),
        'file_type': event.get('file_type'),
        'segment_count': segment_count,
        'preprocessor_metadata_uri': event.get('preprocessor_metadata_uri'),
        'language': event.get('language'),
        'document_prompt': event.get('document_prompt'),
    }
