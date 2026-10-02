"""Retention Sweeper Lambda

Runs daily (EventBridge schedule) and deletes client data older than
RETENTION_DAYS: documents (same cascade as the backend's delete_document),
extracted facts, datasets, chat sessions, artifacts and finished Amazon
Transcribe jobs. The logic lives in sweep.py; this module only builds the
boto3 clients and the configuration from the environment.

Environment:
    BACKEND_TABLE_NAME, DOCUMENT_STORAGE_BUCKET_NAME, SESSION_STORAGE_BUCKET_NAME,
    AGENT_STORAGE_BUCKET_NAME, LANCEDB_FUNCTION_NAME, GRAPH_DELETE_QUEUE_URL
    (all required), RETENTION_DAYS (default 7), DRY_RUN (default false),
    DELETE_TRANSCRIBE_JOBS (default true).

A manual invoke with {"dry_run": true} only counts what would be deleted.
Logs contain counts only.
"""
import json
import os

import boto3
from botocore.config import Config

from sweep import SweepConfig, run_sweep

REQUIRED_ENV = (
    'BACKEND_TABLE_NAME',
    'DOCUMENT_STORAGE_BUCKET_NAME',
    'SESSION_STORAGE_BUCKET_NAME',
    'AGENT_STORAGE_BUCKET_NAME',
    'LANCEDB_FUNCTION_NAME',
    'GRAPH_DELETE_QUEUE_URL',
)

# The LanceDB service can run for up to 5 minutes per call.
LAMBDA_CLIENT_CONFIG = Config(
    read_timeout=310,
    connect_timeout=10,
    retries={'max_attempts': 2, 'mode': 'standard'},
)


def _env_bool(env, name: str, default: bool) -> bool:
    value = env.get(name)
    if value is None or str(value).strip() == '':
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def load_config(env=None) -> SweepConfig:
    env = os.environ if env is None else env
    missing = [name for name in REQUIRED_ENV if not env.get(name)]
    if missing:
        raise RuntimeError(f'Missing required environment variables: {", ".join(missing)}')
    raw_days = str(env.get('RETENTION_DAYS', '7') or '7').strip()
    try:
        retention_days = int(raw_days)
    except ValueError:
        raise RuntimeError(f'RETENTION_DAYS must be an integer >= 1, got {raw_days!r}') from None
    if retention_days < 1:
        raise RuntimeError(f'RETENTION_DAYS must be an integer >= 1, got {raw_days!r}')
    return SweepConfig(
        table_name=env['BACKEND_TABLE_NAME'],
        document_bucket=env['DOCUMENT_STORAGE_BUCKET_NAME'],
        session_bucket=env['SESSION_STORAGE_BUCKET_NAME'],
        agent_bucket=env['AGENT_STORAGE_BUCKET_NAME'],
        lancedb_function=env['LANCEDB_FUNCTION_NAME'],
        graph_delete_queue_url=env['GRAPH_DELETE_QUEUE_URL'],
        retention_days=retention_days,
        dry_run=_env_bool(env, 'DRY_RUN', False),
        delete_transcribe_jobs=_env_bool(env, 'DELETE_TRANSCRIBE_JOBS', True),
    )


def handler(event, context):
    cfg = load_config()
    # An invoke payload can only turn dry run ON, never off.
    if isinstance(event, dict) and event.get('dry_run') is True:
        cfg.dry_run = True

    region = os.environ.get('AWS_REGION')
    table = boto3.resource('dynamodb', region_name=region).Table(cfg.table_name)
    s3 = boto3.client('s3', region_name=region)
    lambda_client = boto3.client('lambda', region_name=region, config=LAMBDA_CLIENT_CONFIG)
    sqs = boto3.client('sqs', region_name=region)
    transcribe = boto3.client('transcribe', region_name=region)

    time_left_ms = getattr(context, 'get_remaining_time_in_millis', None)
    result = run_sweep(table, s3, lambda_client, sqs, transcribe, cfg, time_left_ms=time_left_ms)
    print(json.dumps({'retention_sweep': result}, default=str))
    return result
