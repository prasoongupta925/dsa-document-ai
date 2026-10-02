"""Unit tests for grounding.py with the interfaces section 12 raw misreads (synthetic data only).

Run: python -m pytest -q   (from this folder)
"""
import os
import sys

os.environ['BACKEND_TABLE_NAME'] = 'test-table'
os.environ['AWS_DEFAULT_REGION'] = 'ap-south-1'
os.environ['AWS_REGION'] = 'ap-south-1'
os.environ['AWS_ACCESS_KEY_ID'] = 'testing'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'testing'

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', '..')))
sys.path.insert(0, HERE)

from grounding import (  # noqa: E402
    PAN_RE,
    field_pages,
    ground_fields,
    inr,
    lev,
    printed_mobiles,
    unverified_numbers,
    with_row_pages,
)
from normalize import normalise_fields  # noqa: E402

IDENTITY_TEXT = (
    'IDENTITY DETAILS - SELF DECLARATION\n'
    'Full name\nRahul Vijay Deshmukh\n'
    'PAN\nBQXPD4821K\n'
    'Aadhaar (masked)\nXXXX-XXXX-7304\n'
)

SLIP_TEXT = (
    'Salary Slip - Page 1\nVarad Logistics\n'
    'Salary slip for the month of August 2026\n'
    'Employee name\nAmit Suresh Patil\nPAN\nDMVPP5928L\n'
    'Gross Earnings\n80,000\nNET PAY (take-home)\n₹71,200\n'
)

FORM16_TEXT = (
    'FORM-16 / ITR SUMMARY FY 2025-26 (AY 2026-27)\n'
    'Employee\nRahul Vijay Deshmukh\nPAN of the employee\nBQXPD4821K\n'
    'Employer\nKonkan Softworks Pvt Ltd\n1. Gross salary\n11,40,000\n'
)

BANK_TEXT = (
    'STATEMENT OF ACCOUNT\nAccount holder\nRAHUL VIJAY DESHMUKH\n'
    'Period: 01-Mar-2026 to 31-Aug-2026\n'
    'Reference PAN on file: BQXPD4821K\n'
    '01-Mar-2026\nNEFT CR/SAL KONKAN SOFTWORKS/MAR26\n\n82,500.00\n'
)


def test_lev_and_inr():
    assert lev('BQXPD4821K', 'BQXP4821K') == 1
    assert lev('DMVPP5926L', 'DMVPP5928L') == 1
    assert lev('', 'abc') == 3
    assert inr(600000) == '₹6,00,000'
    assert inr(1140000) == '₹11,40,000'
    assert inr(82500.5) == '₹82,500.50'
    assert inr(-950) == '-₹950'
    assert inr(None) == '–'
    assert PAN_RE.findall('pan BQXPD4821K and bqxpd4821k') == ['BQXPD4821K']


def test_pan_missing_letter_is_corrected_with_note():
    fields = normalise_fields({'doc_type': 'identity_details', 'pan': 'BQXP4821K',
                               'masked_aadhaar_last4': '7304', 'applicant_name': 'Rahul Vijay Deshmukh'})
    out, notes = ground_fields(fields, IDENTITY_TEXT)
    assert out['pan'] == 'BQXPD4821K'
    assert any('PAN corrected' in n and 'BQXPD4821K' in n for n in notes)
    assert fields['pan'] == 'BQXP4821K', 'input dict must not be mutated'


def test_pan_extra_letter_is_corrected():
    fields = normalise_fields({'doc_type': 'salary_slip', 'pan': 'DMVPPS5928L',
                               'gross_salary': 80000, 'net_salary': 71200})
    out, notes = ground_fields(fields, SLIP_TEXT)
    assert out['pan'] == 'DMVPP5928L'
    assert len(notes) == 1


def test_pan_filled_when_absent_and_single_pan_in_text():
    out, notes = ground_fields(normalise_fields({'doc_type': 'form16_itr'}), FORM16_TEXT)
    assert out['pan'] == 'BQXPD4821K'
    assert notes == ["PAN filled from text layer: 'BQXPD4821K'"]


def test_pan_not_filled_on_bank_statement():
    out, notes = ground_fields(normalise_fields({'doc_type': 'bank_statement'}), BANK_TEXT)
    assert out['pan'] is None
    assert not any('PAN' in n for n in notes)


def test_pan_not_filled_when_text_has_two_pans():
    text = IDENTITY_TEXT + 'Co-applicant PAN\nCKRPK7314M\n'
    out, _ = ground_fields(normalise_fields({'doc_type': 'identity_details'}), text)
    assert out['pan'] is None


def test_correct_pan_is_kept():
    out, notes = ground_fields(normalise_fields({'doc_type': 'identity_details', 'pan': 'BQXPD4821K'}),
                               IDENTITY_TEXT)
    assert out['pan'] == 'BQXPD4821K'
    assert notes == []


def test_aadhaar_corrected():
    out, notes = ground_fields(
        normalise_fields({'doc_type': 'identity_details', 'pan': 'BQXPD4821K', 'masked_aadhaar_last4': '7340'}),
        IDENTITY_TEXT)
    assert out['masked_aadhaar_last4'] == '7304'
    assert any('Aadhaar last-4 corrected' in n for n in notes)


def test_form16_gross_extra_zero_is_corrected():
    fields = normalise_fields({'doc_type': 'form16_itr', 'pan': 'BQXPD4821K', 'gross_salary': 11400000,
                               'financial_year': 'FY 2025-26', 'product': 'null'})
    assert fields['product'] is None
    out, notes = ground_fields(fields, FORM16_TEXT)
    assert out['gross_salary'] == 1140000.0
    assert any('gross_salary corrected' in n and '₹11,40,000' in n for n in notes)


def test_amount_divided_and_salary_credit_fixed():
    fields = normalise_fields({'doc_type': 'bank_statement', 'salary_credits': [
        {'date': '01-Mar-2026', 'amount': 8250, 'narration': 'NEFT CR/SAL KONKAN SOFTWORKS/MAR26'}]})
    out, notes = ground_fields(fields, BANK_TEXT)
    assert out['salary_credits'][0]['amount'] == 82500.0
    assert out['salary_credits'][0]['narration'] == 'NEFT CR/SAL KONKAN SOFTWORKS/MAR26'
    assert any('salary credit 2026-03-01 corrected' in n for n in notes)


def test_unknown_amount_left_unchanged():
    fields = normalise_fields({'doc_type': 'salary_slip', 'net_salary': 12345})
    out, _ = ground_fields(fields, SLIP_TEXT)
    assert out['net_salary'] == 12345.0


def test_noop_when_text_shorter_than_50_chars():
    fields = normalise_fields({'doc_type': 'identity_details', 'pan': 'BQXP4821K', 'gross_salary': 11400000})
    out, notes = ground_fields(fields, 'PAN BQXPD4821K 11,40,000')
    assert out is fields
    assert notes == []
    assert ground_fields(fields, '')[1] == []
    assert ground_fields(fields, None)[1] == []


def test_unverified_numbers():
    fields = normalise_fields({'doc_type': 'salary_slip', 'gross_salary': 80000, 'net_salary': 70000})
    assert unverified_numbers(fields, SLIP_TEXT) == ['net_salary']
    assert unverified_numbers(normalise_fields({'doc_type': 'salary_slip', 'gross_salary': 80000,
                                                'net_salary': 71200}), SLIP_TEXT) == []
    assert unverified_numbers(fields, 'Net pay 70,000') == []


def test_unverified_salary_credit():
    fields = normalise_fields({'doc_type': 'bank_statement', 'salary_credits': [
        {'date': '2026-03-01', 'amount': 82500, 'narration': 'x'},
        {'date': '2026-04-01', 'amount': 99999, 'narration': 'y'}]})
    assert unverified_numbers(fields, BANK_TEXT) == ['salary_credits[1].amount']


# --------------------------------------------------------------------------- #
# obligations: recurring debits and declared EMIs (synthetic demo text)
# --------------------------------------------------------------------------- #
RAHUL_BANK_TEXT = (
    'STATEMENT OF ACCOUNT\nAccount holder RAHUL VIJAY DESHMUKH\nPeriod: 01-Mar-2026 to 31-Aug-2026\n'
    '01-Mar-2026 NEFT CR/SAL KONKAN SOFTWORKS/MAR26 SAHYN7438010963 82,500.00 2,01,142.35\n'
    '03-Mar-2026 NEFT DR/RENT MAR26/VASANT JOSHI SAHYN7467648334 18,000.00 1,83,142.35\n'
    '05-Mar-2026 ACH DR/MULSHI AUTO FINANCE/CAR LOAN EMI ACH11261866 8,200.00 1,74,942.35\n'
    '07-Mar-2026 ACH DR/SIP/SAMPLE ASSET MGMT MF ACH89348362 5,000.00 1,69,942.35\n'
    '25-Mar-2026 BILLPAY/DR/MOBILE & BROADBAND BP756664639 1,299.00 1,44,602.35\n'
)

RAHUL_APP_TEXT = (
    'PERSONAL LOAN APPLICATION FORM\nLoan amount requested ₹6,00,000 (Rupees Six Lakh Only) Tenure 48 months\n'
    'Full name Rahul Vijay Deshmukh PAN BQXPD4821K Net monthly salary ₹82,500\n'
    '4. EXISTING OBLIGATIONS (AS DECLARED BY APPLICANT)\n'
    'Mulshi Auto Finance Ltd (sample) Car loan ₹8,200 22 months\nTotal existing EMI ₹8,200\n'
)


def _debit(date, amount, narration, category, channel='other'):
    return {'date': date, 'amount': amount, 'narration': narration, 'channel': channel, 'category': category}


def test_recurring_debit_dropped_digit_is_corrected():
    fields = normalise_fields({'doc_type': 'bank_statement', 'recurring_debits': [
        _debit('2026-03-05', 820, 'ACH DR/MULSHI AUTO FINANCE/CAR LOAN EMI', 'loan_emi', 'ACH'),
        _debit('2026-03-03', 18000, 'NEFT DR/RENT MAR26/VASANT JOSHI', 'rent')]})
    out, notes = ground_fields(fields, RAHUL_BANK_TEXT)
    assert [d['amount'] for d in out['recurring_debits']] == [8200.0, 18000.0]
    assert notes == ['debit 2026-03-05 corrected from text layer: model read ₹820, document text says ₹8,200']
    assert unverified_numbers(out, RAHUL_BANK_TEXT) == []
    assert fields['recurring_debits'][0]['amount'] == 820.0, 'input dict must not be mutated'


def test_ungrounded_recurring_debit_is_marked_unverified():
    fields = normalise_fields({'doc_type': 'bank_statement', 'recurring_debits': [
        _debit('2026-03-05', 8200, 'ACH DR/MULSHI AUTO FINANCE/CAR LOAN EMI', 'loan_emi', 'ACH'),
        _debit('2026-03-10', 12345, 'NACH DR/UNKNOWN FINSERV/PL EMI', 'loan_emi', 'NACH')]})
    out, _ = ground_fields(fields, RAHUL_BANK_TEXT)
    assert out['recurring_debits'][1]['amount'] == 12345.0  # kept, but flagged
    assert unverified_numbers(out, RAHUL_BANK_TEXT) == ['recurring_debits[1].amount']


def test_declared_emi_and_requested_loan_grounded():
    fields = normalise_fields({
        'doc_type': 'loan_application', 'pan': 'BQXPD4821K', 'declared_net_salary': 82500,
        'declared_existing_emis': [{'lender': 'Mulshi Auto Finance Ltd (sample)', 'loan_type': 'Car loan',
                                    'amount': 82000}],
        'declared_total_existing_emi': 8200, 'loan_amount': 600000, 'loan_tenure_months': 48})
    out, notes = ground_fields(fields, RAHUL_APP_TEXT)
    assert out['declared_existing_emis'][0]['amount'] == 8200.0
    assert notes == ['declared EMI (Mulshi Auto Finance Ltd (sample)) corrected from text layer: '
                     'model read ₹82,000, document text says ₹8,200']
    assert unverified_numbers(out, RAHUL_APP_TEXT) == []


def test_ungrounded_declared_emi_and_tenure_are_unverified():
    fields = normalise_fields({
        'doc_type': 'loan_application',
        'declared_existing_emis': [{'lender': 'Mulshi Auto Finance Ltd (sample)', 'amount': 9100}],
        'declared_total_existing_emi': 9100, 'loan_tenure_months': 60})
    out, _ = ground_fields(fields, RAHUL_APP_TEXT)
    assert unverified_numbers(out, RAHUL_APP_TEXT) == [
        'declared_total_existing_emi', 'loan_tenure_months', 'declared_existing_emis[0].amount']


def test_declared_none_zero_total_is_grounded():
    text = RAHUL_APP_TEXT.replace('Mulshi Auto Finance Ltd (sample) Car loan ₹8,200 22 months\n'
                                  'Total existing EMI ₹8,200\n', 'None declared – ₹0 –\n')
    fields = normalise_fields({'doc_type': 'loan_application', 'pan': 'BQXPD4821K', 'declared_existing_emis': [],
                               'declared_total_existing_emi': 0})
    out, notes = ground_fields(fields, text)
    assert out['declared_total_existing_emi'] == 0.0 and notes == []
    assert unverified_numbers(out, text) == []


def test_old_record_without_obligation_keys_is_left_alone():
    old = {'doc_type': 'bank_statement', 'salary_credits': [
        {'date': '2026-03-01', 'amount': 82500, 'narration': 'NEFT CR/SAL KONKAN SOFTWORKS/MAR26'}]}
    out, _ = ground_fields(old, RAHUL_BANK_TEXT)
    assert 'recurring_debits' not in out and 'declared_existing_emis' not in out
    assert unverified_numbers(old, RAHUL_BANK_TEXT) == []


# --------------------------------------------------------------------------- #
# CIBIL page fields: credit report, application, rent agreement (synthetic text)
# --------------------------------------------------------------------------- #
# Page texts as pypdf extracts the synthetic sample report (2 pages).
CREDIT_PAGE_1 = (
    'Sample credit report (synthetic, for demo only)\n'
    'Bureau CIBIL (sample format - not issued by any credit bureau) Report date 18-Sep-2026\n'
    'Name RAHUL VIJAY DESHMUKH Date of birth 14-02-1992 PAN BQXPD4821K\n'
    'Credit score 771 (range 300-900)\n'
    'ENQUIRY SUMMARY (CUMULATIVE)\nLast 30 days Last 60 days Last 90 days Last 120 days\n0 1 1 2\n'
    'ACCOUNTS (TRADELINES)\n'
    'Car loan Mulshi Auto Finance Ltd MAF2022070004512 4,50,000 1,65,115 8,200 0 Active 50 22 10-07-2022 '
    '05-Sep-2026\n'
)
CREDIT_PAGE_2 = (
    'Credit card Tamrapur Urban Co-op Bank 5243910000457731 1,50,000 12,400 – 0 Active – – 14-03-2019 '
    '16-Aug-2026\n'
    'ENQUIRIES\n02-Aug-2026 Konkan Finserv Ltd (sample) Credit Card\n'
    '14-Jun-2026 Godavari Credit Ltd (sample) Personal Loan\n'
    '11-Nov-2025 Tamrapur Urban Co-op Bank Credit Card\n'
)
CREDIT_TEXT = CREDIT_PAGE_1 + CREDIT_PAGE_2


def _credit_fields(**overrides):
    raw = {
        'doc_type': 'credit_report', 'applicant_name': 'RAHUL VIJAY DESHMUKH', 'pan': 'BQXPD4821K',
        'bureau': 'CIBIL', 'report_date': '2026-09-18', 'credit_score': 771,
        'enquiries_30d': 0, 'enquiries_60d': 1, 'enquiries_90d': 1, 'enquiries_120d': 2,
        'enquiries': [{'date': '2026-08-02', 'lender': 'Konkan Finserv Ltd (sample)', 'purpose': 'Credit Card'},
                      {'date': '2026-06-14', 'lender': 'Godavari Credit Ltd (sample)', 'purpose': 'Personal Loan'},
                      {'date': '2025-11-11', 'lender': 'Tamrapur Urban Co-op Bank', 'purpose': 'Credit Card'}],
        'tradelines': [
            {'loan_type': 'car_loan', 'lender': 'Mulshi Auto Finance Ltd', 'sanction_amount': 450000,
             'outstanding': 165115, 'emi': 8200, 'status': 'active', 'account_last4': '4512', 'overdue': 0,
             'emis_paid': 50, 'emis_pending': 22, 'open_date': '2022-07-10', 'last_payment_date': '2026-09-05'},
            {'loan_type': 'credit_card', 'lender': 'Tamrapur Urban Co-op Bank', 'sanction_amount': 150000,
             'outstanding': 12400, 'status': 'active', 'account_last4': '7731', 'overdue': 0,
             'open_date': '2019-03-14', 'last_payment_date': '2026-08-16'},
        ],
    }
    raw.update(overrides)
    return normalise_fields(raw)


def test_credit_report_grounded_as_printed():
    fields = _credit_fields()
    out, notes = ground_fields(fields, CREDIT_TEXT)
    assert notes == []
    assert out == fields
    assert unverified_numbers(out, CREDIT_TEXT) == []


def test_tradeline_amount_with_a_dropped_digit_is_corrected():
    fields = _credit_fields()
    fields['tradelines'][0]['outstanding'] = 16511.5
    fields['tradelines'][0]['emi'] = 82000.0
    out, notes = ground_fields(fields, CREDIT_TEXT)
    assert out['tradelines'][0]['outstanding'] == 165115.0 and out['tradelines'][0]['emi'] == 8200.0
    assert notes == [
        ('tradeline (Mulshi Auto Finance Ltd) outstanding corrected from text layer: model read ₹16,511.50, '
         'document text says ₹1,65,115'),
        ('tradeline (Mulshi Auto Finance Ltd) emi corrected from text layer: model read ₹82,000, '
         'document text says ₹8,200')]
    assert fields['tradelines'][0]['emi'] == 82000.0, 'input dict must not be mutated'


def test_ungrounded_tradeline_amount_and_score_are_unverified():
    fields = _credit_fields(credit_score=781)
    fields['tradelines'][1]['outstanding'] = 12345.0
    out, _ = ground_fields(fields, CREDIT_TEXT)
    assert unverified_numbers(out, CREDIT_TEXT) == ['credit_score', 'tradelines[1].outstanding']


def test_enquiry_counts_are_never_corrected():
    # 3 is not printed but 30 is ('Last 30 days'): a count is not an amount with a dropped digit
    fields = _credit_fields(enquiries_30d=3, enquiries_60d=3, enquiries_90d=3, enquiries_120d=3)
    out, notes = ground_fields(fields, CREDIT_TEXT)
    assert out['enquiries_30d'] == 3 and notes == []
    assert unverified_numbers(out, CREDIT_TEXT) == []


def test_record_without_tradelines_key_is_left_alone():
    old = {'doc_type': 'credit_report', 'credit_score': 771}
    out, _ = ground_fields(old, CREDIT_TEXT)
    assert 'tradelines' not in out


RAHUL_APP_V2_TEXT = RAHUL_APP_TEXT + (
    'Mobile +91 90000 00101 Date of birth 14-02-1992\n'
    'Current address Flat B-702, Sample Heights, Baner Road, Pune, Maharashtra – 411045 Pincode 411045\n'
    'House ownership Rented Employment type Salaried – Private Limited company\n'
    'Employer name Konkan Softworks Pvt Ltd Office phone 020-4000 1234\n'
)


def test_mobile_misread_is_corrected_from_the_text_layer():
    fields = normalise_fields({'doc_type': 'loan_application', 'pan': 'BQXPD4821K', 'mobile': '9000000107'})
    out, notes = ground_fields(fields, RAHUL_APP_V2_TEXT)
    assert out['mobile'] == '9000000101'
    assert notes == ["Mobile corrected from text layer: model read '9000000107', document text says '9000000101'"]
    assert printed_mobiles(RAHUL_APP_V2_TEXT) == {'9000000101'}


def test_mobile_left_out_is_filled_from_the_only_printed_one():
    # left out, or copied with a digit too many ('90000 00101' as 90000000101) and dropped by the normaliser
    for raw in ({}, {'mobile': '90000000101'}):
        fields = normalise_fields({'doc_type': 'loan_application', 'pan': 'BQXPD4821K', **raw})
        assert fields['mobile'] is None
        out, notes = ground_fields(fields, RAHUL_APP_V2_TEXT)
        assert out['mobile'] == '9000000101'
        assert notes == ["Mobile filled from text layer: '9000000101'"]
        assert unverified_numbers(out, RAHUL_APP_V2_TEXT) == []


def test_mobile_is_not_filled_when_several_are_printed_or_off_the_forms():
    text = RAHUL_APP_V2_TEXT + 'Reference 1 (sample) Mobile +91 90000 00199\n'
    out, notes = ground_fields(normalise_fields({'doc_type': 'loan_application', 'pan': 'BQXPD4821K'}), text)
    assert out['mobile'] is None and notes == []
    # only applications and identity details keep a mobile
    out, notes = ground_fields(normalise_fields({'doc_type': 'credit_report', 'pan': 'BQXPD4821K'}), RAHUL_APP_V2_TEXT)
    assert out['mobile'] is None and notes == []


def test_mobile_far_from_any_printed_number_is_kept_but_unverified():
    fields = normalise_fields({'doc_type': 'loan_application', 'pan': 'BQXPD4821K', 'mobile': '9812345678',
                               'current_pincode': '411045', 'permanent_pincode': '416008'})
    out, notes = ground_fields(fields, RAHUL_APP_V2_TEXT)
    assert out['mobile'] == '9812345678' and notes == []
    assert unverified_numbers(out, RAHUL_APP_V2_TEXT) == ['mobile', 'permanent_pincode']


def test_rent_agreement_pan_is_not_filled_from_the_text():
    # the only PAN printed is the tenant's: the record is the landlord's
    text = ('LEAVE AND LICENCE AGREEMENT (SAMPLE)\nLicensor Vasant Ramchandra Joshi\n'
            'Licensee Rahul Vijay Deshmukh PAN BQXPD4821K\nLicence fee ₹18,000 per month\n')
    out, notes = ground_fields(normalise_fields({'doc_type': 'rent_agreement', 'monthly_rent': 18000}), text)
    assert out['pan'] is None and notes == []


def test_new_income_amounts_are_grounded():
    text = SLIP_TEXT + 'Performance bonus\n15,000\nSales incentive\n4,250\n'
    fields = normalise_fields({'doc_type': 'salary_slip', 'pan': 'DMVPP5928L', 'gross_salary': 80000,
                               'net_salary': 71200, 'bonus': 150000, 'incentive': 4250})
    out, notes = ground_fields(fields, text)
    assert out['bonus'] == 15000.0
    assert notes == ['bonus corrected from text layer: model read ₹1,50,000, document text says ₹15,000']


# --------------------------------------------------------------------------- #
# field_pages: "from <file>, page N"
# --------------------------------------------------------------------------- #
def test_field_pages_of_a_two_page_credit_report():
    fields = _credit_fields()
    pages = field_pages(fields, [(1, CREDIT_PAGE_1), (2, CREDIT_PAGE_2)])
    assert pages == {
        'applicant_name': 1, 'pan': 1, 'bureau': 1, 'report_date': 1, 'credit_score': 1,
        'enquiries_30d': 1, 'enquiries_60d': 1, 'enquiries_90d': 1, 'enquiries_120d': 1,
        'enquiries': 2, 'tradelines': 1,  # a list: the first page of its rows
        'enquiries[0]': 2, 'enquiries[1]': 2, 'enquiries[2]': 2, 'tradelines[0]': 1, 'tradelines[1]': 2,
    }


def test_rows_carry_their_page():
    fields = _credit_fields()
    pages = field_pages(fields, [(1, CREDIT_PAGE_1), (2, CREDIT_PAGE_2)])
    out = with_row_pages(fields, {**pages, 'tradelines[1]': None})
    assert [t['page'] for t in out['tradelines']] == [1, None]
    assert [e['page'] for e in out['enquiries']] == [2, 2, 2]
    assert 'page' not in fields['tradelines'][0], 'input dict must not be mutated'
    assert with_row_pages({'doc_type': 'salary_slip'}, {}) == {'doc_type': 'salary_slip'}


def test_computed_enquiry_counts_point_to_the_enquiry_list():
    page_1 = CREDIT_PAGE_1.replace('ENQUIRY SUMMARY (CUMULATIVE)\nLast 30 days Last 60 days Last 90 days '
                                   'Last 120 days\n0 1 1 2\n', '')
    fields = _credit_fields(enquiries_30d=None, enquiries_60d=None, enquiries_90d=None, enquiries_120d=None)
    assert fields['enquiries_120d'] == 2  # counted from the list
    pages = field_pages(fields, [(1, page_1), (2, CREDIT_PAGE_2)])
    assert [pages[k] for k in ('enquiries_30d', 'enquiries_60d', 'enquiries_90d', 'enquiries_120d')] == [2, 2, 2, 2]


def test_field_pages_of_an_application_with_wrapped_address():
    page = RAHUL_APP_V2_TEXT.replace('Baner Road, Pune,', 'Baner\nRoad, Pune,')
    fields = normalise_fields({
        'doc_type': 'loan_application', 'applicant_name': 'Rahul Vijay Deshmukh', 'pan': 'BQXPD4821K',
        'employer': 'Konkan Softworks Pvt Ltd', 'mobile': '9000000101', 'dob': '1992-02-14',
        'house_ownership': 'rented', 'employment_type': 'private_limited',
        'current_address': 'Flat B-702, Sample Heights, Baner Road, Pune, Maharashtra – 411045',
        'declared_existing_emis': [{'lender': 'Mulshi Auto Finance Ltd (sample)', 'amount': 8200}],
        'loan_amount': 600000, 'loan_tenure_months': 48})
    pages = field_pages(fields, [(1, 'Cover page of the sample file ' * 3), (2, page)])
    assert pages == {
        'applicant_name': 2, 'pan': 2, 'employer': 2, 'loan_amount': 2, 'loan_tenure_months': 2, 'mobile': 2,
        'dob': 2, 'current_address': 2, 'current_pincode': 2, 'house_ownership': 2, 'employment_type': 2,
        'company': 2, 'declared_existing_emis': 2, 'declared_existing_emis[0]': 2}


def test_field_pages_bank_statement_rows_on_their_pages():
    page_1 = RAHUL_BANK_TEXT
    page_2 = ('05-Apr-2026 ACH DR/MULSHI AUTO FINANCE/CAR LOAN EMI ACH11261866 8,200.00 1,74,942.35\n'
              '01-Apr-2026 NEFT CR/SAL KONKAN SOFTWORKS/APR26 SAHYN7438010964 82,500.00 2,01,142.35\n')
    fields = normalise_fields({
        'doc_type': 'bank_statement', 'applicant_name': 'RAHUL VIJAY DESHMUKH',
        'statement_from': '2026-03-01', 'statement_to': '2026-08-31',
        'salary_credits': [{'date': '2026-03-01', 'amount': 82500, 'narration': 'NEFT CR/SAL KONKAN SOFTWORKS/MAR26'},
                           {'date': '2026-04-01', 'amount': 82500, 'narration': 'NEFT CR/SAL KONKAN SOFTWORKS/APR26'}],
        'recurring_debits': [_debit('2026-03-05', 8200, 'ACH DR/MULSHI AUTO FINANCE/CAR LOAN EMI', 'loan_emi'),
                             _debit('2026-04-05', 8200, 'ACH DR/MULSHI AUTO FINANCE/CAR LOAN EMI', 'loan_emi')]})
    pages = field_pages(fields, [(1, page_1), (2, page_2)])
    assert pages['salary_credits[0]'] == 1 and pages['salary_credits[1]'] == 2
    assert pages['recurring_debits[0]'] == 1 and pages['recurring_debits[1]'] == 2
    assert pages['salary_credits'] == pages['recurring_debits'] == 1  # where the list starts
    assert pages['statement_from'] == pages['statement_to'] == 1  # 'Period: 01-Mar-2026 to 31-Aug-2026'
    assert pages['applicant_name'] == 1


def test_field_pages_one_page_document_and_no_text():
    fields = normalise_fields({'doc_type': 'pension_slip', 'applicant_name': 'Vasant Ramchandra Joshi',
                               'month': '2026-08', 'monthly_pension': 32400})
    # one page: every value is on it, even without a text layer (a scan read by the vision model)
    assert field_pages(fields, [(1, '')]) == {'applicant_name': 1, 'month': 1, 'monthly_pension': 1}
    assert field_pages(fields, [(1, ''), (2, '')]) == {}
    assert field_pages(fields, []) == {}
    month_pages = field_pages(fields, [(1, 'Pension slip for August 2026'), (2, 'Net pension 32,400')])
    assert month_pages == {'month': 1, 'monthly_pension': 2}
