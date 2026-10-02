"""Document Facts Lambda (3rd branch of PostAnalysisParallel).

After a document is analysed, extract ONE structured loan-file facts record
(doc type, applicant, PAN, employer, month/period, salaries, salary credits,
recurring debits, declared existing EMIs, loan amount / tenure, financial
year, and the CIBIL page fields: mobile, DOB, addresses, house ownership,
employment type, other income, rent agreement, pension, credit report score,
enquiries and tradelines) with the facts model (FACTS_MODEL_ID, gpt-oss-120b),
ground identifiers and amounts against the document's own machine text, and
store the record in S3 (analysis/facts.json, with model_fields for audit) and
DynamoDB (PROJ#{pid} / FACTS#{did}, without document text or model_fields).

The record's `field_pages` names the page each value is printed on
({'mobile': 1, 'tradelines': 1, 'tradelines[1]': 2, ...}: a list by its first
row's page; grounding.field_pages), so a value can be shown as "from
<document_name>, page N"; enquiries and tradelines rows also carry their own
'page'. Credit-report account numbers are kept as their last 4 characters only
(normalize.account_last4).

The record's `usage` is the cost of that one model call: {model_id,
service_tier, input_tokens, output_tokens, cost_usd} at the model's price for the
service tier that served it (extractor.cost_usd; BEDROCK_SERVICE_TIER requests
the tier); zero tokens and model_id None when no call was made.

Non-fatal: the handler never raises. It returns ONLY {workflow_id, status}
because Step Functions keeps execution history for 90 days, so the output must
carry no names, PAN or amounts. Logs carry only ids, doc_type, counts and
token usage.
"""
import os
import traceback
from datetime import datetime, timezone

from shared.ddb_client import (
    get_document,
    record_step_complete,
    record_step_error,
    record_step_start,
    save_document_facts,
    StepName,
)
from shared.s3_analysis import get_all_segment_analyses, save_facts

from extractor import MACHINE_TEXT_FIELDS, build_texts, call_model, page_texts, usage_record
from grounding import MIN_TEXT_CHARS, field_pages, ground_fields, unverified_numbers, with_row_pages
from normalize import normalise_fields

SCHEMA_VERSION = 1
SOURCE_MODEL = 'model'
SOURCE_NONE = 'none'
DEFAULT_MAX_CHARS = 60000
WEBREQ_FILE_TYPE = 'application/x-webreq'
SEGMENT_FIELDS = ['segment_type', *MACHINE_TEXT_FIELDS, 'ai_analysis']


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _basename(file_uri: str) -> str:
    return (file_uri or '').rstrip('/').rsplit('/', 1)[-1]


def _document_id_from_uri(file_uri: str) -> str:
    parts = (file_uri or '').split('/')
    try:
        i = parts.index('documents')
        return parts[i + 1] if i + 1 < len(parts) else ''
    except ValueError:
        return ''


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _max_chars() -> int:
    value = _to_int(os.environ.get('FACTS_MAX_CHARS'), DEFAULT_MAX_CHARS)
    return value if value > 0 else DEFAULT_MAX_CHARS


def _base_record(ctx: dict, status: str) -> dict:
    """Facts record skeleton (interfaces section 3) with an empty, normalised field set."""
    fields = normalise_fields({})
    return {
        'schema_version': SCHEMA_VERSION,
        'document_id': ctx['document_id'],
        'project_id': ctx['project_id'],
        'workflow_id': ctx['workflow_id'],
        'document_name': ctx['document_name'],
        'file_type': ctx['file_type'],
        'doc_type': fields['doc_type'],
        'fields': fields,
        'grounding': {
            'grounded': False,
            'text_chars': 0,
            'notes': [],
            'unverified_fields': [],
            'truncated': False,
            'output_truncated': False,
        },
        'field_pages': {},
        'source': SOURCE_NONE,
        'model_id': ctx['model_id'],
        'status': status,
        'reason': None,
        'error': None,
        'usage': usage_record(),
        'extracted_at': _now(),
    }


def handler(event, context):
    event = event or {}
    workflow_id = event.get('workflow_id')
    project_id = event.get('project_id')
    file_uri = event.get('file_uri') or ''
    file_type = event.get('file_type') or ''
    segment_count = max(_to_int(event.get('segment_count')), 0)
    language = event.get('language') or ''
    document_id = event.get('document_id') or _document_id_from_uri(file_uri)

    print(f'Event keys: {sorted(event)}; workflow_id={workflow_id} document_id={document_id} '
          f'project_id={project_id} file_type={file_type} segment_count={segment_count} '
          f'language={language}')

    ctx = {
        'workflow_id': workflow_id,
        'document_id': document_id,
        'project_id': project_id,
        'file_type': file_type,
        'document_name': _basename(file_uri),
        'model_id': os.environ.get('FACTS_MODEL_ID', ''),
    }

    try:
        record_step_start(workflow_id, StepName.DOCUMENT_FACTS)
    except Exception:
        traceback.print_exc()

    usage = None  # set once the model call has been billed
    try:
        ctx['document_name'] = (get_document(project_id, document_id) or {}).get('name') or ctx['document_name']

        segments = (get_all_segment_analyses(file_uri, segment_count, fields=SEGMENT_FIELDS)
                    if segment_count and file_type != WEBREQ_FILE_TYPE else [])
        max_chars = _max_chars()
        model_text, grounding_text, stats = build_texts(segments, max_chars)
        print(f'Facts input: segments={len(segments)} pages={stats["pages"]} '
              f'pages_with_machine_text={stats["pages_with_machine_text"]} '
              f'text_chars={stats["text_chars"]} model_chars={len(model_text)} truncated={stats["truncated"]}')

        reason = None
        if file_type == WEBREQ_FILE_TYPE:
            # Web pages are not loan documents; create_workflow marks this
            # step skipped for them, so keep it skipped (and make no model call).
            reason = 'webreq'
        elif stats['pages'] == 0:
            is_media = bool(segments) or file_type.startswith(('video/', 'audio/'))
            reason = 'media' if is_media else 'no_text'
        elif not model_text.strip():
            reason = 'no_text'

        if reason:
            record = _base_record(ctx, 'skipped')
            record['reason'] = reason
            record['grounding']['text_chars'] = stats['text_chars']
            save_facts(file_uri, {**record, 'model_fields': None})
            save_document_facts(project_id, document_id, record)
            record_step_complete(workflow_id, StepName.DOCUMENT_FACTS, skipped=True, reason=reason)
            print(f'Facts skipped: workflow_id={workflow_id} reason={reason}')
            return {'workflow_id': workflow_id, 'status': 'skipped'}

        raw, usage = call_model(model_text, os.environ['FACTS_MODEL_ID'])
        output_truncated = bool(usage.pop('output_truncated', False))
        model_fields = normalise_fields(raw)
        fields, notes = ground_fields(model_fields, grounding_text)

        grounded = len(grounding_text.strip()) >= MIN_TEXT_CHARS
        if stats['truncated']:
            notes.append(f'input truncated to {max_chars} chars')
        if not grounded:
            notes.append('no machine text layer; values not verified')
        if output_truncated:
            notes.append('model output hit the token limit; lists may be incomplete')

        pages = field_pages(fields, page_texts(segments))
        record = _base_record(ctx, 'completed')
        record.update({
            'doc_type': fields['doc_type'],
            'fields': with_row_pages(fields, pages),
            'grounding': {
                'grounded': grounded,
                'text_chars': stats['text_chars'],
                'notes': notes,
                'unverified_fields': unverified_numbers(fields, grounding_text),
                'truncated': stats['truncated'],
                'output_truncated': output_truncated,
            },
            'field_pages': pages,
            'source': SOURCE_MODEL,
            'usage': usage,
        })

        save_facts(file_uri, {**record, 'model_fields': model_fields})
        save_document_facts(project_id, document_id, record)
        record_step_complete(workflow_id, StepName.DOCUMENT_FACTS, doc_type=fields['doc_type'])

        print(f'Facts completed: workflow_id={workflow_id} doc_type={fields["doc_type"]} '
              f'grounded={grounded} notes={len(notes)} '
              f'unverified={len(record["grounding"]["unverified_fields"])} '
              f'debits={len(fields["recurring_debits"])} declared_emis={len(fields["declared_existing_emis"])} '
              f'tradelines={len(fields["tradelines"])} enquiries={len(fields["enquiries"])} '
              f'pages_found={len(record["field_pages"])} '
              f'input_tokens={usage.get("input_tokens", 0)} output_tokens={usage.get("output_tokens", 0)} '
              f'service_tier={usage.get("service_tier")} cost_usd={usage.get("cost_usd", 0)}')
        return {'workflow_id': workflow_id, 'status': 'completed'}

    except Exception as e:
        traceback.print_exc()
        error = str(e)
        try:
            record_step_error(workflow_id, StepName.DOCUMENT_FACTS, error)
        except Exception:
            traceback.print_exc()
        try:
            record = _base_record(ctx, 'failed')
            record['error'] = error[:500]
            # A call that answered without fields (or failed later) was still billed.
            spent = usage or getattr(e, 'usage', None)
            if isinstance(spent, dict):
                record['usage'] = spent
            save_document_facts(project_id, document_id, record)
        except Exception:
            traceback.print_exc()
        print(f'Facts failed: workflow_id={workflow_id} error_type={type(e).__name__}')
        return {'workflow_id': workflow_id, 'status': 'failed'}
