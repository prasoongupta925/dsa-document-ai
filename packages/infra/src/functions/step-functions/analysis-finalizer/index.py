"""Analysis Finalizer Lambda

Sends QA pairs to LanceDB write queue for vector indexing.
Runs in parallel with entity-extractor and page-description-generator in the Distributed Map.
"""
import json
import os
from datetime import datetime, timezone

import boto3

from shared.s3_analysis import (
    get_segment_analysis,
    update_segment_status,
    SegmentStatus,
)

sqs_client = None
LANCEDB_WRITE_QUEUE_URL = os.environ.get('LANCEDB_WRITE_QUEUE_URL')


def get_sqs_client():
    global sqs_client
    if sqs_client is None:
        sqs_client = boto3.client('sqs', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
    return sqs_client


def build_qa_content(analysis_query: str, content: str) -> str:
    """Build content string for a single QA pair."""
    if analysis_query:
        return f'{analysis_query}\n{content}'
    return content


def handler(event, _context):
    print(f'Event: {json.dumps(event)}')

    workflow_id = event.get('workflow_id')
    document_id = event.get('document_id', '')
    project_id = event.get('project_id', 'default')
    segment_index = event.get('segment_index', 0)
    file_uri = event.get('file_uri', '')
    file_type = event.get('file_type', '')
    language = event.get('language', 'en')

    if isinstance(segment_index, dict):
        segment_index = segment_index.get('segment_index', 0)

    segment_data = get_segment_analysis(file_uri, segment_index)
    if not segment_data:
        print(f'Segment not found in S3 for file {file_uri}, segment {segment_index}')
        return {
            'workflow_id': workflow_id,
            'segment_index': segment_index,
            'status': 'not_found',
        }

    image_uri = segment_data.get('image_uri', '')

    # Update status to FINALIZING
    update_segment_status(file_uri, segment_index, SegmentStatus.FINALIZING)

    # Send per-QA pair messages to SQS
    ai_analysis = segment_data.get('ai_analysis', [])
    created_at = datetime.now(timezone.utc).isoformat()
    sent_count = 0

    try:
        client = get_sqs_client()

        for qa_index, analysis in enumerate(ai_analysis):
            analysis_query = analysis.get('analysis_query', '')
            content = analysis.get('content', '')
            if not content:
                continue

            qa_content = build_qa_content(analysis_query, content)
            message = {
                'workflow_id': workflow_id,
                'document_id': document_id,
                'project_id': project_id,
                'segment_index': segment_index,
                'qa_index': qa_index,
                'question': analysis_query,
                'content_combined': qa_content,
                'language': language,
                'file_uri': file_uri,
                'file_type': file_type,
                'image_uri': image_uri,
                'created_at': created_at,
            }

            response = client.send_message(
                QueueUrl=LANCEDB_WRITE_QUEUE_URL,
                MessageBody=json.dumps(message),
            )
            sent_count += 1
            print(f'Sent segment {segment_index} QA {qa_index} to SQS, MessageId: {response["MessageId"]}')

        # Update status to COMPLETED
        update_segment_status(file_uri, segment_index, SegmentStatus.COMPLETED)

        return {
            'workflow_id': workflow_id,
            'segment_index': segment_index,
            'status': 'queued',
            'qa_count': sent_count,
        }

    except Exception as e:
        print(f'Error sending to SQS: {e}')
        update_segment_status(file_uri, segment_index, SegmentStatus.FAILED, error=str(e))
        return {
            'workflow_id': workflow_id,
            'segment_index': segment_index,
            'status': 'failed',
            'error': str(e),
        }
