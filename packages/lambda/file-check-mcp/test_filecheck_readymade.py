"""Bundled ready-made brand rule sets (Varunika Loan Partners / Varunika Loans).

checklists.json ships the default salaried PL checklist followed by the 22
brand rule sets converted from ready-made/checklists/checklists.json with
convert_checklists.convert_ready_made. These tests load the bundle, pin the
mapping rules on a small inline sample of the research format and, when the
research file is on this machine, check that the bundle is up to date.
"""

import copy
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import convert_checklists as cc  # noqa: E402
import engine  # noqa: E402
from test_filecheck_engine import amit, rahul, sneha  # noqa: E402

EN = '–'

# A ready-made research file (checklists/checklists.json of the research format):
# set READY_MADE_CHECKLISTS to it to check that the bundle is up to date.
READY_MADE_PATH = os.environ.get('READY_MADE_CHECKLISTS', '')

BRAND_IDS = [
    'vlp_pl_sal', 'vlp_pl_se', 'vlp_bl_se', 'vlp_hl_sal', 'vlp_hl_se',
    'vlp_lap_sal', 'vlp_lap_se', 'vlp_cl_sal', 'vlp_cl_se', 'vlp_el_stu_cosal',
    'vlp_el_stu_cose', 'vl_pl_sal', 'vl_pl_se', 'vl_bl_se', 'vl_hl_sal',
    'vl_hl_se', 'vl_lap_sal', 'vl_lap_se', 'vl_el_stu_cosal', 'vl_el_stu_cose',
    'vl_cl_sal', 'vl_cl_se',
]


@pytest.fixture(scope='module')
def catalog():
    return engine.load_catalog()


def _items(checklist):
    return {i['id']: i for i in checklist['items']}


def _rows(applicant):
    return {r['item_id']: r for r in applicant['checklist']}


def _kinds(reasons):
    return [r.split(f' {EN} ', 1)[0] for r in reasons]


# ------------------------------------------------------------------ bundle
def test_bundle_has_default_then_brand_rule_sets(catalog):
    assert engine.validate_catalog(catalog) == []
    assert catalog['default_checklist'] == 'salaried_personal_loan'
    assert [c['id'] for c in catalog['checklists']] == [
        'salaried_personal_loan', *BRAND_IDS]
    listed = engine.list_checklists(catalog)
    names = {c['id']: c['name'] for c in listed['checklists']}
    assert names['vlp_pl_sal'] == 'Varunika Loan Partners - Personal Loan - Salaried'
    assert names['vl_el_stu_cose'] == (
        'Varunika Loans - Education Loan - Student (self-employed co-applicant)')


def test_every_brand_rule_set_needs_a_person_before_ready(catalog):
    """Each brand list has blockers the rules cannot verify (e.g. PAN format,
    DOB), so none of them can say READY on machine checks alone."""
    for cid in BRAND_IDS:
        cl = engine.get_checklist(catalog, cid)
        items = cl['items']
        assert any(i['rule']['kind'] != 'manual' for i in items), cid
        assert all(
            i['doc_types'] == [] for i in items if i['rule']['kind'] == 'manual'
        ), cid
        assert any(
            i['rule']['kind'] == 'manual' and i['required'] for i in items
        ), cid
        a = engine.run_file_check(rahul(), cl)['applicants'][0]
        assert a['verdict'] == 'NOT READY', cid
        assert 'REVIEW' in _kinds(a['reasons']), cid


# ------------------------------------------------------------------ golden
def test_ss_pl_sal_demo_applicants(catalog):
    """Research file expected_outcomes for VLP-PL-SAL: all NOT READY; Sneha
    fails VLP-PL-03/04 + X07/X08, Amit fails X03, and VLP-PL-02 (address proof)
    needs a person for everyone."""
    cl = engine.get_checklist(catalog, 'vlp_pl_sal')

    r = engine.run_file_check(rahul(), cl)['applicants'][0]
    assert r['verdict'] == 'NOT READY'
    assert set(_kinds(r['reasons'])) == {'REVIEW'}
    assert r['missing_items'] == [] and r['mismatches'] == []
    rows = _rows(r)
    assert rows['vlp_pl_02']['status'] == 'REVIEW'
    assert rows['vlp_pl_02']['item'] == 'Address Proof (Passport/Utility Bill)'
    for iid in ('vlp_pl_01', 'vlp_pl_03', 'vlp_pl_04'):
        assert rows[iid]['status'] == 'PRESENT', iid
    assert all(c['status'] != 'MISMATCH' for c in r['consistency'])

    s = engine.run_file_check(sneha(), cl)['applicants'][0]
    assert s['verdict'] == 'NOT READY'
    assert s['missing_items'] == [
        'Salary slip: Jun 2026', 'Bank statement: Mar 2026, Apr 2026, May 2026']
    assert [m.split(':')[0] for m in s['mismatches']] == [
        'Declared net salary vs salary slips',
        'Declared net salary vs bank credits',
    ]
    assert 'gross appears declared as net' in s['mismatches'][0]  # X10

    a = engine.run_file_check(amit(), cl)['applicants'][0]
    assert a['verdict'] == 'NOT READY'
    assert a['missing_items'] == []
    assert [m.split(':')[0] for m in a['mismatches']] == ['PAN']


def test_ls_pl_sal_needs_employment_proof(catalog):
    cl = engine.get_checklist(catalog, 'vl_pl_sal')
    a = engine.run_file_check(rahul(), cl)['applicants'][0]
    assert a['verdict'] == 'NOT READY'
    assert (
        'Employment Proof: Company ID Card / Appointment Letter'
        in a['manual_review']
    )
    assert a['missing_items'] == [] and a['mismatches'] == []


# ------------------------------------------------------------------ mapping
SAMPLE = {
    'schema_version': '1.0.0',
    'captured': '2026-09-27',
    'doc_types': {'identity_proof': {}, 'address_proof': {}},
    'parameters': {'income_tolerance_pct': {'value': 7}},
    'cross_checks': [
        {'check_id': 'X02', 'name': 'PAN format', 'severity': 'blocker'},
        {'check_id': 'X12', 'name': 'EMI debits', 'severity': 'warning'},
    ],
    'checklists': [
        {
            'checklist_id': 'XX-PL-SAL',
            'brand_name': 'Example DSA',
            'product': 'Personal Loan',
            'product_key': 'personal_loan',
            'applicant_type': 'salaried',
            'source_url': 'https://example.invalid/pl',
            'documents': [
                {'rule_id': 'XX-01', 'label': 'Identity proof',
                 'doc_type': 'identity_proof', 'min_count': 1, 'period': None,
                 'applies_to': 'both', 'required': True, 'severity': 'blocker'},
                {'rule_id': 'XX-02', 'label': 'Address proof',
                 'doc_type': 'address_proof', 'min_count': 1, 'period': None,
                 'applies_to': 'both', 'required': True, 'severity': 'blocker'},
                {'rule_id': 'XX-03', 'label': 'Last 3 months salary slips',
                 'doc_type': 'salary_slip', 'min_count': 3,
                 'period': {'unit': 'month', 'length': 3},
                 'applies_to': 'salaried', 'required': True,
                 'severity': 'blocker'},
                {'rule_id': 'XX-04', 'label': 'Last 6 months bank statement',
                 'doc_type': 'bank_statement', 'min_count': 1,
                 'period': {'unit': 'month', 'length': 6},
                 'applies_to': 'both', 'required': True, 'severity': 'blocker'},
                {'rule_id': 'XX-05', 'label': 'Last 2 years ITR',
                 'doc_type': 'itr', 'min_count': 2,
                 'period': {'unit': 'fy', 'length': 2},
                 'applies_to': 'salaried', 'required': True,
                 'severity': 'warning'},
                {'rule_id': 'XX-06', 'label': 'Co-applicant KYC',
                 'doc_type': 'kyc', 'min_count': 1, 'period': None,
                 'applies_to': 'co_applicant', 'required': True,
                 'severity': 'blocker'},
                {'rule_id': 'XX-07', 'label': 'Collateral papers',
                 'doc_type': 'collateral_documents', 'min_count': 1,
                 'period': None,
                 'applies_to': 'conditional(loan.amount > 750000)',
                 'required': False, 'severity': 'warning'},
            ],
            'checks': [
                {'check_id': 'XX-E01', 'kind': 'eligibility',
                 'verbatim': 'Minimum income 25,000/month',
                 'severity': 'blocker'},
                {'check_id': 'XX-E02', 'kind': 'eligibility',
                 'verbatim': 'Credit score 750+', 'severity': 'warning'},
            ],
            'cross_check_ids': ['X01', 'X02', 'X03', 'X06', 'X10', 'X11', 'X12'],
        }
    ],
}


def test_ready_made_mapping_rules():
    assert cc.is_ready_made(SAMPLE)
    catalog, warnings = cc.convert(copy.deepcopy(SAMPLE))
    assert warnings == []
    assert engine.validate_catalog(catalog) == []
    assert catalog['default_checklist'] == 'xx_pl_sal'
    cl = catalog['checklists'][0]
    assert cl['name'] == 'Example DSA - Personal Loan - Salaried'
    assert cl['product'] == 'personal_loan'
    assert cl['tolerance_pct'] == 7
    items = _items(cl)
    # X12 is computed by the engine now (no manual item)
    assert list(items) == [
        'xx_01', 'xx_02', 'xx_03', 'xx_04', 'xx_05', 'xx_06', 'xx_07',
        'xx_e01', 'xx_e02', 'x02',
    ]
    assert items['xx_01'] == {
        'id': 'xx_01', 'label': 'Identity proof',
        'doc_types': ['identity_details'], 'required': True,
        'rule': {'kind': 'present'},
    }
    manual = {'kind': 'manual'}
    # Address proof variants cannot be told apart from PAN / Aadhaar
    assert items['xx_02']['rule'] == manual and items['xx_02']['required']
    assert items['xx_03']['rule'] == {
        'kind': 'monthly', 'months': 3, 'field': 'month'}
    assert items['xx_03']['missing_label'] == 'Salary slip'
    assert items['xx_04']['rule']['kind'] == 'period'
    assert items['xx_04']['rule']['months'] == 6
    # ITR for 2 FYs: two documents; severity warning never blocks
    assert items['xx_05']['doc_types'] == ['form16_itr']
    assert items['xx_05']['rule'] == {'kind': 'present', 'min_count': 2}
    assert items['xx_05']['required'] is False
    # Co-applicant documents need a person (the engine has no roles)
    assert items['xx_06']['rule'] == manual and items['xx_06']['required']
    assert items['xx_07']['rule'] == manual
    assert items['xx_07']['required'] is False
    assert items['xx_07']['label'] == (
        'Collateral papers (only if loan.amount > 750000)')
    assert items['xx_e01']['label'] == (
        'Eligibility: Minimum income 25,000/month')
    assert items['xx_e01']['required'] is True
    assert items['xx_e02']['required'] is False
    assert items['x02']['label'] == 'Cross-check: PAN format'
    assert items['x02']['required'] is True
    # X01/X03/X06/X12 map to engine checks; X10/X11 are covered elsewhere.
    # Slips + Form-16/ITR add the INFO-only Form-16 gross check.
    assert cl['consistency_checks'] == [
        'pan', 'applicant_name', 'employer', 'employer_vs_bank_credits',
        'form16_vs_slip_gross', 'declared_emis_vs_bank_debits',
    ]
    assert 'foir' not in cl  # no X14 and no parameters.foir in the sample


def test_ready_made_unknown_applies_to_warns():
    data = copy.deepcopy(SAMPLE)
    data['checklists'][0]['documents'][0]['applies_to'] = 'self_employed'
    catalog, warnings = cc.convert(data)
    assert warnings == ["checklist XX-PL-SAL: XX-01 applies to 'self_employed'"]
    item = _items(catalog['checklists'][0])['xx_01']
    assert item['rule'] == {'kind': 'manual'} and item['required'] is False


def test_generic_shapes_are_not_ready_made():
    assert not cc.is_ready_made({'checklists': [{'name': 'X', 'docs': []}]})
    assert not cc.is_ready_made([{'name': 'X'}])


# ------------------------------------------------------------------ freshness
@pytest.mark.skipif(
    not os.path.exists(READY_MADE_PATH),
    reason='ready-made research file not on this machine',
)
def test_bundle_matches_research_file(catalog):
    with open(READY_MADE_PATH, encoding='utf-8') as fh:
        data = json.load(fh)
    converted, warnings = cc.convert(data)
    assert warnings == []
    assert converted['checklists'] == catalog['checklists'][1:], (
        'checklists.json is stale; regenerate it (see convert_checklists.py)'
    )


# ------------------------------------------------------------------ FOIR
FOIR_PARAMS = {
    'label_result': 'indicative; lender policy decides',
    'by_brand': {
        'XX': [
            {'value': 0.5, 'where': '/check-eligibility', 'verbatim': 'Calculated at 50% FOIR',
             'url': 'https://example.invalid/check-eligibility'},
            {'value': 0.7, 'where': '/calculator, eligibility tab', 'verbatim': 'FOIR 70%',
             'url': 'https://example.invalid/calculator'},
        ],
        'YY': [{'value': 0.7, 'where': 'Calculator', 'verbatim': 'FOIR 70%',
                'url': 'https://example.invalid/yy'}],
    },
    'default_for_checker': {'XX': 0.5, 'YY': 0.7, 'basis': 'DEMO-POLICY'},
}


def test_ready_made_foir_from_research_parameters():
    data = copy.deepcopy(SAMPLE)
    data['parameters']['foir'] = FOIR_PARAMS
    data['checklists'][0]['brand'] = 'XX'
    data['checklists'][0]['cross_check_ids'].append('X14')
    yy = copy.deepcopy(data['checklists'][0])
    yy.update(checklist_id='YY-PL-SAL', brand='YY', brand_name='Other DSA')
    data['checklists'].append(yy)
    catalog, warnings = cc.convert(data)
    assert warnings == []
    assert engine.validate_catalog(catalog) == []
    xx, yy_cl = catalog['checklists']
    assert xx['consistency_checks'][-2:] == ['declared_emis_vs_bank_debits', 'foir']
    assert xx['foir'] == {
        'value': 0.5,
        'hard_limit': False,
        'basis': 'DEMO-POLICY',
        'source': "Example DSA /check-eligibility: 'Calculated at 50% FOIR'",
        'url': 'https://example.invalid/check-eligibility',
        'alternatives': [{'value': 0.7,
                          'source': "Example DSA /calculator, eligibility tab: 'FOIR 70%'",
                          'url': 'https://example.invalid/calculator'}],
        'note': 'Example DSA also shows 70% (/calculator, eligibility tab); 50% is used '
                'for eligibility. Confirm with the lender.',
    }
    assert yy_cl['foir'] == {
        'value': 0.7, 'hard_limit': False, 'basis': 'PUBLISHED',
        'source': "Other DSA Calculator: 'FOIR 70%'", 'url': 'https://example.invalid/yy'}


def test_bundle_foir_per_brand(catalog):
    """SS 70% (calculator); LS 50% for eligibility with the 70% calculator noted;
    the default demo checklist 70%. X12/X14 are engine checks, not manual items."""
    default = engine.get_checklist(catalog, 'salaried_personal_loan')
    assert default['foir']['value'] == 0.7
    for cid in BRAND_IDS:
        cl = engine.get_checklist(catalog, cid)
        assert cl['foir']['hard_limit'] is False, cid
        assert {'declared_emis_vs_bank_debits', 'foir'} <= set(cl['consistency_checks']), cid
        assert not {'x12', 'x14'} & {i['id'] for i in cl['items']}, cid
        if cid.startswith('vlp_'):
            assert cl['foir']['value'] == 0.7, cid
            assert "'FOIR 70%'" in cl['foir']['source'], cid
        else:
            assert cl['foir']['value'] == 0.5, cid
            assert [a['value'] for a in cl['foir']['alternatives']] == [0.7], cid
            assert '70% (/calculator, eligibility tab)' in cl['foir']['note'], cid
    assert 'Car Loan eligibility' in engine.get_checklist(catalog, 'vl_cl_sal')['foir']['source']


def test_ls_pl_sal_foir_at_50_percent(catalog):
    cl = engine.get_checklist(catalog, 'vl_pl_sal')
    a = engine.run_file_check(rahul(), cl)['applicants'][0]
    f = a['foir']
    assert (f['foir_limit'], f['existing_emis'], f['net_monthly_income']) == (0.5, 8200, 82500)
    assert f['max_new_emi'] == 33050  # 0.5 x 82,500 - 8,200
    assert f['status'] == 'OK' and f['label'] == "indicative — the lender's policy decides"
    assert 'FOIR 50%' in f['detail']
    assert f['limit_alternatives'][0]['value'] == 0.7
    rows = {c['check_id']: c for c in a['consistency']}
    assert rows['declared_emis_vs_bank_debits']['status'] == 'OK'
