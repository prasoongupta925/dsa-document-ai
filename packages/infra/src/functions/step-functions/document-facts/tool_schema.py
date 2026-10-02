"""Converse tool schema for loan-file facts extraction (the forced tool of extractor.call_model).

Port of the Plan B lean-file-check TOOL_SCHEMA, plus financial_year.
Plain (non-nullable) JSON types only: Nova tool use degrades badly with
["string", "null"] unions. A missing field is simply omitted by the model.

Obligations (FOIR input): bank statements carry recurring_debits (loan EMIs,
rent, SIP, insurance, utility bills, credit-card payments; one entry per
transaction) and loan applications carry declared_existing_emis /
declared_total_existing_emi. Records extracted before these fields existed
simply lack the keys; the file-check engine reports that as NEEDS REVIEW.

CIBIL page (the DSA's Data Entry and CAM sheets): loan applications and
identity details carry the mobile, date of birth, current and permanent
address with pincodes, house ownership, employment type and company; salary
slips the month's bonus and incentive; Form-16 / ITR the other and rental
income of the year; rent agreements (the applicant as landlord) the rent and
how the agreement is registered; pension slips the monthly pension; credit
reports the bureau, score, enquiries and tradelines (account numbers reduced
to their last 4 characters). These fields are kept only on their own document
types (DOC_TYPE_FIELDS).
"""

DOC_TYPES = [
    'loan_application',
    'identity_details',
    'salary_slip',
    'bank_statement',
    'form16_itr',
    'credit_report',
    'rent_agreement',
    'pension_slip',
    'other',
]

# Payment channel of a bank debit and what the debit pays for.
DEBIT_CHANNELS = ['ACH', 'NACH', 'ECS', 'SI', 'UPI', 'other']
DEBIT_CATEGORIES = ['loan_emi', 'rent', 'investment', 'utility', 'credit_card', 'insurance', 'other']

# The CIBIL page's vocabularies. Employment types are the eligibility page's ids
# (packages/backend/app/eligibility.py EMPLOYMENT_TYPES); loan types are the CAM
# sheet's list.
EMPLOYMENT_TYPES = ['defence', 'government', 'grade_4', 'llp', 'merchant_navy',
                    'partnership_proprietorship', 'private_limited', 'public_limited']
HOUSE_OWNERSHIP = ['owned', 'rented', 'parental', 'company_provided']
RENT_REGISTRATIONS = ['notarised', 'registered']
BUREAUS = ['CIBIL', 'Experian', 'Equifax', 'CRIF']
TRADELINE_LOAN_TYPES = ['personal_loan', 'home_loan', 'mortgage_loan', 'car_loan', 'education_loan',
                        'application_loan', 'consumer_loan', 'credit_card', 'other']
TRADELINE_STATUSES = ['active', 'closed', 'written_off', 'settled', 'other']

TOOL_NAME = 'record_loan_document'
TOOL_DESCRIPTION = 'Record the structured fields of one loan-file document.'

_NUM = {'type': 'number'}
_STR = {'type': 'string'}
_DATE = {'type': 'string', 'description': 'YYYY-MM-DD'}

TOOL_SCHEMA = {
    'type': 'object',
    'properties': {
        'doc_type': {
            'type': 'string',
            'enum': DOC_TYPES,
            'description': 'loan_application = loan application form; identity_details = identity / KYC '
                           'details sheet; salary_slip = monthly payslip; bank_statement = bank account '
                           'statement; form16_itr = Form-16 or ITR summary; credit_report = credit bureau '
                           'report (CIBIL, Experian, Equifax, CRIF) with score, enquiries and accounts; '
                           'rent_agreement = rent / leave and licence agreement; pension_slip = pension '
                           'payment slip; other = anything else',
        },
        'applicant_name': {**_STR, 'description': 'Applicant / employee / account-holder full name exactly as printed'},
        'pan': {**_STR, 'description': 'PAN exactly as printed (10 chars), omit if not on the document'},
        'masked_aadhaar_last4': {**_STR, 'description': 'Last 4 digits of the masked Aadhaar, omit if absent'},
        'employer': {**_STR, 'description': 'Employer name exactly as printed; omit on bank statements, credit '
                                            'reports, rent agreements and pension slips'},
        'month': {**_STR, 'description': 'Salary slips and pension slips only: pay month as YYYY-MM'},
        'gross_salary': {**_NUM, 'description': 'Salary slip: gross earnings for the month. Form-16: gross annual salary'},
        'net_salary': {**_NUM, 'description': 'Salary slip: net pay (take-home) for the month'},
        'statement_from': {**_STR, 'description': 'Bank statement period start as YYYY-MM-DD'},
        'statement_to': {**_STR, 'description': 'Bank statement period end as YYYY-MM-DD'},
        'salary_credits': {
            'type': 'array',
            'description': 'Bank statement: EVERY salary credit row (narration contains SAL / SALARY), one per month',
            'items': {
                'type': 'object',
                'properties': {
                    'date': {'type': 'string', 'description': 'YYYY-MM-DD'},
                    'amount': {'type': 'number'},
                    'narration': {'type': 'string'},
                },
                'required': ['date', 'amount', 'narration'],
            },
        },
        'recurring_debits': {
            'type': 'array',
            'description': 'Bank statement: EVERY debit row that pays a loan EMI, rent, SIP / mutual fund / RD, '
                           'insurance premium, utility / phone / broadband / electricity bill or credit-card '
                           'bill, one entry per transaction in every month (include one-off premiums). '
                           'Skip groceries, shopping, fuel, restaurants, medical stores, ATM withdrawals and credits',
            'items': {
                'type': 'object',
                'properties': {
                    'date': {'type': 'string', 'description': 'YYYY-MM-DD'},
                    'amount': {'type': 'number', 'description': 'Withdrawal amount of this row'},
                    'narration': {'type': 'string', 'description': 'Narration exactly as printed'},
                    'channel': {'type': 'string', 'enum': DEBIT_CHANNELS,
                                'description': 'ACH / NACH / ECS / SI (standing instruction) / UPI, else other'},
                    'category': {'type': 'string', 'enum': DEBIT_CATEGORIES,
                                 'description': 'loan_emi = repayment of a loan (EMI); investment = SIP, mutual '
                                                'fund, RD; utility = electricity, phone, broadband, DTH bills; '
                                                'credit_card = credit-card bill payment (not an EMI)'},
                },
                'required': ['date', 'amount', 'narration', 'category'],
            },
        },
        'declared_net_salary': {**_NUM, 'description': 'Loan application: net monthly (take-home) salary declared'},
        'declared_existing_emis': {
            'type': 'array',
            'description': 'Loan application: every existing loan the applicant declared (existing obligations '
                           'section), one entry per loan. A credit card that is not an EMI is not a loan. '
                           'Empty when none is declared',
            'items': {
                'type': 'object',
                'properties': {
                    'lender': {'type': 'string', 'description': 'Lender name exactly as printed'},
                    'loan_type': {'type': 'string', 'description': 'Facility, e.g. Car loan'},
                    'amount': {'type': 'number', 'description': 'Monthly EMI'},
                },
                'required': ['amount'],
            },
        },
        'declared_total_existing_emi': {**_NUM, 'description': 'Loan application: total existing monthly EMI '
                                                               'declared (0 when none is declared)'},
        'loan_amount': {**_NUM, 'description': 'Loan application: loan amount requested'},
        'loan_tenure_months': {**_NUM, 'description': 'Loan application: requested tenure in months'},
        'product': {**_STR, 'description': 'Loan application: loan product, e.g. Personal Loan'},
        'financial_year': {**_STR, 'description': 'Form-16 / ITR only: financial year as YYYY-YY, e.g. 2025-26'},
        'mobile': {**_STR, 'description': 'Loan application / identity details: mobile number as printed, '
                                          '10 digits; omit when masked'},
        'dob': {**_STR, 'description': 'Loan application / identity details: date of birth as YYYY-MM-DD'},
        'current_address': {**_STR, 'description': 'Loan application / identity details: current (residential) '
                                                   'address exactly as printed'},
        'current_pincode': {**_STR, 'description': '6-digit pincode of the current address'},
        'permanent_address': {**_STR, 'description': 'Loan application / identity details: permanent address '
                                                     'exactly as printed'},
        'permanent_pincode': {**_STR, 'description': '6-digit pincode of the permanent address'},
        'house_ownership': {'type': 'string', 'enum': HOUSE_OWNERSHIP,
                            'description': 'Loan application: ownership of the current residence (residence type)'},
        'employment_type': {'type': 'string', 'enum': EMPLOYMENT_TYPES,
                            'description': "Loan application: the printed employment type, or the employer's "
                                           'constitution (Pvt Ltd = private_limited, LLP = llp); omit for '
                                           'self-employed or when neither is printed'},
        'company': {**_STR, 'description': 'Loan application / identity details: company (employer) name exactly '
                                           'as printed'},
        'bonus': {**_NUM, 'description': 'Salary slip: bonus paid in this month'},
        'incentive': {**_NUM, 'description': 'Salary slip: incentive / commission paid in this month'},
        'other_income_annual': {**_NUM, 'description': 'Form-16 / ITR: income from other sources for the year'},
        'rental_income_annual': {**_NUM, 'description': 'Form-16 / ITR: income from house property (rent) for '
                                                        'the year'},
        'landlord_name': {**_STR, 'description': 'Rent agreement: landlord / licensor name exactly as printed'},
        'tenant_name': {**_STR, 'description': 'Rent agreement: tenant / licensee name exactly as printed'},
        'monthly_rent': {**_NUM, 'description': 'Rent agreement: monthly rent / licence fee'},
        'registration': {'type': 'string', 'enum': RENT_REGISTRATIONS,
                         'description': 'Rent agreement: notarised, or registered with the sub-registrar'},
        'agreement_from': {**_STR, 'description': 'Rent agreement: period start as YYYY-MM-DD'},
        'agreement_to': {**_STR, 'description': 'Rent agreement: period end as YYYY-MM-DD'},
        'monthly_pension': {**_NUM, 'description': 'Pension slip: net pension paid for the month'},
        'bureau': {'type': 'string', 'enum': BUREAUS, 'description': 'Credit report: the credit bureau'},
        'report_date': {**_STR, 'description': 'Credit report: report date as YYYY-MM-DD'},
        'credit_score': {**_NUM, 'description': 'Credit report: credit score, e.g. 771'},
        'enquiries_30d': {**_NUM, 'description': 'Credit report: enquiries in the last 30 days, only when the '
                                                 'report prints this count'},
        'enquiries_60d': {**_NUM, 'description': 'Credit report: enquiries in the last 60 days (cumulative), only '
                                                 'when printed'},
        'enquiries_90d': {**_NUM, 'description': 'Credit report: enquiries in the last 90 days (cumulative), only '
                                                 'when printed'},
        'enquiries_120d': {**_NUM, 'description': 'Credit report: enquiries in the last 120 days (cumulative), '
                                                  'only when printed'},
        'enquiries': {
            'type': 'array',
            'description': 'Credit report: EVERY enquiry the report lists, one entry per enquiry',
            'items': {
                'type': 'object',
                'properties': {
                    'date': {'type': 'string', 'description': 'Enquiry date as YYYY-MM-DD'},
                    'lender': {'type': 'string', 'description': 'Enquiring member / lender exactly as printed'},
                    'purpose': {'type': 'string', 'description': 'Enquiry purpose, e.g. Personal Loan'},
                },
                'required': ['date'],
            },
        },
        'tradelines': {
            'type': 'array',
            'description': 'Credit report: EVERY account (tradeline), one entry per account',
            'items': {
                'type': 'object',
                'properties': {
                    'loan_type': {'type': 'string', 'enum': TRADELINE_LOAN_TYPES,
                                  'description': 'Account type; a two-wheeler or auto loan is car_loan, a loan '
                                                 'against property mortgage_loan'},
                    'lender': {'type': 'string', 'description': 'Lender / member name exactly as printed'},
                    'sanction_amount': {'type': 'number', 'description': 'Sanctioned amount; the credit limit of '
                                                                         'a card'},
                    'outstanding': {'type': 'number', 'description': 'Current balance / outstanding'},
                    'emi': {'type': 'number', 'description': 'Monthly EMI, only when printed'},
                    'status': {'type': 'string', 'enum': TRADELINE_STATUSES,
                               'description': 'active = open / current; closed; written_off; settled; other'},
                    'account_last4': {'type': 'string', 'description': 'ONLY the last 4 characters of the '
                                                                       'account number'},
                    'overdue': {'type': 'number', 'description': 'Amount overdue'},
                    'emis_paid': {'type': 'number', 'description': 'EMIs paid so far'},
                    'emis_pending': {'type': 'number', 'description': 'EMIs still to pay'},
                    'open_date': _DATE,
                    'last_payment_date': _DATE,
                },
                'required': ['loan_type', 'lender'],
            },
        },
    },
    'required': ['doc_type'],
}

# Amount fields that are grounded against the printed numbers of the document.
NUMERIC_FIELDS = ('gross_salary', 'net_salary', 'declared_net_salary', 'declared_total_existing_emi',
                  'loan_amount', 'loan_tenure_months', 'bonus', 'incentive', 'other_income_annual',
                  'rental_income_annual', 'monthly_rent', 'monthly_pension', 'credit_score')

# Enquiry counts by window (days): whole numbers, cumulative, never grounded
# (normalise_fields counts the listed enquiries when the report prints no count).
ENQUIRY_WINDOWS = {'enquiries_30d': 30, 'enquiries_60d': 60, 'enquiries_90d': 90, 'enquiries_120d': 120}

# Tradeline amounts (grounded per tradeline) and instalment counts.
TRADELINE_AMOUNT_FIELDS = ('sanction_amount', 'outstanding', 'emi', 'overdue')
TRADELINE_COUNT_FIELDS = ('emis_paid', 'emis_pending')

# List fields: always present in a normalised record, [] when empty.
LIST_FIELDS = ('salary_credits', 'recurring_debits', 'declared_existing_emis', 'enquiries', 'tradelines')

# The CIBIL page fields and the document types they are read from; on any other
# document type normalise_fields clears them (e.g. the monthly rent a tenant
# declares on a loan application is not rental income).
_APPLICANT_DOCS = ('loan_application', 'identity_details')
DOC_TYPE_FIELDS = {
    **dict.fromkeys(('mobile', 'dob', 'current_address', 'current_pincode', 'permanent_address',
                     'permanent_pincode', 'house_ownership', 'employment_type', 'company'), _APPLICANT_DOCS),
    **dict.fromkeys(('bonus', 'incentive'), ('salary_slip',)),
    **dict.fromkeys(('other_income_annual', 'rental_income_annual'), ('form16_itr',)),
    **dict.fromkeys(('landlord_name', 'tenant_name', 'monthly_rent', 'registration', 'agreement_from',
                     'agreement_to'), ('rent_agreement',)),
    'monthly_pension': ('pension_slip',),
    **dict.fromkeys(('bureau', 'report_date', 'credit_score', *ENQUIRY_WINDOWS, 'enquiries', 'tradelines'),
                    ('credit_report',)),
}

# Canonical field order of a normalised facts record (includes doc_type).
FIELD_NAMES = list(TOOL_SCHEMA['properties'])
