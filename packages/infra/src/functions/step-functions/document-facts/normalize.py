"""Normalisation of model-extracted loan-file fields (pure, no AWS).

Port of Plan B lean-file-check extract.to_number / to_date / to_month /
normalise_fields, plus financial_year normalisation (normalise_fy), the
obligations lists (recurring_debits, declared_existing_emis) and the CIBIL page
fields (mobile, addresses and pincodes, house ownership, employment type, other
income, rent agreements, pension slips, credit reports).
"""
import re
from datetime import datetime

from tool_schema import (
    BUREAUS,
    DEBIT_CATEGORIES,
    DOC_TYPE_FIELDS,
    DOC_TYPES,
    EMPLOYMENT_TYPES,
    ENQUIRY_WINDOWS,
    FIELD_NAMES,
    HOUSE_OWNERSHIP,
    LIST_FIELDS,
    NUMERIC_FIELDS,
    TRADELINE_AMOUNT_FIELDS,
    TRADELINE_COUNT_FIELDS,
    TRADELINE_LOAN_TYPES,
    TRADELINE_STATUSES,
)

_MONTHS = {m: i for i, m in enumerate(
    ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}

_NULL_STRINGS = ('', 'null', 'none', 'n/a', 'na', '-', '–')

# '2025-26', '2025-2026', '2025/26', '2025 – 26'
_FY_RE = re.compile(r'(\d{4})\s*[-/–—]\s*(\d{4}|\d{2})(?!\d)')
# 'AY', 'A.Y.', 'A Y', 'ASSESSMENT YEAR' before the year pair
_AY_RE = re.compile(r'\bA\.?\s*Y\b|ASSESSMENT')

# Channel spelled by the model -> canonical channel.
_CHANNEL_ALIASES = {
    'ACH': 'ACH', 'NACH': 'NACH', 'ENACH': 'NACH', 'ECS': 'ECS', 'SI': 'SI',
    'STANDINGINSTRUCTION': 'SI', 'UPI': 'UPI', 'UPIAUTOPAY': 'UPI',
}
# Channel prefix of a narration, e.g. 'ACH DR/MULSHI AUTO FINANCE/CAR LOAN EMI'.
_NARRATION_CHANNEL_RE = re.compile(r'^\s*(E-?NACH|NACH|ACH|ECS|SI|UPI)\b', re.I)
# 'Total existing EMI' / 'Grand total' row of the application's obligations table.
_TOTAL_ROW_RE = re.compile(r'^\s*(?:grand\s+)?total\b', re.I)
_CATEGORY_ALIASES = {
    'emi': 'loan_emi', 'loan': 'loan_emi', 'loan_repayment': 'loan_emi', 'loanemi': 'loan_emi',
    'sip': 'investment', 'mutual_fund': 'investment', 'investments': 'investment', 'rd': 'investment',
    'utilities': 'utility', 'bill': 'utility', 'bills': 'utility',
    'creditcard': 'credit_card', 'credit_card_payment': 'credit_card', 'card': 'credit_card',
    'premium': 'insurance',
}

# The last 6-digit pincode of an address: '... Pune, Maharashtra – 411045' -> '411045'.
_PINCODE_RE = re.compile(r'(?<!\d)([1-9]\d{2})\s?(\d{3})(?!\d)')
# A permanent address printed as 'Same as current address' / 'Same as above'.
_SAME_ADDRESS_RE = re.compile(r'^\s*same\s+as\s+(?:the\s+)?(?:current|present|above|residential|communication)\b',
                              re.IGNORECASE)

# Printed text -> vocabulary id, first match wins ('merchant navy' before 'navy',
# 'limited liability partnership' before 'partnership', 'owned by parents' before 'owned').
_EMPLOYMENT_WORDS = (
    (r'merchant\s*navy|seafar', 'merchant_navy'),
    (r'defen[cs]e|\barmy\b|\bnavy\b|air\s*force|armed\s+forces', 'defence'),
    (r'\b(?:grade|class|group)[\s-]*(?:4|iv|d)\b', 'grade_4'),
    (r'\bllp\b|limited\s+liability\s+partnership', 'llp'),
    (r'partnership|proprietor', 'partnership_proprietorship'),
    (r'\bpublic\s+(?:limited|ltd)\b', 'public_limited'),
    (r'\bgovt\b|government|\bpsu\b|public\s+sector', 'government'),
    (r'\bpvt\b|\bprivate\s+(?:limited|ltd)\b', 'private_limited'),
)
_HOUSE_WORDS = (
    (r'compan|employer|quarters', 'company_provided'),
    (r'parent|family|ancestral', 'parental'),
    (r'\brent|tenan|lease|licen[cs]e|paying\s+guest', 'rented'),
    (r'\bown', 'owned'),
)
_BUREAU_WORDS = (
    (r'cibil|transunion', 'CIBIL'),
    (r'experian', 'Experian'),
    (r'equifax', 'Equifax'),
    (r'crif|high\s*mark', 'CRIF'),
)
_LOAN_TYPE_WORDS = (
    (r'credit\s*card|\bcard\b', 'credit_card'),
    (r'\bhome\b|housing', 'home_loan'),
    (r'mortgage|property|\blap\b', 'mortgage_loan'),
    (r'\bcar\b|\bauto\b|vehicle|two[\s-]*wheeler|\btw\b|\bbike\b|motor', 'car_loan'),
    (r'educat|student', 'education_loan'),
    (r'consumer|durable', 'consumer_loan'),
    (r'\bapp\b|application|digital', 'application_loan'),
    (r'personal|\bpl\b', 'personal_loan'),
)


def to_number(v):
    """'₹65,000.00' / '65000' / 65000 -> 65000.0 ; None on failure."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r'[^\d.\-]', '', str(v))
    try:
        return float(s) if s not in ('', '.', '-') else None
    except ValueError:
        return None


def to_count(v):
    """A whole, non-negative count: '2' / 2.0 -> 2 ; None for fractions, negatives and text."""
    n = to_number(v)
    if n is None or n < 0 or n != int(n):
        return None
    return int(n)


def to_date(v):
    """Parse '01-Jun-2026', '2026-06-01', '01/06/2026', '01-06-2026', '01.06.2026', 'Jun 1, 2026' -> date."""
    if not v:
        return None
    s = str(v).strip()
    for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d-%B-%Y', '%d/%m/%Y', '%d-%m-%Y', '%d %b %Y', '%d %B %Y',
                '%d.%m.%Y', '%b %d, %Y', '%B %d, %Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def to_iso_date(v):
    """A date field of the CIBIL page: 'YYYY-MM-DD', or None when unparseable."""
    d = to_date(v)
    return d.isoformat() if d else None


def to_month(v):
    """'2026-07' / 'July 2026' / 'Jul-2026' / '2026-07-01' -> (2026, 7)."""
    if not v:
        return None
    s = str(v).strip().lower()
    m = re.match(r'^(\d{4})-(\d{1,2})', s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r'([a-z]{3})[a-z]*[\s\-/,]*(\d{4})', s)
    if m and m.group(1) in _MONTHS:
        return int(m.group(2)), _MONTHS[m.group(1)]
    d = to_date(v)
    return (d.year, d.month) if d else None


def normalise_fy(v):
    """Financial year -> 'YYYY-YY' or None.

    'FY 2025-26', '2025-2026', '2025-26', '2025/26' -> '2025-26'.
    An assessment year is one year after its financial year: 'AY 2026-27' -> '2025-26'.
    """
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip().upper()
    if not s:
        return None
    m = _FY_RE.search(s)
    if not m:
        return None
    start = int(m.group(1))
    end_s = m.group(2)
    if len(end_s) == 4:
        end = int(end_s)
    else:
        end = start // 100 * 100 + int(end_s)
        if end < start:  # century rollover, e.g. 1999-00
            end += 100
    if end != start + 1:
        return None
    if _AY_RE.search(s[:m.start()]):
        start -= 1
    return f'{start:04d}-{(start + 1) % 100:02d}'


def _clean_str(v):
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip()
    return None if s.lower() in _NULL_STRINGS else s


def _key(v) -> str:
    """'Private Limited' -> 'private_limited'."""
    return re.sub(r'[^a-z0-9]+', '_', str(v or '').strip().lower()).strip('_')


def _choice(v, choices, words):
    """An id of `choices` (case and separators ignored), else the first `words` pattern found; else None."""
    key = _key(v)
    if not key:
        return None
    for choice in choices:
        if key == _key(choice):
            return choice
    text = str(v).lower()
    return next((choice for pattern, choice in words if re.search(pattern, text)), None)


def normalise_channel(v, narration=None) -> str:
    """'e-NACH' -> 'NACH', 'Standing instruction' -> 'SI'; else the narration prefix; else 'other'."""
    key = re.sub(r'[^A-Z]', '', str(v or '').upper())
    if key in _CHANNEL_ALIASES:
        return _CHANNEL_ALIASES[key]
    m = _NARRATION_CHANNEL_RE.match(str(narration or ''))
    if m:
        return _CHANNEL_ALIASES[re.sub(r'[^A-Z]', '', m.group(1).upper())]
    return 'other'


def normalise_category(v) -> str:
    """'Loan EMI' -> 'loan_emi', 'SIP' -> 'investment'; unknown -> 'other'."""
    key = re.sub(r'[\s\-/]+', '_', str(v or '').strip().lower())
    key = _CATEGORY_ALIASES.get(key, key)
    return key if key in DEBIT_CATEGORIES else 'other'


def normalise_debits(items) -> list:
    """recurring_debits -> [{date, amount, narration, channel, category}].

    Amounts are positive (a debit printed as -8,200 is 8200). Entries that are
    not objects or carry no usable amount are dropped.
    """
    out = []
    for r in items if isinstance(items, list) else []:
        if not isinstance(r, dict):
            continue
        amount = to_number(r.get('amount'))
        if not amount:
            continue
        d = to_date(r.get('date'))
        narration = _clean_str(r.get('narration'))
        out.append({'date': d.isoformat() if d else _clean_str(r.get('date')),
                    'amount': abs(amount),
                    'narration': narration,
                    'channel': normalise_channel(r.get('channel'), narration),
                    'category': normalise_category(r.get('category'))})
    return out


def is_total_row(lender) -> bool:
    """'Total existing EMI' copied from the obligations table as if it were a loan."""
    return bool(_TOTAL_ROW_RE.match(str(lender or '')))


def normalise_declared_emis(items) -> list:
    """declared_existing_emis -> [{lender, loan_type, amount}].

    'None declared' / 0 rows are dropped. A 'Total ...' row is kept here and
    moved to declared_total_existing_emi by normalise_fields.
    """
    out = []
    for e in items if isinstance(items, list) else []:
        if not isinstance(e, dict):
            continue
        amount = to_number(e.get('amount'))
        if not amount:
            continue
        out.append({'lender': _clean_str(e.get('lender')),
                    'loan_type': _clean_str(e.get('loan_type')),
                    'amount': abs(amount)})
    return out


# --------------------------------------------------------------------------- #
# CIBIL page fields
# --------------------------------------------------------------------------- #
def _as_printed(v) -> str:
    """A value as text, a whole number without its '.0' (the model may send 411045.0); '' for None / bools."""
    if v is None or isinstance(v, bool):
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def normalise_mobile(v):
    """'+91 90000 00101' / '090000-00101' / 9000000101 -> '9000000101'; masked or not a mobile -> None."""
    digits = re.sub(r'\D', '', _as_printed(v))
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]
    return digits if re.fullmatch(r'[6-9]\d{9}', digits) else None


def normalise_pincode(v):
    """'411 045' / 411045 -> '411045'; None unless six digits (not starting with 0)."""
    digits = re.sub(r'\D', '', _as_printed(v))
    return digits if re.fullmatch(r'[1-9]\d{5}', digits) else None


def address_pincode(address):
    """The last pincode printed in an address, or None."""
    found = _PINCODE_RE.findall(str(address or ''))
    return ''.join(found[-1]) if found else None


def normalise_employment_type(v):
    """'Salaried – Private Limited company' -> 'private_limited'; 'LLP' -> 'llp'; unknown -> None."""
    return _choice(v, EMPLOYMENT_TYPES, _EMPLOYMENT_WORDS)


def normalise_house_ownership(v):
    """'Rented' -> 'rented', 'Owned by parents' -> 'parental', 'Company lease' -> 'company_provided'."""
    return _choice(v, HOUSE_OWNERSHIP, _HOUSE_WORDS)


def normalise_registration(v):
    """'Registered (sub-registrar)' -> 'registered'; 'Notarized' / 'Unregistered, notarised' -> 'notarised'."""
    s = str(v or '').lower()
    if 'regist' in s and not re.search(r'\bun-?\s*regist|\bnot\s+regist', s):
        return 'registered'
    return 'notarised' if 'notar' in s else None


def normalise_bureau(v):
    """'TransUnion CIBIL' -> 'CIBIL', 'CRIF High Mark' -> 'CRIF'; unknown -> None."""
    return _choice(v, BUREAUS, _BUREAU_WORDS)


def normalise_loan_type(v):
    """'Two-wheeler loan' -> 'car_loan', 'Loan against property' -> 'mortgage_loan'; unknown -> 'other'.

    The eligibility page's short ids ('car', 'home') are accepted too. None only when blank.
    """
    key = _key(v)
    if not key:
        return None
    if f'{key}_loan' in TRADELINE_LOAN_TYPES:
        return f'{key}_loan'
    return _choice(v, TRADELINE_LOAN_TYPES, _LOAN_TYPE_WORDS) or 'other'


def normalise_tradeline_status(v):
    """'Written-off' -> 'written_off', 'Open' / 'Current' -> 'active', 'Paid' -> 'closed'; else 'other'."""
    key = _key(v)
    if not key:
        return None
    if key in TRADELINE_STATUSES:
        return key
    s = str(v).lower()
    if 'writ' in s or re.search(r'\bw\s*/?\s*o\b', s):
        return 'written_off'
    if 'settl' in s:
        return 'settled'
    if re.search(r'clos|\bpaid\b', s):
        return 'closed'
    if re.search(r'activ|open|current|live|standard|regular', s):
        return 'active'
    return 'other'


def account_last4(v):
    """'XXXXXXXX4821' / 'MAF/2022/004512' -> '4821' / '4512': never more than the last 4 characters."""
    s = re.sub(r'[^A-Za-z0-9]', '', _as_printed(v)).upper()[-4:]
    return s if s.strip('X') else None


def normalise_tradelines(items) -> list:
    """tradelines -> [{loan_type, lender, sanction_amount, outstanding, emi, status, account_last4,
    overdue, emis_paid, emis_pending, open_date, last_payment_date}].

    Amounts are positive; dates ISO or None; only the last 4 characters of an
    account number are kept (also when the model copied the whole number, or
    named the key account_number). Entries with no lender and no amount are dropped.
    """
    out = []
    for t in items if isinstance(items, list) else []:
        if not isinstance(t, dict):
            continue
        amounts = {k: to_number(t.get(k)) for k in TRADELINE_AMOUNT_FIELDS}
        amounts = {k: abs(v) if v is not None else None for k, v in amounts.items()}
        lender = _clean_str(t.get('lender'))
        if not lender and all(v is None for v in amounts.values()):
            continue
        out.append({
            'loan_type': normalise_loan_type(t.get('loan_type')),
            'lender': lender,
            'sanction_amount': amounts['sanction_amount'],
            'outstanding': amounts['outstanding'],
            'emi': amounts['emi'],
            'status': normalise_tradeline_status(t.get('status')),
            'account_last4': account_last4(t.get('account_last4') or t.get('account_number')),
            'overdue': amounts['overdue'],
            **{k: to_count(t.get(k)) for k in TRADELINE_COUNT_FIELDS},
            'open_date': to_iso_date(t.get('open_date')),
            'last_payment_date': to_iso_date(t.get('last_payment_date')),
        })
    return out


def normalise_enquiries(items) -> list:
    """enquiries -> [{date, lender, purpose}] (date ISO or None); entries with no date and no lender are dropped."""
    out = []
    for e in items if isinstance(items, list) else []:
        if not isinstance(e, dict):
            continue
        row = {'date': to_iso_date(e.get('date')), 'lender': _clean_str(e.get('lender')),
               'purpose': _clean_str(e.get('purpose'))}
        if row['date'] or row['lender']:
            out.append(row)
    return out


def enquiry_counts(enquiries, report_date) -> dict:
    """Enquiries dated within each window before the report date (inclusive): {enquiries_30d: n, ...}.

    {} when the report date or every enquiry date is unknown.
    """
    report = to_date(report_date)
    dates = [to_date(e.get('date')) for e in enquiries or [] if isinstance(e, dict)]
    dates = [d for d in dates if d]
    if not report or not dates:
        return {}
    ages = [(report - d).days for d in dates]
    return {k: sum(1 for a in ages if 0 <= a <= days) for k, days in ENQUIRY_WINDOWS.items()}


def _cumulative(counts: dict) -> dict:
    """Counts of a longer window are never below a shorter one's (each count is a lower bound)."""
    out, floor = dict(counts), None
    for k in ENQUIRY_WINDOWS:
        if out[k] is None:
            continue
        floor = out[k] if floor is None else max(floor, out[k])
        out[k] = floor
    return out


def normalise_fields(f: dict) -> dict:
    """Plan B normalise_fields + financial_year + obligations + CIBIL page fields.

    Always returns every FIELD_NAMES key.
    """
    if not isinstance(f, dict):
        f = {}
    out = {k: f.get(k) for k in FIELD_NAMES}
    for k, v in out.items():
        if isinstance(v, str) and v.strip().lower() in _NULL_STRINGS:
            out[k] = None
    if out['doc_type'] not in DOC_TYPES:
        out['doc_type'] = 'other'
    for k in NUMERIC_FIELDS:
        out[k] = to_number(out[k])
    if out['pan']:
        out['pan'] = re.sub(r'\s', '', str(out['pan'])).upper()
    if out['masked_aadhaar_last4']:
        digits = re.sub(r'\D', '', str(out['masked_aadhaar_last4']))
        out['masked_aadhaar_last4'] = digits[-4:] or None
    ym = to_month(out['month'])
    out['month'] = f'{ym[0]:04d}-{ym[1]:02d}' if ym else None
    for k in ('statement_from', 'statement_to'):
        d = to_date(out[k])
        out[k] = d.isoformat() if d else out[k]
    credits = []
    for c in out.get('salary_credits') or []:
        if not isinstance(c, dict):
            continue
        d = to_date(c.get('date'))
        credits.append({'date': d.isoformat() if d else c.get('date'),
                        'amount': to_number(c.get('amount')),
                        'narration': c.get('narration')})
    out['salary_credits'] = credits
    out['recurring_debits'] = normalise_debits(out['recurring_debits'])
    declared = normalise_declared_emis(out['declared_existing_emis'])
    totals = [e['amount'] for e in declared if is_total_row(e['lender'])]
    out['declared_existing_emis'] = [e for e in declared if not is_total_row(e['lender'])]
    for k in ('declared_total_existing_emi', 'loan_tenure_months'):
        if out[k] is not None:
            out[k] = abs(out[k])
    if totals and out['declared_total_existing_emi'] is None:
        out['declared_total_existing_emi'] = max(totals)
    out['financial_year'] = normalise_fy(out['financial_year'])
    _normalise_cibil_fields(out)
    return out


def _normalise_cibil_fields(out: dict) -> None:
    """The CIBIL page fields of a record being normalised (in place)."""
    doc_type = out['doc_type']
    for k, doc_types in DOC_TYPE_FIELDS.items():
        if doc_type not in doc_types:
            out[k] = [] if k in LIST_FIELDS else None
    # The employer of a pension slip (the former employer), a rent agreement or a
    # credit report is not the applicant's current employer.
    if doc_type in ('credit_report', 'rent_agreement', 'pension_slip'):
        out['employer'] = None
    for k in ('current_address', 'permanent_address', 'company', 'landlord_name', 'tenant_name'):
        out[k] = _clean_str(out[k])
    out['mobile'] = normalise_mobile(out['mobile'])
    for k in ('dob', 'report_date', 'agreement_from', 'agreement_to'):
        out[k] = to_iso_date(out[k])
    out['current_pincode'] = normalise_pincode(out['current_pincode']) or address_pincode(out['current_address'])
    if out['permanent_address'] and _SAME_ADDRESS_RE.match(out['permanent_address']):
        out['permanent_address'] = out['current_address']
        out['permanent_pincode'] = out['permanent_pincode'] or out['current_pincode']
    out['permanent_pincode'] = (normalise_pincode(out['permanent_pincode'])
                                or address_pincode(out['permanent_address']))
    out['house_ownership'] = normalise_house_ownership(out['house_ownership'])
    out['employment_type'] = normalise_employment_type(out['employment_type'])
    if doc_type in ('loan_application', 'identity_details'):
        # 'company' and 'employer' are the same value: either one fills the other
        out['company'] = out['company'] or out['employer']
        out['employer'] = out['employer'] or out['company']
    for k in ('bonus', 'incentive', 'other_income_annual', 'rental_income_annual', 'monthly_rent',
              'monthly_pension'):
        if out[k] is not None:
            out[k] = abs(out[k])
    out['registration'] = normalise_registration(out['registration'])
    if doc_type == 'rent_agreement':
        out['applicant_name'] = out['applicant_name'] or out['landlord_name']
    out['bureau'] = normalise_bureau(out['bureau'])
    if out['credit_score'] is not None and out['credit_score'] <= 0:  # -1 / 0: no credit history
        out['credit_score'] = None
    out['tradelines'] = normalise_tradelines(out['tradelines'])
    out['enquiries'] = normalise_enquiries(out['enquiries'])
    counts = {k: to_count(out[k]) for k in ENQUIRY_WINDOWS}
    listed = enquiry_counts(out['enquiries'], out['report_date'])
    counts = {k: v if v is not None else listed.get(k) for k, v in counts.items()}
    out.update(_cumulative(counts))
