"""Unit tests for normalize.py (synthetic data only, no AWS).

Run: python -m pytest -q   (from this folder)
"""
import os
import sys
from datetime import date

os.environ['BACKEND_TABLE_NAME'] = 'test-table'
os.environ['AWS_DEFAULT_REGION'] = 'ap-south-1'
os.environ['AWS_REGION'] = 'ap-south-1'
os.environ['AWS_ACCESS_KEY_ID'] = 'testing'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'testing'

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', '..')))
sys.path.insert(0, HERE)

import pytest  # noqa: E402

from normalize import normalise_fields, normalise_fy, to_date, to_month, to_number  # noqa: E402
from normalize import normalise_category, normalise_channel  # noqa: E402
from normalize import (  # noqa: E402
    account_last4, enquiry_counts, normalise_employment_type, normalise_house_ownership, normalise_loan_type,
    normalise_mobile, normalise_pincode, normalise_registration, normalise_tradeline_status, to_count)
from tool_schema import FIELD_NAMES, LIST_FIELDS  # noqa: E402


def test_to_number():
    assert to_number('₹65,000.00') == 65000.0
    assert to_number('65000') == 65000.0
    assert to_number(65000) == 65000.0
    assert to_number(True) is None
    assert to_number(None) is None
    assert to_number('n/a') is None


def test_to_month():
    assert to_month('July 2026') == (2026, 7)
    assert to_month('Jul-2026') == (2026, 7)
    assert to_month('2026-07-01') == (2026, 7)
    assert to_month('2026-07') == (2026, 7)
    assert to_month('') is None


def test_to_date():
    assert to_date('01-Jun-2026') == date(2026, 6, 1)
    assert to_date('2026-06-01') == date(2026, 6, 1)
    assert to_date('01/06/2026') == date(2026, 6, 1)
    assert to_date('not a date') is None


def test_null_strings_and_bad_doc_type():
    out = normalise_fields({
        'doc_type': 'payslip',
        'applicant_name': 'null',
        'employer': 'N/A',
        'product': 'null',
        'loan_amount': '-',
        'month': 'none',
    })
    assert out['doc_type'] == 'other'
    assert out['applicant_name'] is None
    assert out['employer'] is None
    assert out['product'] is None
    assert out['loan_amount'] is None
    assert out['month'] is None


def test_every_field_present_and_empty_record():
    out = normalise_fields({})
    assert list(out) == FIELD_NAMES
    assert out['doc_type'] == 'other'
    assert all(out[k] == [] for k in LIST_FIELDS)
    assert all(out[k] is None for k in FIELD_NAMES if k != 'doc_type' and k not in LIST_FIELDS)
    assert normalise_fields(None)['doc_type'] == 'other'


def test_pan_aadhaar_month():
    out = normalise_fields({
        'doc_type': 'salary_slip',
        'pan': ' dmvpp 5928l ',
        'masked_aadhaar_last4': 'XXXX-XXXX-2619',
        'month': 'Aug 2026',
        'gross_salary': '₹80,000',
        'net_salary': 71200,
    })
    assert out['doc_type'] == 'salary_slip'
    assert out['pan'] == 'DMVPP5928L'
    assert out['masked_aadhaar_last4'] == '2619'
    assert out['month'] == '2026-08'
    assert out['gross_salary'] == 80000.0
    assert out['net_salary'] == 71200.0


@pytest.mark.parametrize('value', [
    'FY 2025-26', '2025-2026', '2025-26', '2025/26', 'AY 2026-27', 'A.Y. 2026-27',
    'Assessment Year 2026-2027', 'F.Y. 2025 – 26', 'FY 2025-26 (AY 2026-27)',
])
def test_financial_year(value):
    assert normalise_fy(value) == '2025-26'
    assert normalise_fields({'doc_type': 'form16_itr', 'financial_year': value})['financial_year'] == '2025-26'


@pytest.mark.parametrize('value', [None, '', 'null', '2025', '2025-27', 'FY', True])
def test_financial_year_invalid(value):
    assert normalise_fields({'doc_type': 'form16_itr', 'financial_year': value})['financial_year'] is None


def test_financial_year_century_rollover():
    assert normalise_fy('1999-00') == '1999-00'


def test_statement_period_and_salary_credits():
    out = normalise_fields({
        'doc_type': 'bank_statement',
        'statement_from': '01-Jun-2026',
        'statement_to': '31/08/2026',
        'salary_credits': [
            {'date': '01-Jun-2026', 'amount': '58,000.00', 'narration': 'NEFT CR/SAL DECCAN RETAIL PVT LTD/JUN26'},
            {'date': '2026-07-01', 'amount': 58000, 'narration': 'NEFT CR/SAL DECCAN RETAIL PVT LTD/JUL26'},
            {'date': 'sometime', 'amount': 'x', 'narration': 'odd'},
            'not a dict',
        ],
    })
    assert out['statement_from'] == '2026-06-01'
    assert out['statement_to'] == '2026-08-31'
    assert out['salary_credits'] == [
        {'date': '2026-06-01', 'amount': 58000.0, 'narration': 'NEFT CR/SAL DECCAN RETAIL PVT LTD/JUN26'},
        {'date': '2026-07-01', 'amount': 58000.0, 'narration': 'NEFT CR/SAL DECCAN RETAIL PVT LTD/JUL26'},
        {'date': 'sometime', 'amount': None, 'narration': 'odd'},
    ]


def test_unparseable_statement_date_is_kept():
    out = normalise_fields({'doc_type': 'bank_statement', 'statement_from': 'June 2026'})
    assert out['statement_from'] == 'June 2026'


# --------------------------------------------------------------------------- #
# obligations (synthetic demo applicants)
# --------------------------------------------------------------------------- #
def test_channel_and_category_normalisation():
    assert normalise_channel('e-NACH') == 'NACH'
    assert normalise_channel('Standing Instruction') == 'SI'
    assert normalise_channel('ach') == 'ACH'
    assert normalise_channel('other', 'ACH DR/MULSHI AUTO FINANCE/CAR LOAN EMI') == 'ACH'
    assert normalise_channel(None, 'NACH DR/XYZ FINSERV/PL EMI') == 'NACH'
    assert normalise_channel('NEFT', 'NEFT DR/RENT MAR26/VASANT JOSHI') == 'other'
    assert normalise_channel(None, 'BILLPAY/DR/ELECTRICITY BILL') == 'other'
    assert normalise_category('Loan EMI') == 'loan_emi'
    assert normalise_category('EMI') == 'loan_emi'
    assert normalise_category('SIP') == 'investment'
    assert normalise_category('credit card') == 'credit_card'
    assert normalise_category('utility') == 'utility'
    assert normalise_category('groceries') == 'other'
    assert normalise_category(None) == 'other'


def test_recurring_debits_normalised():
    out = normalise_fields({
        'doc_type': 'bank_statement',
        'recurring_debits': [
            {'date': '05-Mar-2026', 'amount': '8,200.00', 'narration': 'ACH DR/MULSHI AUTO FINANCE/CAR LOAN EMI',
             'channel': 'ach', 'category': 'Loan EMI'},
            {'date': '2026-03-03', 'amount': -18000, 'narration': 'NEFT DR/RENT MAR26/VASANT JOSHI',
             'category': 'rent'},
            {'date': '07/03/2026', 'amount': 5000, 'narration': 'ACH DR/SIP/SAMPLE ASSET MGMT MF',
             'channel': 'other', 'category': 'SIP'},
            {'date': '2026-03-11', 'amount': 'n/a', 'narration': 'no amount', 'category': 'utility'},
            {'date': '2026-03-12', 'amount': 0, 'narration': 'zero', 'category': 'utility'},
            'not a dict',
        ],
    })
    assert out['recurring_debits'] == [
        {'date': '2026-03-05', 'amount': 8200.0, 'narration': 'ACH DR/MULSHI AUTO FINANCE/CAR LOAN EMI',
         'channel': 'ACH', 'category': 'loan_emi'},
        {'date': '2026-03-03', 'amount': 18000.0, 'narration': 'NEFT DR/RENT MAR26/VASANT JOSHI',
         'channel': 'other', 'category': 'rent'},
        {'date': '2026-03-07', 'amount': 5000.0, 'narration': 'ACH DR/SIP/SAMPLE ASSET MGMT MF',
         'channel': 'ACH', 'category': 'investment'},
    ]


def test_declared_emis_rahul_and_requested_loan():
    out = normalise_fields({
        'doc_type': 'loan_application',
        'declared_net_salary': '82,500',
        'declared_existing_emis': [
            {'lender': 'Mulshi Auto Finance Ltd (sample)', 'loan_type': 'Car loan', 'amount': '₹8,200'},
            {'lender': 'None declared', 'amount': 0},
            {'lender': 'n/a', 'loan_type': '-', 'amount': None},
        ],
        'declared_total_existing_emi': '₹8,200',
        'loan_amount': '6,00,000',
        'loan_tenure_months': '48',
    })
    assert out['declared_existing_emis'] == [
        {'lender': 'Mulshi Auto Finance Ltd (sample)', 'loan_type': 'Car loan', 'amount': 8200.0}]
    assert out['declared_total_existing_emi'] == 8200.0
    assert out['loan_amount'] == 600000.0
    assert out['loan_tenure_months'] == 48.0


def test_declared_none_sneha():
    out = normalise_fields({'doc_type': 'loan_application', 'declared_existing_emis': [],
                            'declared_total_existing_emi': 0, 'loan_amount': 400000, 'loan_tenure_months': 36})
    assert out['declared_existing_emis'] == []
    assert out['declared_total_existing_emi'] == 0.0
    assert out['loan_tenure_months'] == 36.0


def test_declared_emi_list_not_a_list_is_empty():
    out = normalise_fields({'doc_type': 'loan_application', 'declared_existing_emis': 'Car loan 8200',
                            'recurring_debits': {'amount': 1}})
    assert out['declared_existing_emis'] == [] and out['recurring_debits'] == []


def test_total_row_copied_as_a_loan_is_moved_to_the_total():
    # the model copies the table's 'Total existing EMI' row as one more loan
    out = normalise_fields({'doc_type': 'loan_application', 'declared_existing_emis': [
        {'lender': 'Mulshi Auto Finance Ltd (sample)', 'loan_type': 'Car loan', 'amount': 8200},
        {'lender': 'Total existing EMI', 'amount': 8200},
    ]})
    assert out['declared_existing_emis'] == [
        {'lender': 'Mulshi Auto Finance Ltd (sample)', 'loan_type': 'Car loan', 'amount': 8200.0}]
    assert out['declared_total_existing_emi'] == 8200.0
    # an explicit total wins over the copied row
    out = normalise_fields({'doc_type': 'loan_application', 'declared_total_existing_emi': 11650,
                            'declared_existing_emis': [{'lender': 'Grand total', 'amount': 9999}]})
    assert out['declared_existing_emis'] == [] and out['declared_total_existing_emi'] == 11650.0
    # a lender whose name merely contains 'total' is a loan
    out = normalise_fields({'doc_type': 'loan_application', 'declared_existing_emis': [
        {'lender': 'Subtotal Finance Ltd', 'amount': 3450}]})
    assert [e['amount'] for e in out['declared_existing_emis']] == [3450.0]


# --------------------------------------------------------------------------- #
# CIBIL page fields (synthetic demo applicants)
# --------------------------------------------------------------------------- #
RAHUL_ADDRESS = 'Flat B-702, Sample Heights, Baner Road, Pune, Maharashtra – 411045'


def test_application_personal_fields_rahul():
    out = normalise_fields({
        'doc_type': 'loan_application',
        'applicant_name': 'Rahul Vijay Deshmukh',
        'employer': 'Konkan Softworks Pvt Ltd',
        'mobile': '+91 90000 00101',
        'dob': '14-02-1992',
        'current_address': RAHUL_ADDRESS,
        'permanent_address': 'House 21, Sample Wadi, Rajarampuri, Kolhapur, Maharashtra – 416008',
        'permanent_pincode': '416 008',
        'house_ownership': 'Rented',
        'employment_type': 'Salaried – Private Limited company',
    })
    assert out['mobile'] == '9000000101'
    assert out['dob'] == '1992-02-14'
    assert out['current_address'] == RAHUL_ADDRESS
    assert out['current_pincode'] == '411045'  # read from the address
    assert out['permanent_pincode'] == '416008'
    assert out['house_ownership'] == 'rented'
    assert out['employment_type'] == 'private_limited'
    assert out['company'] == 'Konkan Softworks Pvt Ltd'  # the employer
    assert out['employer'] == 'Konkan Softworks Pvt Ltd'


def test_permanent_address_same_as_current_amit():
    address = 'House 18, Sample Nagar Society, Chinchwad, Pune, Maharashtra – 411033'
    out = normalise_fields({'doc_type': 'loan_application', 'current_address': address,
                            'current_pincode': 411033, 'permanent_address': 'Same as current address',
                            'house_ownership': 'Owned', 'employment_type': 'LLP', 'company': 'Varad Logistics LLP'})
    assert out['permanent_address'] == address
    assert out['current_pincode'] == out['permanent_pincode'] == '411033'
    assert out['house_ownership'] == 'owned'
    assert out['employment_type'] == 'llp'
    assert out['employer'] == 'Varad Logistics LLP'  # filled from the company


@pytest.mark.parametrize('value, expected', [
    ('+91 90000 00101', '9000000101'), ('9000000101', '9000000101'), ('090000-00101', '9000000101'),
    ('919000000101', '9000000101'), (9000000101, '9000000101'), (9000000101.0, '9000000101'),
    ('+91 9XXXX X4421 (masked)', None), ('5000000101', None), ('90000 0010', None), (None, None), ('', None),
    (True, None)])
def test_mobile(value, expected):
    assert normalise_mobile(value) == expected


@pytest.mark.parametrize('value, expected', [
    ('411 045', '411045'), (411045, '411045'), (411045.0, '411045'), ('041104', None), ('4110', None),
    (None, None), (True, None)])
def test_pincode(value, expected):
    assert normalise_pincode(value) == expected


def test_pincode_not_found_in_address_stays_empty():
    out = normalise_fields({'doc_type': 'identity_details', 'current_address': 'Baner Road, Pune'})
    assert out['current_pincode'] is None and out['permanent_pincode'] is None


@pytest.mark.parametrize('value, expected', [
    ('private_limited', 'private_limited'), ('Private Limited', 'private_limited'),
    ('Salaried – Pvt. Ltd. company', 'private_limited'), ('Salaried - LLP', 'llp'),
    ('Limited Liability Partnership', 'llp'), ('Public Limited', 'public_limited'), ('Central Govt', 'government'),
    ('PSU', 'government'), ('Indian Army', 'defence'), ('Merchant Navy', 'merchant_navy'),
    ('Grade IV staff', 'grade_4'), ('Partnership/Proprietorship', 'partnership_proprietorship'),
    ('Proprietorship firm', 'partnership_proprietorship'), ('Salaried – private sector', None),
    ('Self-employed professional', None), (None, None)])
def test_employment_type(value, expected):
    assert normalise_employment_type(value) == expected


@pytest.mark.parametrize('value, expected', [
    ('Rented', 'rented'), ('On rent (leave and licence)', 'rented'), ('Owned', 'owned'), ('Self-owned', 'owned'),
    ('Owned by parents', 'parental'), ('Parental', 'parental'), ('Company provided', 'company_provided'),
    ('Company leased flat', 'company_provided'), ('Hostel', None), (None, None)])
def test_house_ownership(value, expected):
    assert normalise_house_ownership(value) == expected


def test_cibil_page_fields_are_kept_only_on_their_document_types():
    # a tenant's rent on the loan application is not rental income, a mobile on a slip is not read, ...
    out = normalise_fields({'doc_type': 'loan_application', 'monthly_rent': 18000, 'bonus': 5000,
                            'credit_score': 771, 'tradelines': [{'lender': 'X', 'emi': 100}],
                            'enquiries': [{'date': '2026-09-01'}], 'mobile': '9000000101'})
    assert out['monthly_rent'] is None and out['bonus'] is None and out['credit_score'] is None
    assert out['tradelines'] == [] and out['enquiries'] == []
    assert out['mobile'] == '9000000101'
    out = normalise_fields({'doc_type': 'salary_slip', 'mobile': '9000000101', 'company': 'Konkan Softworks',
                            'employer': 'Konkan Softworks Pvt Ltd', 'enquiries_30d': 2})
    assert out['mobile'] is None and out['company'] is None and out['enquiries_30d'] is None
    assert out['employer'] == 'Konkan Softworks Pvt Ltd'  # the file-check fields are untouched
    out = normalise_fields({'doc_type': 'other', 'monthly_pension': 30000, 'landlord_name': 'X'})
    assert out['monthly_pension'] is None and out['landlord_name'] is None


def test_salary_slip_bonus_and_incentive():
    out = normalise_fields({'doc_type': 'salary_slip', 'month': 'March 2026', 'gross_salary': 115000,
                            'bonus': '₹15,000', 'incentive': -2500})
    assert out['bonus'] == 15000.0 and out['incentive'] == 2500.0
    assert out['month'] == '2026-03'


def test_form16_other_and_rental_income():
    out = normalise_fields({'doc_type': 'form16_itr', 'financial_year': 'FY 2025-26',
                            'other_income_annual': '18,450', 'rental_income_annual': '1,44,000'})
    assert out['other_income_annual'] == 18450.0 and out['rental_income_annual'] == 144000.0


def test_rent_agreement_landlord_is_the_applicant():
    out = normalise_fields({'doc_type': 'rent_agreement', 'landlord_name': 'Vasant Ramchandra Joshi',
                            'tenant_name': 'Rahul Vijay Deshmukh', 'monthly_rent': '₹18,000',
                            'registration': 'Registered with the Sub-Registrar', 'agreement_from': '01-12-2025',
                            'agreement_to': '31/10/2026', 'employer': 'Retired'})
    assert out['applicant_name'] == 'Vasant Ramchandra Joshi'
    assert out['tenant_name'] == 'Rahul Vijay Deshmukh'
    assert out['monthly_rent'] == 18000.0
    assert out['registration'] == 'registered'
    assert (out['agreement_from'], out['agreement_to']) == ('2025-12-01', '2026-10-31')
    assert out['employer'] is None


@pytest.mark.parametrize('value, expected', [
    ('notarised', 'notarised'), ('Notarized', 'notarised'), ('Notary', 'notarised'), ('Registered', 'registered'),
    ('Registered (sub-registrar)', 'registered'), ('Unregistered, notarised', 'notarised'), ('Unregistered', None),
    ('Oral', None), (None, None)])
def test_rent_registration(value, expected):
    assert normalise_registration(value) == expected


def test_pension_slip():
    out = normalise_fields({'doc_type': 'pension_slip', 'applicant_name': 'Vasant Ramchandra Joshi',
                            'month': 'Aug-2026', 'monthly_pension': '32,400.00',
                            'employer': 'Tamrapur Urban Co-operative Bank Ltd.'})
    assert out['month'] == '2026-08' and out['monthly_pension'] == 32400.0
    assert out['employer'] is None  # the former employer is not the applicant's employer


def test_unparseable_cibil_page_dates_are_dropped():
    out = normalise_fields({'doc_type': 'identity_details', 'dob': 'Feb 1992'})
    assert out['dob'] is None
    out = normalise_fields({'doc_type': 'credit_report', 'report_date': 'Sep 18, 2026'})
    assert out['report_date'] == '2026-09-18'


# --------------------------------------------------------------------------- #
# credit reports
# --------------------------------------------------------------------------- #
def test_credit_report_score_and_bureau():
    out = normalise_fields({'doc_type': 'credit_report', 'applicant_name': 'RAHUL VIJAY DESHMUKH',
                            'pan': 'BQXPD4821K', 'bureau': 'TransUnion CIBIL', 'report_date': '18-Sep-2026',
                            'credit_score': '771', 'employer': 'Konkan Softworks'})
    assert out['bureau'] == 'CIBIL' and out['report_date'] == '2026-09-18' and out['credit_score'] == 771.0
    assert out['employer'] is None
    assert normalise_fields({'doc_type': 'credit_report', 'credit_score': -1})['credit_score'] is None
    assert normalise_fields({'doc_type': 'credit_report', 'bureau': 'CRIF High Mark'})['bureau'] == 'CRIF'
    assert normalise_fields({'doc_type': 'credit_report', 'bureau': 'Sample bureau'})['bureau'] is None


RAHUL_TRADELINES = [
    {'loan_type': 'Auto loan', 'lender': 'Mulshi Auto Finance Ltd', 'sanction_amount': '4,50,000',
     'outstanding': '1,65,115', 'emi': 8200, 'status': 'Active', 'account_last4': 'MAF/2022/07/004512',
     'overdue': 0, 'emis_paid': '50', 'emis_pending': 22.0, 'open_date': '10-07-2022',
     'last_payment_date': '05-Sep-2026'},
    {'loan_type': 'credit_card', 'lender': 'Tamrapur Urban Co-op Bank', 'sanction_amount': 150000,
     'outstanding': 12400, 'status': 'open', 'account_number': '5243 9100 0045 7731'},
    {'loan_type': '', 'lender': 'n/a'},
    'not a dict',
]


def test_tradelines_normalised_and_account_numbers_cut_to_last_4():
    out = normalise_fields({'doc_type': 'credit_report', 'tradelines': RAHUL_TRADELINES})
    assert out['tradelines'] == [
        {'loan_type': 'car_loan', 'lender': 'Mulshi Auto Finance Ltd', 'sanction_amount': 450000.0,
         'outstanding': 165115.0, 'emi': 8200.0, 'status': 'active', 'account_last4': '4512', 'overdue': 0.0,
         'emis_paid': 50, 'emis_pending': 22, 'open_date': '2022-07-10', 'last_payment_date': '2026-09-05'},
        {'loan_type': 'credit_card', 'lender': 'Tamrapur Urban Co-op Bank', 'sanction_amount': 150000.0,
         'outstanding': 12400.0, 'emi': None, 'status': 'active', 'account_last4': '7731', 'overdue': None,
         'emis_paid': None, 'emis_pending': None, 'open_date': None, 'last_payment_date': None},
    ]
    stored = str(out)
    for full in ('004512', 'MAF', '0045 7731', '5243'):
        assert full not in stored, f'{full!r} of an account number kept'


@pytest.mark.parametrize('value, expected', [
    ('personal_loan', 'personal_loan'), ('Personal Loan', 'personal_loan'), ('PL', 'personal_loan'),
    ('car', 'car_loan'), ('Two-wheeler loan', 'car_loan'), ('Housing loan', 'home_loan'),
    ('Loan against property', 'mortgage_loan'), ('Property loan', 'mortgage_loan'),
    ('Consumer durable loan', 'consumer_loan'), ('Education loan', 'education_loan'),
    ('App loan', 'application_loan'), ('Credit Card', 'credit_card'), ('Gold loan', 'other'), (None, None)])
def test_tradeline_loan_type(value, expected):
    assert normalise_loan_type(value) == expected


@pytest.mark.parametrize('value, expected', [
    ('Active', 'active'), ('Open', 'active'), ('Current', 'active'), ('Closed', 'closed'), ('Paid', 'closed'),
    ('Written-off', 'written_off'), ('Post (WO) settled', 'written_off'), ('Settled', 'settled'),
    ('Suit filed', 'other'), ('', None)])
def test_tradeline_status(value, expected):
    assert normalise_tradeline_status(value) == expected


@pytest.mark.parametrize('value, expected', [
    ('XXXXXXXX4821', '4821'), ('****1234', '1234'), ('4821', '4821'), ('LN-00045-ab12', 'AB12'), ('XXXX', None),
    (5243910000457731, '7731'), (4512.0, '4512'), ('', None), (None, None), (True, None)])
def test_account_last4(value, expected):
    assert account_last4(value) == expected


SNEHA_ENQUIRIES = [  # report date 2026-09-19
    {'date': '2026-09-05', 'lender': 'Konkan Finserv Ltd (sample)', 'purpose': 'Personal Loan'},
    {'date': '2026-08-28', 'lender': 'Godavari Credit Ltd (sample)', 'purpose': 'Personal Loan'},
    {'date': '2026-08-02', 'lender': 'Tamrapur Urban Co-op Bank', 'purpose': 'Credit Card'},
    {'date': '01-Aug-2026', 'lender': 'Deccan Consumer Finance (sample)', 'purpose': 'Consumer Loan'},
    {'date': '2026-07-10', 'lender': 'Konkan Finserv Ltd (sample)', 'purpose': 'Personal Loan'},
    {'date': '2026-06-25', 'lender': 'Pavana Capital (sample)', 'purpose': 'Personal Loan'},
    {'date': '2026-05-30', 'lender': 'Godavari Credit Ltd (sample)', 'purpose': 'Personal Loan'},
    {'date': '2025-11-14', 'lender': 'Tamrapur Urban Co-op Bank', 'purpose': 'Credit Card'},
    {'lender': None, 'purpose': 'x'},
]


def test_enquiry_counts_are_counted_from_the_listed_enquiries():
    out = normalise_fields({'doc_type': 'credit_report', 'report_date': '2026-09-19',
                            'enquiries': SNEHA_ENQUIRIES})
    assert len(out['enquiries']) == 8  # the entry with no date and no lender is dropped
    assert out['enquiries'][3] == {'date': '2026-08-01', 'lender': 'Deccan Consumer Finance (sample)',
                                   'purpose': 'Consumer Loan'}
    assert [out[k] for k in ('enquiries_30d', 'enquiries_60d', 'enquiries_90d', 'enquiries_120d')] == [2, 4, 6, 7]


def test_printed_enquiry_counts_win_and_stay_cumulative():
    out = normalise_fields({'doc_type': 'credit_report', 'report_date': '2026-09-19', 'enquiries': SNEHA_ENQUIRIES,
                            'enquiries_30d': 3})
    assert [out[k] for k in ('enquiries_30d', 'enquiries_60d', 'enquiries_90d', 'enquiries_120d')] == [3, 4, 6, 7]
    # a longer window never counts fewer enquiries than a shorter one
    out = normalise_fields({'doc_type': 'credit_report', 'enquiries_30d': 2, 'enquiries_60d': 1,
                            'enquiries_90d': '0', 'enquiries_120d': 4})
    assert [out[k] for k in ('enquiries_30d', 'enquiries_60d', 'enquiries_90d', 'enquiries_120d')] == [2, 2, 2, 4]


def test_enquiry_counts_need_the_report_date():
    out = normalise_fields({'doc_type': 'credit_report', 'enquiries': SNEHA_ENQUIRIES})
    assert all(out[k] is None for k in ('enquiries_30d', 'enquiries_60d', 'enquiries_90d', 'enquiries_120d'))
    assert enquiry_counts([{'date': None}], '2026-09-19') == {}
    # an enquiry after the report date is not counted
    assert enquiry_counts([{'date': '2026-09-20'}], '2026-09-19')['enquiries_120d'] == 0


@pytest.mark.parametrize('value, expected', [(2, 2), ('2', 2), (2.0, 2), (0, 0), (2.5, None), (-1, None),
                                             ('none', None), (None, None), (True, None)])
def test_to_count(value, expected):
    assert to_count(value) == expected
