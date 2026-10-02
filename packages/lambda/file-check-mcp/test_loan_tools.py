"""EMI, FOIR and loan-eligibility tools (loan_tools.py) and the vendored engine (no AWS calls).

The worked examples: 1,00,00,000 at 16% for 30 years -> EMI 1,34,476, total interest
3,84,11,252, total payable 4,84,11,252, principal 21% / interest 79%; the client sheet's
ICICI Bank (income 98,000, FOIR 70%, obligations 15,000, 11%, calculation tenure 60, max 72)
-> FOIR eligibility 24,65,227, eligible 20,58,000, EMI 39,172.13. Applicants are synthetic.
"""

import copy
import hashlib
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

os.environ['BACKEND_TABLE_NAME'] = 'test-table'
os.environ['AWS_DEFAULT_REGION'] = 'ap-south-1'
os.environ.setdefault('AWS_REGION', 'ap-south-1')
os.environ['AWS_ACCESS_KEY_ID'] = 'testing'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'testing'
os.environ['AWS_SESSION_TOKEN'] = 'testing'

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pytest  # noqa: E402

import eligibility  # noqa: E402
import index  # noqa: E402
import loan_tools  # noqa: E402
from test_filecheck_handler import _ctx, _items, _to_ddb  # noqa: E402
from test_filecheck_engine import rahul, sneha  # noqa: E402

BACKEND = HERE.parents[1] / 'backend' / 'app'
EMI = _ctx('filecheck___emi_calculator')
FOIR = _ctx('filecheck___foir_eligibility')
ELIGIBILITY = _ctx('filecheck___loan_eligibility')
NOW = 1_790_000_000  # 2026-09-21 14:13:20 UTC
WEEK = 7 * 86400
RAHUL, RAHUL_PAN = 'Rahul Vijay Deshmukh', 'BQXPD4821K'
SAMPLE = 'sample policy — replace with your lender grid'


# ------------------------------------------------------------------ the vendored engine
@pytest.mark.parametrize(
    'name',
    [
        'eligibility.py',
        'data/lender_policies.json',
        'data/pincodes.json',
        'data/companies.json',
    ],
)
def test_the_engine_is_a_copy_of_the_backends(name):
    assert (HERE / name).read_bytes() == (BACKEND / name).read_bytes(), (
        f'packages/lambda/file-check-mcp/{name} must be a copy of packages/backend/app/{name}: '
        f'cp packages/backend/app/{name} packages/lambda/file-check-mcp/{name}'
    )


def test_the_engine_runs_without_pydantic():
    """The Lambda runtime has the standard library and boto3 only."""
    code = (
        "import sys; sys.modules['pydantic'] = None; import eligibility; "
        "print(eligibility.load_policy_book().lender('icici_bank').calculation_tenure)"
    )
    run = subprocess.run([sys.executable, '-c', code], cwd=HERE, capture_output=True, text=True)

    assert (run.returncode, run.stdout.strip()) == (0, '60'), run.stderr
    assert eligibility.calculate({})['per_lender'][1]['calculation_tenure_months'] == 60


# ------------------------------------------------------------------ emi_calculator
def test_emi_example():
    res = index.handler(
        {'principal': 10000000, 'annual_rate_pct': 16, 'tenure_years': 30},
        EMI,
    )

    assert (res['emi'], res['total_interest'], res['total_payable']) == (
        134476,
        38411252,
        48411252,
    )
    assert (res['principal_pct'], res['interest_pct']) == (21, 79)
    assert res['emi_exact'] == 134475.7
    assert res['tenure_months'] == 360
    assert res['display'] == {
        'principal': '₹1,00,00,000',
        'annual_rate': '16%',
        'tenure': '30 years (360 months)',
        'emi': '₹1,34,476',
        'total_interest': '₹3,84,11,252',
        'total_payable': '₹4,84,11,252',
        'split': 'principal 21%, interest 79%',
    }
    assert res['summary'] == (
        'EMI ₹1,34,476 a month on ₹1,00,00,000 at 16% p.a. over 30 years (360 months): '
        'total interest ₹3,84,11,252, total payable ₹4,84,11,252 (principal 21%, interest 79%)'
    )
    assert res['label'].startswith('indicative')
    assert 'balance_transfer' not in res
    json.dumps(res, ensure_ascii=False)


def test_yearly_schedule():
    res = loan_tools.emi_calculator(
        {'principal': 10000000, 'annual_rate_pct': 16, 'tenure_months': 360}
    )

    rows = res['yearly_schedule']
    assert [r['year'] for r in rows] == list(range(1, 31))
    assert rows[0] == {
        'year': 1,
        'months': 12,
        'opening_balance': 10000000,
        'principal_paid': 14760,
        'interest_paid': 1598949,
        'closing_balance': 9985240,
    }
    assert rows[-1]['closing_balance'] == 0
    for before, after in zip(rows, rows[1:]):
        assert after['opening_balance'] == before['closing_balance']
    # Rounded per year, the columns add up to the totals within a rupee a year.
    assert abs(sum(r['principal_paid'] for r in rows) - 10000000) <= 30
    assert abs(sum(r['interest_paid'] for r in rows) - res['total_interest']) <= 30


def test_a_part_year_and_the_sheets_emi():
    res = loan_tools.emi_calculator(
        {'principal': 2058000, 'annual_rate_pct': 11, 'tenure_months': 30}
    )
    sheet = loan_tools.emi_calculator(
        {'principal': 2058000, 'annual_rate_pct': 11, 'tenure_months': 72}
    )

    assert [r['months'] for r in res['yearly_schedule']] == [12, 12, 6]
    assert res['display']['tenure'] == '30 months'
    assert (sheet['emi'], sheet['emi_exact']) == (39172, 39172.13)  # the sheet's ICICI EMI


def test_zero_rate():
    res = loan_tools.emi_calculator(
        {'principal': 120000, 'annual_rate_pct': 0, 'tenure_months': 12}
    )

    assert (res['emi'], res['total_interest'], res['total_payable']) == (10000, 0, 120000)
    assert (res['principal_pct'], res['interest_pct']) == (100, 0)


def test_balance_transfer():
    res = index.handler(
        {
            'principal': 500000,
            'annual_rate_pct': 14,
            'tenure_months': 36,
            'new_annual_rate_pct': 11,
        },
        EMI,
    )

    bt = res['balance_transfer']
    assert (bt['current_emi'], bt['new_emi'], bt['monthly_saving'], bt['total_saving']) == (
        17089,
        16369,
        719,
        25900,  # 719.44 a month x 36, from the unrounded EMIs
    )
    assert bt['current_emi'] == res['emi']
    assert bt['summary'].startswith(
        'Balance transfer of ₹5,00,000 over the remaining 3 years (36 months) from 14% to 11%: '
        'EMI ₹17,089 -> ₹16,369, saving ₹719 a month and ₹25,900 in all'
    )
    dearer = loan_tools.emi_calculator(
        {'principal': 500000, 'annual_rate_pct': 11, 'tenure_months': 36, 'new_annual_rate_pct': 14}
    )['balance_transfer']
    assert (dearer['monthly_saving'], dearer['total_saving']) == (-719, -25900)
    assert 'costing ₹719 a month and ₹25,900 in all' in dearer['summary']


@pytest.mark.parametrize(
    'event',
    [
        {'principal': '1,00,00,000', 'annual_rate_pct': '16%', 'tenure_months': '360'},
        {'principal': '₹ 1,00,00,000', 'annual_rate_pct': 16.0, 'tenure_years': '30'},
        {'principal': 'Rs. 10000000', 'annual_rate_pct': 16, 'tenure_months': 360, 'tenure_years': 30},
    ],
)
def test_numbers_as_text(event):
    assert loan_tools.emi_calculator(event)['emi'] == 134476


@pytest.mark.parametrize(
    ('event', 'error'),
    [
        ({'annual_rate_pct': 11, 'tenure_months': 60}, 'principal is required'),
        ({'principal': 0, 'annual_rate_pct': 11, 'tenure_months': 60}, 'principal must be more than 0'),
        ({'principal': 'ten lakh', 'annual_rate_pct': 11, 'tenure_months': 60}, 'principal must be a number'),
        ({'principal': True, 'annual_rate_pct': 11, 'tenure_months': 60}, 'principal must be a number'),
        ({'principal': 2e9, 'annual_rate_pct': 11, 'tenure_months': 60}, 'principal must be at most 1000000000'),
        ({'principal': 100000, 'tenure_months': 60}, 'annual_rate_pct is required'),
        ({'principal': 100000, 'annual_rate_pct': -1, 'tenure_months': 60}, 'annual_rate_pct must be at least 0'),
        ({'principal': 100000, 'annual_rate_pct': 75, 'tenure_months': 60}, 'annual_rate_pct must be at most 60'),
        ({'principal': 100000, 'annual_rate_pct': 11}, 'tenure_months (or tenure_years) is required'),
        ({'principal': 100000, 'annual_rate_pct': 11, 'tenure_months': 0}, 'the tenure must be 1 to 480 months'),
        ({'principal': 100000, 'annual_rate_pct': 11, 'tenure_months': 481}, 'the tenure must be 1 to 480 months'),
        ({'principal': 100000, 'annual_rate_pct': 11, 'tenure_months': 12.5}, 'tenure_months must be a whole'),
        ({'principal': 100000, 'annual_rate_pct': 11, 'tenure_years': 1.3}, 'tenure_years must make whole months'),
        (
            {'principal': 100000, 'annual_rate_pct': 11, 'tenure_months': 60, 'tenure_years': 4},
            'give tenure_months or tenure_years, not both',
        ),
        (
            {'principal': 100000, 'annual_rate_pct': 11, 'tenure_months': 60, 'new_annual_rate_pct': 'x'},
            'new_annual_rate_pct must be a number',
        ),
    ],
)
def test_bad_emi_inputs(event, error):
    res = index.handler(event, EMI)
    assert set(res) == {'error'}
    assert res['error'].startswith(error)


def test_the_systems_ids_are_ignored():
    """The agent adds project_id and user_id to every MCP tool call."""
    res = index.handler(
        {'principal': 100000, 'annual_rate_pct': 11, 'tenure_months': 60, 'project_id': 'p1', 'user_id': 'u-1'},
        EMI,
    )
    assert res['emi'] == 2174


# ------------------------------------------------------------------ foir_eligibility
def test_foir_eligibility_is_the_sheets():
    """(98,000 x 70% - 15,000) / per-lakh EMI(11%, 60) x 1 lakh = 24,65,226.61 -> 24,65,227."""
    res = index.handler(
        {
            'net_monthly_income': 98000,
            'existing_emis': 15000,
            'foir_pct': 70,
            'annual_rate_pct': 11,
            'tenure_months': 60,
        },
        FOIR,
    )

    assert (res['foir_limit'], res['max_emi'], res['max_loan'], res['per_lakh_emi']) == (
        68600,
        53600,
        2465227,
        2174.24,
    )
    assert res['summary'] == (
        'Maximum EMI ₹53,600 and maximum loan ₹24,65,227 at 70% FOIR and 11% p.a. over 5 years (60 months)'
    )
    json.dumps(res, ensure_ascii=False)


def test_foir_eligibility_as_varunikaloans_check_eligibility():
    """50% FOIR at 11%: 50,000 income and 10,000 of EMIs leave an EMI of 15,000 for 5 years."""
    res = loan_tools.foir_eligibility(
        {
            'net_monthly_income': 50000,
            'existing_emis': 10000,
            'foir_pct': 50,
            'annual_rate_pct': 11,
            'tenure_years': 5,
        }
    )

    assert (res['max_emi'], res['max_loan']) == (15000, 689896)
    assert res['display']['max_loan'] == '₹6,89,896'
    # The same loan back through the EMI calculator: an EMI of 15,000.
    assert loan_tools.emi_calculator(
        {'principal': res['max_loan'], 'annual_rate_pct': 11, 'tenure_years': 5}
    )['emi'] == 15000


def test_foir_eligibility_without_room():
    res = loan_tools.foir_eligibility(
        {'net_monthly_income': 40000, 'existing_emis': 25000, 'foir_pct': 50, 'annual_rate_pct': 11, 'tenure_years': 3}
    )

    assert (res['max_emi'], res['max_loan']) == (0, 0)
    assert res['summary'].endswith('the existing EMIs leave no room within the FOIR')


@pytest.mark.parametrize(
    ('change', 'error'),
    [
        ({'net_monthly_income': None}, 'net_monthly_income is required'),
        ({'foir_pct': 0}, 'foir_pct must be more than 0'),
        ({'foir_pct': 120}, 'foir_pct must be at most 100'),
        ({'existing_emis': -5}, 'existing_emis must be at least 0'),
        ({'annual_rate_pct': None}, 'annual_rate_pct is required'),
        ({'tenure_years': None}, 'tenure_months (or tenure_years) is required'),
    ],
)
def test_bad_foir_inputs(change, error):
    event = {'net_monthly_income': 50000, 'foir_pct': 50, 'annual_rate_pct': 11, 'tenure_years': 5, **change}
    assert loan_tools.foir_eligibility(event) == {'error': error}


# ------------------------------------------------------------------ loan_eligibility
def _matches(condition, item) -> bool:
    expr = condition.get_expression()
    op, values = expr['operator'], expr['values']
    if op == 'AND':
        return _matches(values[0], item) and _matches(values[1], item)
    value = item.get(values[0].name)
    if op == '=':
        return value == values[1]
    if op == 'begins_with':
        return isinstance(value, str) and value.startswith(values[1])
    raise AssertionError(f'unsupported key condition {op}')


class KeyTable:
    """The backend table: get_item and key-condition queries (one page), each call recorded."""

    def __init__(self, items):
        self.items = items
        self.calls = []

    def get_item(self, Key, ConsistentRead=None):
        self.calls.append(('get_item', Key['SK']))
        for item in self.items:
            if (item['PK'], item['SK']) == (Key['PK'], Key['SK']):
                return {'Item': copy.deepcopy(item)}
        return {}

    def query(self, KeyConditionExpression, **kwargs):
        self.calls.append(('query', None))
        return {'Items': [copy.deepcopy(i) for i in self.items if _matches(KeyConditionExpression, i)]}


def example_inputs(**profile):
    """The sheet's worked example as saved by PUT .../eligibility/inputs (synthetic applicant)."""
    return {
        'profile': {
            'pan': RAHUL_PAN,
            'name': RAHUL,
            'pincode': '400606',
            'company': 'Konkan Softworks Pvt Ltd',
            'employment_type': 'private_limited',
            'net_income': 98000.0,
            'other_income': [],
            **profile,
        },
        'cibil': {
            'score': 765,
            'enquiries': {'d30': 0, 'd60': 1, 'd90': 2, 'd120': 3},
            'tradelines': [
                {
                    'loan_type': 'personal',
                    'lender': 'Tamrapur Finance (sample)',
                    'outstanding': 250000.0,
                    'emi': 15000.0,
                    'status': 'active',
                    'action': 'obligate',
                    'source': 'manual',
                }
            ],
            'source': 'manual',
        },
        'loan': {'amount': 1500000.0, 'tenure_months': 60},
    }


def saved(applicant, inputs, pid='p1', expires_at=NOW + WEEK, updated_at='2026-09-21T10:00:00.000000+00:00'):
    """An ELIG# item as the backend writes it (numbers come back as Decimal)."""
    return {
        'PK': f'PROJ#{pid}',
        'SK': f'ELIG#{loan_tools.applicant_key(applicant)}',
        'applicant': applicant,
        'inputs': _to_ddb(inputs),
        'created_at': updated_at,
        'updated_at': updated_at,
        'expires_at': Decimal(expires_at),
    }


def run(table, monkeypatch, **event):
    monkeypatch.setattr(index, '_table', table)
    monkeypatch.setattr(loan_tools.time, 'time', lambda: NOW)
    return index.handler({'project_id': 'p1', **event}, ELIGIBILITY)


def at(result, lender_id):
    return next(r for r in result['per_lender'] if r['lender_id'] == lender_id)


def test_the_sheets_worked_example_from_the_saved_inputs(monkeypatch):
    """No documents: the entered income is used, ICICI Bank gives the sheet's numbers."""
    table = KeyTable([saved(RAHUL_PAN, example_inputs())])

    res = run(table, monkeypatch, applicant='bqxpd 4821k')

    icici = at(res, 'icici_bank')
    assert (icici['per_lakh_emi'], icici['foir_eligibility'], icici['multiplier_eligibility']) == (
        2174.24,
        2465226.61,
        2058000.0,
    )
    assert (icici['eligible_amount'], icici['tenure_months'], icici['emi']) == (2058000.0, 72, 39172.13)
    assert (icici['calculation_tenure_months'], icici['sources']['calculation_tenure_months']) == (60, 'policy')
    assert res['best_lender'] == 'ICICI Bank'
    assert res['summary'][0] == 'Best lender: ICICI Bank (lowest ROI among the lenders that cover ₹15,00,000)'
    assert (
        'ICICI Bank: Eligible – ₹20,58,000 at 11% over 72 months, EMI ₹39,172.13 (eligibility calculated '
        'over 60 months; EMI ₹44,745.91 over that tenure)'
    ) in res['summary']
    assert (
        'HDFC Bank: Eligible – ₹15,00,000 at 12% over 60 months, EMI ₹33,366.67; FOIR 70% · slab '
        "75,000–99,999 · CAT A · From Policy (SAMPLE: confirm with Varunika Loan Partners' HDFC grid)"
    ) in res['summary']
    assert 'Axis Bank: Not serviceable – Pincode 400606 is not serviceable by Axis Bank' in res['summary']
    assert res['file_check'] == {
        'used': False,
        'detail': 'no applicant with this name or PAN in the analysed documents',
        'applicant': None,
        'verdict': None,
    }
    assert res['notes'][0].startswith('File check not used')
    assert (res['applicant'], res['pan_masked']) == (RAHUL, 'XXXXXX821K')
    assert res['saved']['expires_at'] == '2026-09-28T14:13:20+00:00'
    assert (res['sample'], res['policy_label']) == (True, SAMPLE)
    assert 'sample policy' in res['assistant_instructions']
    assert table.calls[0] == ('get_item', f'ELIG#{loan_tools.applicant_key(RAHUL_PAN)}')
    json.dumps(res, ensure_ascii=False)


def test_the_file_checks_verified_salary_and_bank_emis(monkeypatch):
    """Rahul's documents: verified salary 82,500 replaces the entered 98,000, and the car loan's
    bank EMI of 8,200 (no tradeline for it) is counted as an obligation."""
    table = KeyTable(_items(rahul() + sneha()) + [saved(RAHUL_PAN, example_inputs(), pid='p1')])

    res = run(table, monkeypatch, applicant=RAHUL)

    assert res['file_check'] == {
        'used': True,
        'detail': 'verified figures from the file check',
        'applicant': RAHUL,
        'verdict': 'READY',
    }
    assert (res['income']['net_salary'], res['income']['net_salary_source']) == (82500.0, 'verified')
    assert res['obligations'] == 15000.0 + 8200.0
    (bank,) = res['obligation_details']['bank_statement_emis']
    assert (bank['amount'], bank['counted']) == (8200.0, True)
    icici = at(res, 'icici_bank')
    assert icici['multiplier_eligibility'] == 82500 * 21
    assert any('Entered net income ₹98,000 differs from the verified ₹82,500' in n for n in res['notes'])


def test_says_when_an_uploaded_list_is_not_applied(monkeypatch):
    """The backend applies the DSA's serviceability and company lists; this tool does not, and says so."""

    def header(kind, expires_at):
        return {'PK': 'PROJ#p1', 'SK': f'REFDATA#{kind}', 'upload_id': 'u1', 'expires_at': Decimal(expires_at)}

    note = 'list is not applied in this answer: the Eligibility page applies it, so its figures can differ'
    items = [saved(RAHUL_PAN, example_inputs())]
    assert not any(note in n for n in run(KeyTable(items), monkeypatch, applicant=RAHUL_PAN)['notes'])

    expired = [header('company_categories', NOW - 1), header('lender_branches', NOW + WEEK)]
    assert not any(note in n for n in run(KeyTable(items + expired), monkeypatch, applicant=RAHUL_PAN)['notes'])

    res = run(KeyTable(items + [header('company_categories', NOW + WEEK)]), monkeypatch, applicant=RAHUL_PAN)
    assert res['notes'][-1] == f'Your uploaded company categories {note}'
    both = [header('pincode_serviceability', NOW + WEEK), header('company_categories', NOW + WEEK)]
    res = run(KeyTable(items + both), monkeypatch, applicant=RAHUL_PAN)
    assert res['notes'][-1] == (
        'Your uploaded pincode serviceability and company categories lists are not applied in this answer: '
        'the Eligibility page applies them, so its figures can differ'
    )
    assert res['best_lender'] == 'ICICI Bank'


def test_inputs_saved_under_the_pan_are_found_by_name(monkeypatch):
    table = KeyTable([saved(RAHUL_PAN, example_inputs())])

    for applicant in (RAHUL, 'rahul  vijay DESHMUKH', 'Rahul Deshmukh', 'Rahul V. Deshmukh'):
        res = run(table, monkeypatch, applicant=applicant)
        assert res.get('best_lender') == 'ICICI Bank', applicant


def test_inputs_saved_under_the_name_are_found_by_pan(monkeypatch):
    table = KeyTable([saved(RAHUL, example_inputs())])

    assert run(table, monkeypatch, applicant=RAHUL_PAN)['best_lender'] == 'ICICI Bank'
    assert run(table, monkeypatch, applicant='CKRPK7314M') == {
        'error': "No saved eligibility inputs for this applicant in this project: open the applicant's "
        'Eligibility & lenders page (the CIBIL page), fill it in and save it, or give the full name or the PAN'
    }


def test_inputs_moved_to_the_pan_are_found_by_their_old_name(monkeypatch):
    """The backend moves inputs saved under a name to the PAN's key and keeps the name as an alias."""
    moved = {**saved(RAHUL_PAN, example_inputs(name=None)), 'aliases': ['Rahul V Deshmukh', 'XXXXXX821K']}
    table = KeyTable([moved])

    assert run(table, monkeypatch, applicant='Rahul V Deshmukh')['best_lender'] == 'ICICI Bank'
    assert run(table, monkeypatch, applicant='Rahul Vijay Deshmukh')['best_lender'] == 'ICICI Bank'
    assert 'error' in run(table, monkeypatch, applicant='XXXXXX821K')  # a masked PAN is not a name


def test_a_first_name_alone_does_not_match(monkeypatch):
    table = KeyTable([saved(RAHUL_PAN, example_inputs())])

    assert 'error' in run(table, monkeypatch, applicant='Rahul')


def test_the_same_applicant_saved_twice_is_one_applicant(monkeypatch):
    """An older save under the name and a newer one under the PAN: the newer one is used."""
    older = saved(RAHUL, example_inputs(pan=None, net_income=50000.0), updated_at='2026-09-20T10:00:00.000000+00:00')
    newer = saved(RAHUL_PAN, example_inputs(), updated_at='2026-09-21T10:00:00.000000+00:00')
    table = KeyTable([older, newer])

    res = run(table, monkeypatch, applicant='Rahul Deshmukh')

    assert res['income']['net_salary'] == 98000.0


def test_two_saved_applicants_with_the_name_are_ambiguous(monkeypatch):
    other = example_inputs(pan='AAAPZ1234Q', name='Rahul Vinod Deshmukh')
    table = KeyTable([saved(RAHUL_PAN, example_inputs()), saved('AAAPZ1234Q', other)])

    assert run(table, monkeypatch, applicant='Rahul Deshmukh') == {
        'error': '2 saved applicants match: give the full name or the PAN'
    }
    assert run(table, monkeypatch, applicant=RAHUL)['applicant'] == RAHUL


def test_expired_inputs_are_not_used(monkeypatch):
    table = KeyTable([saved(RAHUL_PAN, example_inputs(), expires_at=NOW - 1)])

    assert run(table, monkeypatch, applicant=RAHUL_PAN)['error'].startswith('No saved eligibility inputs')
    assert run(table, monkeypatch, applicant=RAHUL)['error'].startswith('No saved eligibility inputs')


def test_another_projects_inputs_are_never_read(monkeypatch):
    table = KeyTable([saved(RAHUL_PAN, example_inputs(), pid='p2')])

    assert 'error' in run(table, monkeypatch, applicant=RAHUL_PAN)


@pytest.mark.parametrize(
    ('event', 'error'),
    [
        ({'project_id': '  ', 'applicant': RAHUL}, 'project_id is required'),
        ({'applicant': None}, 'applicant is required: the full name or the PAN'),
        ({'applicant': '   '}, 'applicant is required: the full name or the PAN'),
        ({'applicant': 42}, 'applicant must be a string'),
        ({'applicant': 'Rahul\nDeshmukh'}, 'applicant must be a name or a PAN'),
        ({'applicant': 'x' * 201}, 'applicant must be a name or a PAN'),
    ],
)
def test_bad_eligibility_inputs(monkeypatch, event, error):
    table = KeyTable([])
    assert run(table, monkeypatch, **event) == {'error': error}
    assert table.calls == []


def test_a_failing_file_check_still_calculates(monkeypatch):
    table = KeyTable([saved(RAHUL_PAN, example_inputs())])

    def boom(event):
        raise RuntimeError('secret BQXPD4821K')

    monkeypatch.setattr(index, 'run_file_check', boom)
    res = run(table, monkeypatch, applicant=RAHUL_PAN)

    assert res['file_check']['detail'] == 'the file check failed'
    assert at(res, 'icici_bank')['eligible_amount'] == 2058000.0


def test_logs_hold_no_applicant_data(monkeypatch, capsys):
    table = KeyTable(_items(rahul()) + [saved(RAHUL_PAN, example_inputs())])

    run(table, monkeypatch, applicant=RAHUL)
    run(table, monkeypatch, applicant='Rohan Iyer')
    index.handler({'principal': 650000, 'annual_rate_pct': 11, 'tenure_months': 60}, EMI)

    out = capsys.readouterr().out
    assert 'loan_eligibility project=p1 saved=1 lenders=5 eligible=' in out
    for secret in ('Rahul', 'Deshmukh', RAHUL_PAN, '821K', '98000', '82500', '650000', 'Rohan'):
        assert secret not in out


def test_an_unexpected_error_names_the_tool_only(monkeypatch, capsys):
    monkeypatch.setattr(loan_tools, 'repayment', lambda *a: 1 / 0)

    res = index.handler({'principal': 100000, 'annual_rate_pct': 11, 'tenure_months': 60}, EMI)

    assert res == {'error': 'EMI calculation failed: ZeroDivisionError'}
    assert 'EMI calculation failed: ZeroDivisionError' in capsys.readouterr().out


def test_the_applicant_key_is_the_backends():
    """ELIG# keys: SHA-256 of 'pan:<PAN>' or 'name:<name, case and spacing ignored>', 40 hex digits."""
    pan_key = hashlib.sha256(b'pan:BQXPD4821K').hexdigest()[:40]
    name_key = hashlib.sha256(b'name:rahul vijay deshmukh').hexdigest()[:40]

    assert loan_tools.applicant_key(' bqxpd 4821k ') == loan_tools.applicant_key('BQXPD4821K') == pan_key
    assert loan_tools.applicant_key('  Rahul  Vijay DESHMUKH ') == name_key


# ------------------------------------------------------------------ schema vs handler
def _schema():
    return json.loads((HERE / 'schema.json').read_text(encoding='utf-8'))


def test_every_schema_tool_has_a_handler():
    names = [t['name'] for t in _schema()]

    assert names == [
        'run_file_check',
        'list_checklists',
        'emi_calculator',
        'foir_eligibility',
        'loan_eligibility',
    ]
    assert set(index._TOOLS) - set(names) == {'applicant_documents'}  # backend only


@pytest.mark.parametrize('tool', ['emi_calculator', 'foir_eligibility', 'loan_eligibility'])
def test_each_required_property_is_required_by_the_handler(monkeypatch, tool):
    spec = next(t for t in _schema() if t['name'] == tool)['inputSchema']
    full = {
        'emi_calculator': {'principal': 100000, 'annual_rate_pct': 11, 'tenure_months': 60},
        'foir_eligibility': {
            'net_monthly_income': 50000,
            'foir_pct': 50,
            'annual_rate_pct': 11,
            'tenure_months': 60,
        },
        'loan_eligibility': {'project_id': 'p1', 'applicant': RAHUL},
    }[tool]
    monkeypatch.setattr(index, '_table', KeyTable([saved(RAHUL_PAN, example_inputs())]))
    monkeypatch.setattr(loan_tools.time, 'time', lambda: NOW)

    assert set(full) <= set(spec['properties'])
    assert 'error' not in index.handler(full, _ctx(f'filecheck___{tool}'))
    for name in spec['required']:
        res = index.handler({k: v for k, v in full.items() if k != name}, _ctx(f'filecheck___{tool}'))
        assert name in res['error'], (tool, name, res)


@pytest.mark.parametrize('tool', ['emi_calculator', 'foir_eligibility'])
def test_every_schema_property_is_read(tool):
    """A bad value of any property is refused by name: the handler reads them all."""
    spec = next(t for t in _schema() if t['name'] == tool)['inputSchema']
    base = {
        'emi_calculator': {'principal': 100000, 'annual_rate_pct': 11, 'tenure_months': 60},
        'foir_eligibility': {
            'net_monthly_income': 50000,
            'foir_pct': 50,
            'annual_rate_pct': 11,
            'tenure_months': 60,
        },
    }[tool]
    for name, prop in spec['properties'].items():
        if name == 'project_id':
            continue
        assert prop['type'] in ('number', 'integer')
        res = index.handler({**base, name: 'not a number'}, _ctx(f'filecheck___{tool}'))
        assert res == {'error': f'{name} must be a number'}, name
