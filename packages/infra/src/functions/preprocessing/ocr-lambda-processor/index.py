"""OCR Lambda Processor

Invokes the Rust PaddleOCR Lambda for OCR processing.
Transforms the Rust response into the standard format expected by ocr-chunk-merger.
"""
import json
import os
import time

import boto3

from shared.ddb_client import (
    update_preprocess_status,
    PreprocessStatus,
    PreprocessType,
    record_step_complete,
    record_step_error,
    StepName,
)

OUTPUT_BUCKET = os.environ.get('OUTPUT_BUCKET', '')
RUST_OCR_FUNCTION_NAME = os.environ.get('RUST_OCR_FUNCTION_NAME', 'idp-v2-paddle-ocr')

s3_client = None
lambda_client = None


def get_s3_client():
    global s3_client
    if s3_client is None:
        s3_client = boto3.client('s3')
    return s3_client


def get_lambda_client():
    global lambda_client
    if lambda_client is None:
        from botocore.config import Config
        config = Config(read_timeout=660, connect_timeout=10)
        lambda_client = boto3.client('lambda', config=config)
    return lambda_client


def parse_s3_uri(uri: str) -> tuple[str, str]:
    clean = uri.replace('s3://', '')
    bucket = clean.split('/')[0]
    key = '/'.join(clean.split('/')[1:])
    return bucket, key


def get_base_path_from_uri(file_uri: str) -> tuple[str, str, str]:
    bucket, key = parse_s3_uri(file_uri)
    key_parts = key.split('/')
    if 'documents' in key_parts:
        doc_idx = key_parts.index('documents')
        base_path = '/'.join(key_parts[:doc_idx + 2])
    else:
        base_path = '/'.join(key_parts[:-1])
    return bucket, key, base_path


def invoke_rust_ocr(file_uri: str, lang: str, use_doc_orientation_classify: bool,
                    from_page: int | None = None, to_page: int | None = None) -> dict:
    payload = {
        's3_uri': file_uri,
        'lang': lang,
        'use_doc_orientation_classify': use_doc_orientation_classify,
    }
    if from_page is not None:
        payload['from'] = from_page
    if to_page is not None:
        payload['to'] = to_page

    response = get_lambda_client().invoke(
        FunctionName=RUST_OCR_FUNCTION_NAME,
        InvocationType='RequestResponse',
        Payload=json.dumps(payload),
    )
    resp_payload = json.loads(response['Payload'].read())
    if 'errorMessage' in resp_payload:
        raise RuntimeError(f'Rust OCR failed: {resp_payload["errorMessage"]}')
    return resp_payload


def transform_rust_response(rust_response: dict, ocr_model: str, ocr_options: dict | None) -> dict:
    pages = []
    all_content = []

    for page_data in rust_response.get('pages', []):
        page_index = page_data['page']
        items = page_data.get('items', [])

        blocks = []
        text_parts = []
        for idx, item in enumerate(items):
            bbox = item.get('bbox', {})
            x1 = bbox.get('x', 0)
            y1 = bbox.get('y', 0)
            x2 = x1 + bbox.get('width', 0)
            y2 = y1 + bbox.get('height', 0)

            blocks.append({
                'block_id': idx,
                'block_label': 'text',
                'block_content': item.get('text', ''),
                'block_bbox': [x1, y1, x2, y2],
                'block_order': idx,
                'group_id': 0,
            })
            text = item.get('text', '').strip()
            if text:
                text_parts.append(text)

        content = '\n'.join(text_parts)
        pages.append({
            'page_index': page_index,
            'content': content,
            'blocks': blocks,
            'width': None,
            'height': None,
        })
        all_content.append(content)

    return {
        'success': True,
        'format': 'markdown',
        'model': ocr_model,
        'model_options': ocr_options or {},
        'pages': pages,
        'page_count': len(pages),
        'content': '\n\n---\n\n'.join(all_content),
    }


def process_file(
    file_uri: str,
    workflow_id: str,
    document_id: str,
    ocr_model: str,
    ocr_options: dict | None = None,
    chunk_index: int | None = None,
    start_page: int | None = None,
    end_page: int | None = None,
    total_chunks: int | None = None,
) -> dict:
    is_chunk = chunk_index is not None
    s3 = get_s3_client()
    bucket, _key, base_path = get_base_path_from_uri(file_uri)

    chunk_label = f' chunk={chunk_index}/{total_chunks}' if is_chunk else ''
    opts = ocr_options or {}
    lang = opts.get('lang', 'ko')
    use_orientation = opts.get('use_doc_orientation_classify', False)

    # Convert page range: orchestrator uses [start_page, end_page) exclusive
    # Rust Lambda uses [from, to] inclusive
    from_page = start_page if is_chunk and start_page is not None else None
    to_page = (end_page - 1) if is_chunk and end_page is not None else None

    try:
        # Invoke Rust OCR Lambda
        invoke_start = time.time()
        print(f'[{workflow_id}]{chunk_label} Invoking Rust OCR: lang={lang}, from={from_page}, to={to_page}')
        rust_response = invoke_rust_ocr(file_uri, lang, use_orientation, from_page, to_page)
        print(f'[{workflow_id}]{chunk_label} Rust OCR completed in {time.time() - invoke_start:.1f}s')

        # Transform response
        output = transform_rust_response(rust_response, ocr_model, ocr_options)

        if is_chunk:
            output_key = f'{base_path}/paddleocr/chunks/chunk_{chunk_index:04d}.json'
            s3.put_object(
                Bucket=bucket,
                Key=output_key,
                Body=json.dumps(output, ensure_ascii=False, indent=2),
                ContentType='application/json',
            )
            output_uri = f's3://{bucket}/{output_key}'
            print(f'[{workflow_id}] Chunk {chunk_index} result saved to: {output_uri}')
            return {'status': 'completed', 'output_uri': output_uri, 'page_count': output['page_count'], 'chunk_index': chunk_index}
        else:
            output_key = f'{base_path}/paddleocr/result.json'
            s3.put_object(
                Bucket=bucket,
                Key=output_key,
                Body=json.dumps(output, ensure_ascii=False, indent=2),
                ContentType='application/json',
            )
            ocr_output_uri = f's3://{bucket}/{output_key}'
            print(f'[{workflow_id}] Result saved to: {ocr_output_uri}')

            page_count = output['page_count']
            update_preprocess_status(
                document_id=document_id,
                workflow_id=workflow_id,
                processor=PreprocessType.OCR,
                status=PreprocessStatus.COMPLETED,
                output_uri=ocr_output_uri,
                page_count=page_count,
            )
            record_step_complete(workflow_id, StepName.PADDLEOCR_PROCESSOR)
            print(f'[{workflow_id}] OCR completed: {page_count} pages')
            return {'status': 'completed', 'output_uri': ocr_output_uri, 'page_count': page_count}

    except Exception:
        if is_chunk:
            try:
                failed_key = f'{base_path}/paddleocr/chunks/chunk_{chunk_index:04d}.failed'
                s3.put_object(
                    Bucket=bucket,
                    Key=failed_key,
                    Body=b'',
                    ContentType='application/octet-stream',
                )
                print(f'[{workflow_id}] Saved failure marker: {failed_key}')
            except Exception as marker_err:
                print(f'[{workflow_id}] Failed to save failure marker: {marker_err}')
        raise


def handler(event, context):
    print(f'Event: {json.dumps(event)}')

    workflow_id = event.get('workflow_id')
    document_id = event.get('document_id')
    file_uri = event.get('file_uri')
    ocr_model = event.get('ocr_model', 'pp-ocrv5')
    ocr_options = event.get('ocr_options', {})

    chunk_index = event.get('chunk_index')
    start_page = event.get('start_page')
    end_page = event.get('end_page')
    total_chunks = event.get('total_chunks')
    is_chunk = chunk_index is not None

    remaining_ms = context.get_remaining_time_in_millis() if context else 900000
    chunk_label = f', chunk={chunk_index}/{total_chunks}' if is_chunk else ''
    print(f'[{workflow_id}] Starting OCR (model={ocr_model}{chunk_label}, timeout={remaining_ms/1000:.0f}s)')

    try:
        result = process_file(
            file_uri=file_uri,
            workflow_id=workflow_id,
            document_id=document_id,
            ocr_model=ocr_model,
            ocr_options=ocr_options,
            chunk_index=chunk_index,
            start_page=start_page,
            end_page=end_page,
            total_chunks=total_chunks,
        )
        return {'statusCode': 200, 'body': json.dumps(result)}

    except Exception as e:
        print(f'[{workflow_id}] OCR processing failed: {e}')
        import traceback
        traceback.print_exc()

        if not is_chunk:
            update_preprocess_status(
                document_id=document_id,
                workflow_id=workflow_id,
                processor=PreprocessType.OCR,
                status=PreprocessStatus.FAILED,
                error=str(e),
            )
            record_step_error(workflow_id, StepName.PADDLEOCR_PROCESSOR, str(e))
        raise
