"""File Check MCP Lambda handler for AgentCore Gateway.

Exposes deterministic tools (no LLM):
  - run_file_check(project_id, checklist_id?, applicant?, reference_month?):
    loads every PROJ#{pid} DOC# and FACTS# item, groups documents by applicant
    and applies a bundled loan-product checklist -> READY / NOT READY with
    findings that cite document names. The project's FCCONF# items (needs-review
    items a person confirmed in the web app) count as met.
  - list_checklists(): the checklists run_file_check can apply.
  - emi_calculator(principal, annual_rate_pct, tenure_months | tenure_years,
    new_annual_rate_pct?): EMI, totals, split, yearly schedule, balance transfer.
  - foir_eligibility(net_monthly_income, existing_emis?, foir_pct,
    annual_rate_pct, tenure_months | tenure_years): maximum EMI and loan.
  - loan_eligibility(project_id, applicant): the applicant's saved CIBIL-page
    inputs at every SAMPLE lender policy (eligibility.py, the backend's engine).

and, for the backend only (not in schema.json, so the chat never sees it):
  - applicant_documents(project_id, applicant): the documents of one
    applicant, grouped exactly as run_file_check groups them (read-only). The
    backend's erase-applicant API deletes these documents.

Only engine.py decides the verdict and only loan_tools.py / eligibility.py
compute EMIs and eligibility; the chat model reports them. Facts contents
(names, PAN, amounts) are never logged.
"""

import decimal
import os
import re
import traceback

import engine
import loan_tools

TABLE_NAME = os.environ['BACKEND_TABLE_NAME']
AWS_REGION = os.environ.get('AWS_REGION', os.environ.get('AWS_DEFAULT_REGION'))
CHECKLISTS_PATH = os.environ.get('CHECKLISTS_PATH') or None
DEFAULT_CHECKLIST_ID = os.environ.get('DEFAULT_CHECKLIST_ID') or None

_YM_RE = re.compile(r'^\d{4}-\d{2}$')

# Lazily created; tests assign a fake table here.
_table = None
_catalog = None


def _get_table():
    global _table
    if _table is None:
        import boto3

        _table = boto3.resource('dynamodb', region_name=AWS_REGION).Table(
            TABLE_NAME
        )
    return _table


def _get_catalog() -> dict:
    """Load checklists.json once per container (DEFAULT_CHECKLIST_ID wins)."""
    global _catalog
    if _catalog is None:
        catalog = engine.load_catalog(CHECKLISTS_PATH)
        if DEFAULT_CHECKLIST_ID:
            engine.get_checklist(catalog, DEFAULT_CHECKLIST_ID)  # must exist
            catalog = {**catalog, 'default_checklist': DEFAULT_CHECKLIST_ID}
        _catalog = catalog
    return _catalog


def _from_decimal(value):
    """Recursively convert DynamoDB Decimals to int / float."""
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _from_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_decimal(v) for v in value]
    return value


def load_project_items(table, project_id: str, confirmations=None):
    """Return (documents, facts): DOC# and FACTS# data of one project.

    A `confirmations` list also collects the FCCONF# data (needs-review items
    a person confirmed; see engine.py). Base-table Query on PK only (no GSI),
    paginated.
    """
    from boto3.dynamodb.conditions import Key

    documents, facts = [], []
    kwargs = {'KeyConditionExpression': Key('PK').eq(f'PROJ#{project_id}')}
    while True:
        resp = table.query(**kwargs)
        for item in resp.get('Items', []):
            sk = str(item.get('SK', ''))
            data = item.get('data')
            if not isinstance(data, dict):
                continue
            if sk.startswith('DOC#'):
                documents.append(_from_decimal(data))
            elif sk.startswith('FACTS#'):
                facts.append(_from_decimal(data))
            elif confirmations is not None and sk.startswith(
                engine.CONFIRMATION_SK_PREFIX
            ):
                confirmations.append(_from_decimal(data))
        last_key = resp.get('LastEvaluatedKey')
        if not last_key:
            break
        kwargs['ExclusiveStartKey'] = last_key
    return documents, facts


def _optional_str(event: dict, key: str):
    value = event.get(key)
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, f'{key} must be a string'
    return value.strip() or None, None


def _project_id(event: dict):
    project_id = event.get('project_id')
    if not isinstance(project_id, str) or not project_id.strip():
        return None, 'project_id is required'
    return project_id.strip(), None


def run_file_check(event: dict) -> dict:
    project_id, err = _project_id(event)
    if err:
        return {'error': err}

    checklist_id, err = _optional_str(event, 'checklist_id')
    if err:
        return {'error': err}
    applicant, err = _optional_str(event, 'applicant')
    if err:
        return {'error': err}
    reference_month, err = _optional_str(event, 'reference_month')
    if err:
        return {'error': err}
    if reference_month is not None and (
        not _YM_RE.match(reference_month)
        or not 1 <= int(reference_month[5:]) <= 12
    ):
        return {'error': 'reference_month must be YYYY-MM, e.g. 2026-08'}

    catalog = _get_catalog()
    try:
        checklist = engine.get_checklist(catalog, checklist_id)
    except ValueError:
        return {
            'error': f'Unknown checklist_id: {checklist_id}',
            'available': [c['id'] for c in catalog['checklists']],
        }

    confirmations = []
    documents, facts = load_project_items(_get_table(), project_id, confirmations)
    result = engine.run_file_check(
        facts,
        checklist,
        documents=documents,
        reference_month=reference_month,
        applicant=applicant,
        project_id=project_id,
        confirmations=confirmations,
    )
    print(
        f'run_file_check project={project_id} checklist={checklist["id"]} '
        f'docs={len(documents)} facts={len(facts)} '
        f'confirmations={len(confirmations)} '
        f'applicants={len(result["applicants"])} '
        f'verdict={result["overall_verdict"]}'
    )
    return result


def list_checklists(event: dict) -> dict:
    return engine.list_checklists(_get_catalog())


def applicant_documents(event: dict) -> dict:
    """Read-only: {applicant_name, pan_masked, documents: [{document_id, name}], matches}."""
    project_id, err = _project_id(event)
    if err:
        return {'error': err}
    applicant, err = _optional_str(event, 'applicant')
    if err:
        return {'error': err}
    if applicant is None:
        return {'error': 'applicant is required'}

    documents, facts = load_project_items(_get_table(), project_id)
    result = engine.applicant_documents(facts, applicant, documents=documents)
    print(
        f'applicant_documents project={project_id} docs={len(documents)} '
        f'facts={len(facts)} matches={result["matches"]} '
        f'documents={len(result["documents"])}'
    )
    return {'project_id': project_id, **result}


def loan_eligibility(event: dict) -> dict:
    return loan_tools.loan_eligibility(event, _get_table(), run_file_check)


_TOOLS = {
    'run_file_check': run_file_check,
    'list_checklists': list_checklists,
    'applicant_documents': applicant_documents,
    'emi_calculator': loan_tools.emi_calculator,
    'foir_eligibility': loan_tools.foir_eligibility,
    'loan_eligibility': loan_eligibility,
}
# What failed, in the error a tool returns for an unexpected exception.
_FAILED = {
    'emi_calculator': 'EMI calculation',
    'foir_eligibility': 'FOIR eligibility',
    'loan_eligibility': 'loan eligibility',
}


def handler(event: dict, context) -> dict:
    """AgentCore Gateway invokes with the tool name in clientContext.custom."""
    tool_name = ''
    client_context = getattr(context, 'client_context', None)
    if client_context is not None and getattr(client_context, 'custom', None):
        tool_name = client_context.custom.get('bedrockAgentCoreToolName', '')

    action = tool_name.split('___')[-1] if '___' in tool_name else tool_name

    fn = _TOOLS.get(action)
    if fn is None:
        return {'error': f'Unknown tool: {tool_name}'}
    try:
        return fn(event if isinstance(event, dict) else {})
    except Exception as e:  # noqa: BLE001 - tool must answer, never raise
        # Frame locations and the exception type only: the message could quote
        # document facts (names, PAN, amounts), which must never be logged.
        frames = [
            f'  {f.filename}:{f.lineno} in {f.name}'
            for f in traceback.extract_tb(e.__traceback__)
        ]
        failed = _FAILED.get(action, 'file check')
        print(
            f'{failed} failed: {type(e).__name__}\n'
            + 'Traceback (most recent call last):\n'
            + '\n'.join(frames)
        )
        return {'error': f'{failed} failed: {type(e).__name__}'}
