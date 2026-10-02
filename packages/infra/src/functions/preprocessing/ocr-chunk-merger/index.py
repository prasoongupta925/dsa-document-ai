"""OCR Chunk Merger Lambda

Triggered by S3 EventBridge when chunk_*.json files are created.
Checks if all chunks for a workflow are complete, then merges them into result.json.

Uses S3 file listing as the sole source of truth (no DDB for chunk tracking).
Idempotent: if two instances run concurrently, both produce the same merged result.
"""
import json
import os
import re

import boto3

from shared.ddb_client import (
    update_preprocess_status,
    PreprocessStatus,
    PreprocessType,
    record_step_complete,
    record_step_error,
    StepName,
)

s3_client = None


def get_s3_client():
    global s3_client
    if s3_client is None:
        s3_client = boto3.client('s3')
    return s3_client


def extract_base_path(chunk_key: str) -> str:
    """Extract document base path from a chunk key.

    chunk_key: projects/proj_X/documents/doc_X/paddleocr/chunks/chunk_0000.json
    returns:   projects/proj_X/documents/doc_X
    """
    match = re.match(r'(.+)/paddleocr/chunks/chunk_\d+\.json$', chunk_key)
    if not match:
        raise ValueError(f'Cannot extract base path from key: {chunk_key}')
    return match.group(1)



def list_chunk_files(s3, bucket: str, base_path: str) -> tuple[list[str], list[str]]:
    """List completed chunk JSON files and failed marker files.

    Returns (json_keys, failed_keys).
    """
    prefix = f'{base_path}/paddleocr/chunks/'
    json_keys = []
    failed_keys = []

    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get('Contents', []):
            key = obj['Key']
            if key.endswith('.json'):
                json_keys.append(key)
            elif key.endswith('.failed'):
                failed_keys.append(key)

    return json_keys, failed_keys


def merge_chunk_results(s3, bucket: str, chunk_keys: list[str]) -> dict:
    """Read all chunk JSON files and merge into a single result.

    Pages are sorted by page_index to maintain correct order.
    """
    all_pages = []
    all_content = []
    model = None
    model_options = {}

    # Sort by chunk index to maintain order
    sorted_keys = sorted(chunk_keys)

    for key in sorted_keys:
        response = s3.get_object(Bucket=bucket, Key=key)
        chunk_data = json.loads(response['Body'].read().decode('utf-8'))

        all_pages.extend(chunk_data.get('pages', []))
        if chunk_data.get('content'):
            all_content.append(chunk_data['content'])

        if model is None:
            model = chunk_data.get('model')
            model_options = chunk_data.get('model_options', {})

    # Sort pages by page_index for correct ordering
    all_pages.sort(key=lambda p: p.get('page_index', 0))

    return {
        'success': True,
        'format': 'markdown',
        'model': model,
        'model_options': model_options,
        'pages': all_pages,
        'page_count': len(all_pages),
        'content': '\n\n---\n\n'.join(all_content),
    }


def cleanup_chunks(s3, bucket: str, base_path: str) -> None:
    """Delete chunk files (PDFs, JSONs, failed markers) after successful merge."""
    prefix = f'{base_path}/paddleocr/chunks/'

    paginator = s3.get_paginator('list_objects_v2')
    keys_to_delete = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get('Contents', []):
            keys_to_delete.append({'Key': obj['Key']})

    if not keys_to_delete:
        return

    # Delete in batches of 1000 (S3 limit)
    for i in range(0, len(keys_to_delete), 1000):
        batch = keys_to_delete[i:i + 1000]
        s3.delete_objects(Bucket=bucket, Delete={'Objects': batch})

    print(f'Cleaned up {len(keys_to_delete)} chunk files')


def get_base_path_from_file_uri(file_uri: str) -> tuple[str, str]:
    """Extract bucket and base path from file_uri."""
    # s3://bucket/projects/proj_X/documents/doc_X/file.pdf
    parts = file_uri.replace('s3://', '').split('/', 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else ''
    key_parts = key.split('/')
    if 'documents' in key_parts:
        doc_idx = key_parts.index('documents')
        base_path = '/'.join(key_parts[:doc_idx + 2])
    else:
        base_path = '/'.join(key_parts[:-1])
    return bucket, base_path


def handler(event, _context):
    """Handle chunk merge after Step Functions Map completion."""
    print(f'Event: {json.dumps(event)}')

    workflow_id = event.get('workflow_id')
    document_id = event.get('document_id')
    file_uri = event.get('file_uri')

    if not workflow_id or not file_uri:
        print('Missing workflow_id or file_uri')
        return {**event, 'merge_status': 'skipped', 'reason': 'missing_fields'}

    bucket, base_path = get_base_path_from_file_uri(file_uri)
    s3 = get_s3_client()

    total_chunks = event.get('ocr_total_chunks', 0)
    if not total_chunks:
        print('Missing ocr_total_chunks in event')
        return {**event, 'merge_status': 'error', 'reason': 'missing_total_chunks'}

    # Count completed and failed chunks
    json_keys, failed_keys = list_chunk_files(s3, bucket, base_path)
    completed = len(json_keys)
    failed = len(failed_keys)
    total_done = completed + failed

    print(f'[{workflow_id}] Chunk progress: {completed} completed, {failed} failed, {total_done}/{total_chunks} total')

    if failed > 0:
        error_msg = f'{failed}/{total_chunks} OCR chunks failed'
        print(f'[{workflow_id}] {error_msg}')

        update_preprocess_status(
            document_id=document_id,
            workflow_id=workflow_id,
            processor=PreprocessType.OCR,
            status=PreprocessStatus.FAILED,
            error=error_msg,
        )
        record_step_error(workflow_id, StepName.PADDLEOCR_PROCESSOR, error_msg)
        cleanup_chunks(s3, bucket, base_path)
        raise RuntimeError(error_msg)

    # All chunks succeeded -> merge
    print(f'[{workflow_id}] Merging {completed} chunks...')

    merged = merge_chunk_results(s3, bucket, json_keys)

    # Save merged result.json
    output_key = f'{base_path}/paddleocr/result.json'
    s3.put_object(
        Bucket=bucket,
        Key=output_key,
        Body=json.dumps(merged, ensure_ascii=False, indent=2),
        ContentType='application/json',
    )
    ocr_output_uri = f's3://{bucket}/{output_key}'
    page_count = merged.get('page_count', 0)
    print(f'[{workflow_id}] Merged result saved: {ocr_output_uri} ({page_count} pages)')

    # Update DDB
    update_preprocess_status(
        document_id=document_id,
        workflow_id=workflow_id,
        processor=PreprocessType.OCR,
        status=PreprocessStatus.COMPLETED,
        output_uri=ocr_output_uri,
        page_count=page_count,
    )
    record_step_complete(workflow_id, StepName.PADDLEOCR_PROCESSOR)

    cleanup_chunks(s3, bucket, base_path)

    return {
        **event,
        'ocr_status': 'COMPLETED',
        'ocr_output_uri': ocr_output_uri,
        'ocr_page_count': page_count,
    }
