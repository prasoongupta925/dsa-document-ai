"""OCR Orchestrator Lambda

Routes OCR processing to Lambda (CPU) or SageMaker (GPU).
For PDFs, creates page-range payloads for Step Functions Map state.
Called by Step Functions.
"""
import json
import os
import tempfile
from datetime import datetime, timezone

import boto3

from shared.ddb_client import (
    update_preprocess_status,
    PreprocessStatus,
    PreprocessType,
    record_step_start,
    record_step_skipped,
    record_step_error,
    StepName,
)
from shared.s3_analysis import get_s3_client, parse_s3_uri

SAGEMAKER_ENDPOINT_NAME = os.environ.get('SAGEMAKER_ENDPOINT_NAME', '')
OUTPUT_BUCKET = os.environ.get('OUTPUT_BUCKET', '')
CHUNK_PAGE_SIZE = int(os.environ.get('CHUNK_PAGE_SIZE', '10'))

LAMBDA_OCR_MODELS = {'pp-ocrv5', 'pp-structurev3'}

SUPPORTED_MIME_TYPES = {
    'application/pdf',
    'image/jpeg',
    'image/png',
    'image/tiff',
    'image/gif',
    'image/bmp',
    'image/webp',
}

sagemaker_runtime = None
sagemaker_client = None


def get_sagemaker_runtime():
    global sagemaker_runtime
    if sagemaker_runtime is None:
        sagemaker_runtime = boto3.client(
            'sagemaker-runtime',
            region_name=os.environ.get('AWS_REGION', 'us-east-1')
        )
    return sagemaker_runtime


def get_sagemaker_client():
    global sagemaker_client
    if sagemaker_client is None:
        sagemaker_client = boto3.client(
            'sagemaker',
            region_name=os.environ.get('AWS_REGION', 'us-east-1')
        )
    return sagemaker_client


def ensure_endpoint_running():
    if not SAGEMAKER_ENDPOINT_NAME:
        return
    try:
        client = get_sagemaker_client()
        client.update_endpoint_weights_and_capacities(
            EndpointName=SAGEMAKER_ENDPOINT_NAME,
            DesiredWeightsAndCapacities=[{
                'VariantName': 'AllTraffic',
                'DesiredInstanceCount': 1
            }]
        )
        print(f'Requested scale-out for {SAGEMAKER_ENDPOINT_NAME}')
    except Exception as e:
        print(f'Scale-out request failed (non-fatal): {e}')


def get_document_base_path(file_uri: str) -> tuple[str, str]:
    bucket, key = parse_s3_uri(file_uri)
    key_parts = key.split('/')
    if 'documents' in key_parts:
        doc_idx = key_parts.index('documents')
        base_path = '/'.join(key_parts[:doc_idx + 2])
    else:
        base_path = '/'.join(key_parts[:-1])
    return bucket, base_path


def get_pdf_page_count(bucket: str, key: str) -> int:
    import pypdfium2 as pdfium
    s3 = get_s3_client()
    with tempfile.NamedTemporaryFile(suffix='.pdf', dir='/tmp', delete=True) as tmp:
        s3.download_file(bucket, key, tmp.name)
        doc = pdfium.PdfDocument(tmp.name)
        count = len(doc)
        doc.close()
        return count


def build_page_range_payloads(workflow_id, document_id, project_id, file_uri, page_count, ocr_model, ocr_options):
    """Build payloads with page ranges for Step Functions Map state."""
    payloads = []
    total_chunks = (page_count + CHUNK_PAGE_SIZE - 1) // CHUNK_PAGE_SIZE

    for chunk_idx in range(total_chunks):
        start_page = chunk_idx * CHUNK_PAGE_SIZE
        end_page = min(start_page + CHUNK_PAGE_SIZE, page_count)
        payloads.append({
            'workflow_id': workflow_id,
            'document_id': document_id,
            'project_id': project_id,
            'file_uri': file_uri,
            'ocr_model': ocr_model,
            'ocr_options': ocr_options or {},
            'chunk_index': chunk_idx,
            'start_page': start_page,
            'end_page': end_page,
            'total_chunks': total_chunks,
        })

    return payloads


def build_single_payload(workflow_id, document_id, project_id, file_uri, ocr_model, ocr_options):
    """Build a single payload for Step Functions Map state (single image or small PDF)."""
    return [{
        'workflow_id': workflow_id,
        'document_id': document_id,
        'project_id': project_id,
        'file_uri': file_uri,
        'ocr_model': ocr_model,
        'ocr_options': ocr_options or {},
        'chunk_index': 0,
        'total_chunks': 1,
    }]


def invoke_async_inference(file_uri, workflow_id, document_id, project_id, ocr_model, ocr_options=None):
    client = get_sagemaker_runtime()
    s3_client = get_s3_client()

    bucket, base_path = get_document_base_path(file_uri)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    inference_id = f'{workflow_id[:16]}-{timestamp}'

    inference_request = {
        's3_uri': file_uri,
        'model': ocr_model,
        'model_options': ocr_options or {},
        'metadata': {
            'workflow_id': workflow_id,
            'document_id': document_id,
            'project_id': project_id,
            'file_uri': file_uri,
            'base_path': base_path,
            'bucket': bucket,
        }
    }

    input_key = f'{base_path}/paddleocr/input.json'
    s3_client.put_object(
        Bucket=bucket,
        Key=input_key,
        Body=json.dumps(inference_request, ensure_ascii=False, indent=2).encode('utf-8'),
        ContentType='application/json'
    )
    input_location = f's3://{bucket}/{input_key}'

    response = client.invoke_endpoint_async(
        EndpointName=SAGEMAKER_ENDPOINT_NAME,
        ContentType='application/json',
        InputLocation=input_location,
        InvocationTimeoutSeconds=3600,
        InferenceId=inference_id,
    )
    return response.get('OutputLocation', '')


def handler(event, context):
    print(f'Event: {json.dumps(event)}')

    workflow_id = event.get('workflow_id')
    document_id = event.get('document_id')
    project_id = event.get('project_id')
    file_uri = event.get('file_uri')
    file_type = event.get('file_type')
    ocr_model = event.get('ocr_model', 'pp-ocrv5')
    ocr_options = event.get('ocr_options', {})

    if file_type not in SUPPORTED_MIME_TYPES:
        print(f'Skipping unsupported file type: {file_type}')
        update_preprocess_status(
            document_id=document_id,
            workflow_id=workflow_id,
            processor=PreprocessType.OCR,
            status=PreprocessStatus.SKIPPED,
            reason=f'File type {file_type} not supported'
        )
        record_step_skipped(workflow_id, StepName.PADDLEOCR_PROCESSOR, f'File type {file_type} not supported')
        return {**event, 'ocr_status': 'SKIPPED'}

    try:
        record_step_start(workflow_id, StepName.PADDLEOCR_PROCESSOR)
        update_preprocess_status(
            document_id=document_id,
            workflow_id=workflow_id,
            processor=PreprocessType.OCR,
            status=PreprocessStatus.PROCESSING
        )

        if ocr_model in LAMBDA_OCR_MODELS:
            if file_type == 'application/pdf':
                bucket, _ = get_document_base_path(file_uri)
                _, file_key = parse_s3_uri(file_uri)
                page_count = get_pdf_page_count(bucket, file_key)
                print(f'[{workflow_id}] PDF has {page_count} pages (chunk size: {CHUNK_PAGE_SIZE})')

                ocr_chunks = build_page_range_payloads(
                    workflow_id, document_id, project_id, file_uri,
                    page_count, ocr_model, ocr_options,
                )
            else:
                ocr_chunks = build_single_payload(
                    workflow_id, document_id, project_id, file_uri, ocr_model, ocr_options,
                )

            return {
                **event,
                'ocr_status': 'IN_PROGRESS',
                'ocr_backend': 'lambda',
                'ocr_chunks': ocr_chunks,
                'ocr_total_chunks': len(ocr_chunks),
            }
        else:
            ensure_endpoint_running()
            invoke_async_inference(
                file_uri=file_uri, workflow_id=workflow_id,
                document_id=document_id, project_id=project_id,
                ocr_model=ocr_model, ocr_options=ocr_options,
            )
            return {**event, 'ocr_status': 'IN_PROGRESS', 'ocr_backend': 'sagemaker'}

    except Exception as e:
        print(f'Error invoking OCR: {e}')
        update_preprocess_status(
            document_id=document_id, workflow_id=workflow_id,
            processor=PreprocessType.OCR,
            status=PreprocessStatus.FAILED, error=str(e)
        )
        record_step_error(workflow_id, StepName.PADDLEOCR_PROCESSOR, str(e))
        raise
