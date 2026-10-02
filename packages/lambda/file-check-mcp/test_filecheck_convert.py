"""Converter tests: three synthetic ready-made checklist shapes."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

import convert_checklists as cc  # noqa: E402
import engine  # noqa: E402

PRODUCTS_SHAPE = {
    'products': [
        {
            'name': 'Personal Loan',
            'applicant_type': 'Salaried',
            'documents': [
                'Loan application form',
                'PAN card',
                'Salary slips (last 3 months)',
                'Bank statement - 6 months',
                'Form 16',
            ],
        },
        {
            'product': 'Business Loan',
            'customer_type': 'Self-employed',
            'required_documents': ['Application form', 'ITR for 2 years',
                                   'GST certificate'],
        },
    ]
}

MAPPING_SHAPE = {
    'Home Loan': ['KYC documents', 'Pay slips 6 months', 'Property papers'],
    'Car Loan': ['Aadhaar', 'Bank statement'],
}

DICT_DOCS_SHAPE = [
    {
        'title': 'PL Salaried',
        'profile': 'salaried',
        'items': [
            {'name': 'Last 6 months bank statement', 'mandatory': True},
            {'document': 'Salary slips (3 months)'},
            {'label': 'Form 16', 'required': False},
            {'title': 'PAN card'},
            {'name': 'Property papers', 'mandatory': True},
            {'name': 'Salary slips', 'months': 2},
            {'name': 'Bank statement', 'period': '12 months'},
        ],
    }
]


def _items(catalog, cid):
    cl = next(c for c in catalog['checklists'] if c['id'] == cid)
    return cl, {i['id']: i for i in cl['items']}


def test_products_list_shape():
    catalog, warnings = cc.convert(PRODUCTS_SHAPE)
    assert engine.validate_catalog(catalog) == []
    assert catalog['default_checklist'] == 'personal_loan'
    cl, items = _items(catalog, 'personal_loan')
    assert cl['applicant_type'] == 'Salaried'
    assert items['loan_application_form']['doc_types'] == ['loan_application']
    assert items['pan_card']['doc_types'] == ['identity_details']
    slips = items['salary_slips_last_3_months']
    assert slips['doc_types'] == ['salary_slip']
    assert slips['rule'] == {'kind': 'monthly', 'months': 3, 'field': 'month'}
    bank = items['bank_statement_6_months']
    assert bank['rule']['kind'] == 'period' and bank['rule']['months'] == 6
    assert items['form_16']['doc_types'] == ['form16_itr']
    assert cl['consistency_checks'] == engine.CHECK_IDS

    bl, bitems = _items(catalog, 'business_loan')
    assert bl['applicant_type'] == 'Self-employed'
    assert bitems['itr_for_2_years']['rule'] == {'kind': 'present'}
    assert bitems['gst_certificate']['rule'] == {'kind': 'manual'}
    # Mandatory by default: an unverifiable document keeps NOT READY
    assert bitems['gst_certificate']['required'] is True
    assert bl['consistency_checks'] == ['pan', 'aadhaar_last4', 'applicant_name']
    assert any(w.startswith('unmapped: GST certificate') for w in warnings)


def test_name_to_documents_mapping_shape():
    catalog, warnings = cc.convert(MAPPING_SHAPE)
    assert engine.validate_catalog(catalog) == []
    assert [c['id'] for c in catalog['checklists']] == ['home_loan', 'car_loan']
    assert catalog['default_checklist'] == 'home_loan'
    _, items = _items(catalog, 'home_loan')
    assert items['kyc_documents']['doc_types'] == ['identity_details']
    assert items['pay_slips_6_months']['rule']['months'] == 6
    assert items['property_papers']['rule'] == {'kind': 'manual'}
    _, car = _items(catalog, 'car_loan')
    assert car['aadhaar']['doc_types'] == ['identity_details']
    assert car['bank_statement']['rule']['months'] == 6  # default
    assert warnings == ['unmapped: Property papers (Home Loan); '
                        'added as a manual review item']


def test_list_with_dict_documents():
    catalog, warnings = cc.convert(DICT_DOCS_SHAPE)
    assert engine.validate_catalog(catalog) == []
    cl, items = _items(catalog, 'pl_salaried')
    assert cl['applicant_type'] == 'salaried'
    bank = items['last_6_months_bank_statement']
    assert bank['doc_types'] == ['bank_statement']
    assert bank['rule'] == {
        'kind': 'period', 'months': 6,
        'from_field': 'statement_from', 'to_field': 'statement_to',
    }
    assert bank['required'] is True
    assert bank['missing_label'] == 'Bank statement'
    slips = items['salary_slips_3_months']
    assert slips['rule']['months'] == 3 and slips['required'] is True
    assert items['form_16']['required'] is False
    assert items['pan_card']['doc_types'] == ['identity_details']
    prop = items['property_papers']
    assert prop['rule'] == {'kind': 'manual'}
    assert prop['required'] is True  # mandatory kept: blocks READY
    assert prop['doc_types'] == []
    assert items['salary_slips']['rule']['months'] == 2  # explicit months
    assert items['bank_statement']['rule']['months'] == 12  # '12 months'
    assert any(w.startswith('unmapped: Property papers') for w in warnings)


def test_duplicate_ids_get_suffixes():
    catalog, _ = cc.convert({'PL': ['PAN card', 'PAN card'],
                             'pl': ['Aadhaar']})
    assert [c['id'] for c in catalog['checklists']] == ['pl', 'pl_2']
    _, items = _items(catalog, 'pl')
    assert sorted(items) == ['pan_card', 'pan_card_2']


def test_converted_catalog_runs_in_engine():
    catalog, _ = cc.convert(DICT_DOCS_SHAPE)
    checklist = engine.get_checklist(catalog)
    res = engine.run_file_check([], checklist, documents=[])
    assert res['overall_verdict'] == 'NOT READY'


def test_no_products_is_an_error():
    with pytest.raises(ValueError):
        cc.convert({'products': []})


def _default_only_catalog(tmp_path):
    """The bundled catalog reduced to its default checklist, written to disk."""
    full = engine.load_catalog()
    base = {**full, 'checklists': [engine.get_checklist(full)]}
    path = tmp_path / 'base.json'
    path.write_text(json.dumps(base))
    return base, str(path)


def test_merge_keeps_base_default(tmp_path, capsys):
    base, base_path = _default_only_catalog(tmp_path)
    converted, _ = cc.convert(MAPPING_SHAPE)
    merged, warnings = cc.merge(base, converted)
    assert merged['default_checklist'] == 'salaried_personal_loan'
    assert [c['id'] for c in merged['checklists']] == [
        'salaried_personal_loan', 'home_loan', 'car_loan']
    assert engine.validate_catalog(merged) == []
    assert warnings == []

    # CLI: duplicate ids are skipped with a warning on stderr
    inp = tmp_path / 'ready.json'
    inp.write_text(json.dumps({'checklists': [
        {'name': 'Salaried Personal Loan', 'docs': ['PAN card']},
        {'name': 'Gold Loan', 'docs': ['Gold valuation report', 'KYC']},
    ]}))
    out = tmp_path / 'out.json'
    rc = cc.main([str(inp), '-o', str(out), '--merge', base_path])
    assert rc == 0
    result = json.loads(out.read_text())
    assert result['default_checklist'] == 'salaried_personal_loan'
    assert [c['id'] for c in result['checklists']] == [
        'salaried_personal_loan', 'gold_loan']
    err = capsys.readouterr().err
    assert "skipped 'salaried_personal_loan'" in err
    assert 'unmapped: Gold valuation report' in err


def test_cli_stdout(tmp_path, capsys):
    inp = tmp_path / 'ready.json'
    inp.write_text(json.dumps(MAPPING_SHAPE))
    assert cc.main([str(inp)]) == 0
    captured = capsys.readouterr()
    catalog = json.loads(captured.out)
    assert catalog['default_checklist'] == 'home_loan'
    assert 'warning: unmapped: Property papers' in captured.err


def test_keyword_word_boundaries():
    catalog, warnings = cc.convert({'X': ['Company registration', 'Nitrogen test']})
    _, items = _items(catalog, 'x')
    assert all(i['rule'] == {'kind': 'manual'} for i in items.values())
    assert len(warnings) == 2


def test_merge_replace_updates_existing_ids_but_never_the_default(tmp_path):
    base, _ = _default_only_catalog(tmp_path)
    first, _ = cc.convert(MAPPING_SHAPE)
    merged, _ = cc.merge(base, first)
    changed = json.loads(json.dumps(first))
    changed['checklists'][0]['name'] = 'Home Loan v2'
    changed['checklists'].append(
        {**json.loads(json.dumps(base['checklists'][0])), 'name': 'Replaced?'})
    again, warnings = cc.merge(merged, changed)
    assert [c['name'] for c in again['checklists']][1] == 'Home Loan'
    assert "skipped 'home_loan': already in the base catalog" in warnings
    replaced, warnings = cc.merge(merged, changed, replace=True)
    assert [c['id'] for c in replaced['checklists']] == [
        'salaried_personal_loan', 'home_loan', 'car_loan']
    assert replaced['checklists'][1]['name'] == 'Home Loan v2'
    assert replaced['checklists'][0]['name'] == 'Personal Loan - Salaried'
    assert warnings == ["skipped 'salaried_personal_loan': already in the base catalog"]
