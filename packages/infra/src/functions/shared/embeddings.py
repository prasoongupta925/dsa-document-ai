import json
import os
import re
from typing import List

import boto3


_HTML_TAG_RE = re.compile(r'<[^>]+>')
_MD_HEADER_RE = re.compile(r'^#{1,6}\s+', re.MULTILINE)
_WHITESPACE_RE = re.compile(r'\n{3,}')


def strip_markup(text: str) -> str:
    """Strip HTML tags and markdown headers for cleaner embedding input."""
    text = _HTML_TAG_RE.sub(' ', text)
    text = _MD_HEADER_RE.sub('', text)
    text = (
        text.replace('&amp;', '&')
        .replace('&lt;', '<')
        .replace('&gt;', '>')
        .replace('&nbsp;', ' ')
    )
    text = _WHITESPACE_RE.sub('\n\n', text)
    return text.strip()


EMBEDDING_MODEL_ID = os.environ.get(
    'EMBEDDING_MODEL_ID', 'amazon.nova-2-multimodal-embeddings-v1:0'
)


def generate_single_embedding(text: str, client=None) -> List[float]:
    if client is None:
        # Amazon embedding models are not offered in every region (e.g.
        # ap-south-1): EMBEDDING_REGION (CDK config) wins over the Lambda region.
        client = boto3.client(
            'bedrock-runtime',
            region_name=os.environ.get('EMBEDDING_REGION')
            or os.environ.get('AWS_REGION', 'us-east-1'),
        )

    try:
        clean_text = strip_markup(text)
        response = client.invoke_model(
            modelId=EMBEDDING_MODEL_ID,
            body=json.dumps(
                {
                    'taskType': 'SINGLE_EMBEDDING',
                    'singleEmbeddingParams': {
                        'embeddingPurpose': 'GENERIC_INDEX',
                        'embeddingDimension': 1024,
                        'text': {'truncationMode': 'END', 'value': clean_text},
                    },
                }
            ),
            contentType='application/json',
        )
        result = json.loads(response['body'].read())
        return result['embeddings'][0]['embedding']
    except Exception as e:
        print(f'Error generating embedding: {e}')
        return [0.0] * 1024
