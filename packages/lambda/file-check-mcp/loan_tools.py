"""Deterministic loan maths for the chat (no LLM): EMI, FOIR and lender eligibility.

Three tools of the File Check MCP Lambda (index.py registers them):
  - emi_calculator(principal, annual_rate_pct, tenure_months | tenure_years,
    new_annual_rate_pct?): the EMI, total interest, total payable, the
    principal / interest split, a yearly amortisation schedule and, with a new
    rate, the balance-transfer view (new EMI, monthly and total saving).
  - foir_eligibility(net_monthly_income, existing_emis?, foir_pct,
    annual_rate_pct, tenure_months | tenure_years): the largest EMI the FOIR
    leaves and the loan it repays (varunikaloans.example/check-eligibility's method,
    and the sheet's FOIR eligibility).
  - loan_eligibility(project_id, applicant): the inputs saved on the
    applicant's CIBIL page (PROJ#{pid} ELIG# items) at every lender, through
    eligibility.py (a byte-identical copy of the backend's engine and its
    SAMPLE data) with the file check's verified salary and bank-statement EMIs,
    as POST /projects/{id}/eligibility/calculate does. The DSA's uploaded
    serviceability and company lists (REFDATA# items) are not applied here:
    a note says when one is uploaded (the web page's figures can then differ).

EMI(P, r, n) = P r (1 + r)^n / ((1 + r)^n - 1) with r = annual rate / 12 / 100
(Excel PMT; P / n at 0%), kept exact (Decimal); totals come from the unrounded
EMI and rupee figures are rounded half up only at the end, as the calculator
pages do. Names, PANs and amounts are never logged.
"""

import datetime as dt
import hashlib
import math
import re
import time
from decimal import ROUND_HALF_UP, Decimal, localcontext

import eligibility as el
import engine

# The backend's limits (app/routers/eligibility.py amounts and tenure; the engine's ROI).
MAX_AMOUNT = Decimal(1_000_000_000)
MAX_RATE_PCT = Decimal(60)
MAX_MONTHS = 480

INPUTS_SK_PREFIX = 'ELIG#'
TTL_ATTRIBUTE = 'expires_at'
# The DSA's uploaded lists the backend's calculation applies (app/reference_data.py).
REFERENCE_LISTS = {
    'pincode_serviceability': 'pincode serviceability',
    'company_categories': 'company categories',
}
REFERENCE_SK_PREFIX = 'REFDATA#'

INDICATIVE = 'indicative — the lender decides the final rate, amount and EMI'
EMI_METHOD = (
    'EMI = P × r × (1 + r)^n ÷ ((1 + r)^n − 1), r = annual rate ÷ 12 ÷ 100, '
    'n = months (Excel PMT, reducing balance; P ÷ n at 0%); totals from the '
    'unrounded EMI; rupees rounded half up'
)
FOIR_METHOD = (
    'maximum EMI = FOIR × net monthly income − existing EMIs (0 if negative); '
    'maximum loan = maximum EMI ÷ EMI of ₹1,00,000 at the rate over the tenure '
    '× 1,00,000; rupees rounded half up'
)
ELIGIBILITY_INSTRUCTIONS = (
    "Report the summary lines and each lender's status, reasons, amounts and "
    'EMIs exactly as returned (Indian digit grouping). Never recompute, round '
    'differently or estimate another figure: call emi_calculator for another '
    'amount, rate or tenure. Label every figure "indicative — the lender '
    'decides"; while sample is true also say "sample policy — replace with '
    'your lender grid". Eligible is an indicative result of the policy, never '
    'an approval.'
)

_PAN_RE = re.compile(r'^[A-Z]{5}[0-9]{4}[A-Z]$')
_MASKED_PAN_RE = re.compile(r'^X{6}[0-9]{3}[A-Z]$')
_NUMBER_TEXT_RE = re.compile(r'^\d+(\.\d+)?$')
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')
RUPEE = Decimal(1)


# ------------------------------------------------------------------ inputs
def _as_decimal(value):
    """A number, or a numeric string ('10,00,000', '₹ 98,000', '11.5%'); else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value)) if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip().replace('₹', '').casefold()
        text = re.sub(r'[,\s%]', '', re.sub(r'^(rs\.?|inr)', '', text))
        return Decimal(text) if _NUMBER_TEXT_RE.match(text) else None
    return None


def _number(event, key, *, required=True, maximum=None, positive=False):
    """(Decimal | None, error): a number from 0 (more than 0 if positive) to maximum."""
    value = event.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, (f'{key} is required' if required else None)
    number = _as_decimal(value)
    if number is None:
        return None, f'{key} must be a number'
    if number < 0 or (positive and number == 0):
        return None, f"{key} must be {'more than' if positive else 'at least'} 0"
    if maximum is not None and number > maximum:
        return None, f'{key} must be at most {maximum}'
    return number, None


def _tenure(event):
    """(months, error) from tenure_months (1 to 480) or tenure_years (whole months)."""
    months, err = _number(event, 'tenure_months', required=False)
    if err:
        return None, err
    years, err = _number(event, 'tenure_years', required=False)
    if err:
        return None, err
    if months is None and years is None:
        return None, 'tenure_months (or tenure_years) is required'
    if months is not None and months != months.to_integral_value():
        return None, 'tenure_months must be a whole number of months'
    if years is not None:
        from_years = years * 12
        if from_years != from_years.to_integral_value():
            return None, 'tenure_years must make whole months (e.g. 2.5 years)'
        if months is not None and months != from_years:
            return None, 'give tenure_months or tenure_years, not both'
        months = from_years
    if not 1 <= months <= MAX_MONTHS:
        return None, f'the tenure must be 1 to {MAX_MONTHS} months'
    return int(months), None


# ------------------------------------------------------------------ formatting
def _rupees(value: Decimal) -> int:
    """To the rupee, half up (the calculator pages' rounding)."""
    return int(value.quantize(RUPEE, rounding=ROUND_HALF_UP))


def _rate(value: Decimal) -> str:
    return f'{value.normalize():f}%'


def _tenure_text(months: int) -> str:
    if months % 12 == 0:
        years = months // 12
        return f"{years} year{'s' if years != 1 else ''} ({months} months)"
    return f"{months} month{'s' if months != 1 else ''}"


# ------------------------------------------------------------------ EMI
def yearly_schedule(principal, rate, months, emi) -> list:
    """Per loan year (months 1-12 are year 1): opening balance, principal and
    interest paid, closing balance, in rupees."""
    r = rate / 1200
    balance = principal
    rows = []
    for start in range(0, months, 12):
        opening, paid_principal, paid_interest = balance, Decimal(0), Decimal(0)
        count = min(12, months - start)
        for _ in range(count):
            interest = balance * r
            paid_interest += interest
            paid_principal += emi - interest
            balance -= emi - interest
        if start + count == months:
            balance = Decimal(0)  # the last EMI clears the loan (a rounding trace is left)
        rows.append(
            {
                'year': start // 12 + 1,
                'months': count,
                'opening_balance': _rupees(opening),
                'principal_paid': _rupees(paid_principal),
                'interest_paid': _rupees(paid_interest),
                'closing_balance': _rupees(balance),
            }
        )
    return rows


def repayment(principal: Decimal, rate: Decimal, months: int):
    """(EMI, totals, split and yearly schedule of one loan in rupees; the exact EMI)."""
    with localcontext() as ctx:
        ctx.prec = el.PRECISION
        emi = el.emi(principal, rate, months)
        total = emi * months
        principal_pct = _rupees(principal / total * 100)
        result = {
            'principal': _rupees(principal),
            'annual_rate_pct': float(rate),
            'tenure_months': months,
            'emi': _rupees(emi),
            'emi_exact': el.money(emi),
            'total_interest': _rupees(total - principal),
            'total_payable': _rupees(total),
            'principal_pct': principal_pct,
            'interest_pct': 100 - principal_pct,
            'yearly_schedule': yearly_schedule(principal, rate, months, emi),
        }
    return result, emi


def _balance_transfer(principal, rate, new_rate, months, emi, result) -> dict:
    with localcontext() as ctx:
        ctx.prec = el.PRECISION
        new_emi = el.emi(principal, new_rate, months)
        monthly = emi - new_emi
        bt = {
            'outstanding': result['principal'],
            'remaining_months': months,
            'current_annual_rate_pct': float(rate),
            'new_annual_rate_pct': float(new_rate),
            'current_emi': result['emi'],
            'new_emi': _rupees(new_emi),
            'monthly_saving': _rupees(monthly),
            'total_saving': _rupees(monthly * months),
            'new_total_interest': _rupees(new_emi * months - principal),
        }
    shown = {
        'current_emi': el.inr(bt['current_emi']),
        'new_emi': el.inr(bt['new_emi']),
        'monthly_saving': el.inr(abs(bt['monthly_saving'])),
        'total_saving': el.inr(abs(bt['total_saving'])),
    }
    bt['display'] = shown
    bt['summary'] = (
        f"Balance transfer of {el.inr(bt['outstanding'])} over the remaining "
        f'{_tenure_text(months)} from {_rate(rate)} to {_rate(new_rate)}: EMI '
        f"{shown['current_emi']} -> {shown['new_emi']}, "
        f"{'saving' if bt['total_saving'] >= 0 else 'costing'} "
        f"{shown['monthly_saving']} a month and {shown['total_saving']} in all "
        "(before the new lender's processing fee and any charge for closing the "
        'current loan)'
    )
    return bt


def emi_calculator(event: dict) -> dict:
    principal, err = _number(event, 'principal', positive=True, maximum=MAX_AMOUNT)
    if err:
        return {'error': err}
    rate, err = _number(event, 'annual_rate_pct', maximum=MAX_RATE_PCT)
    if err:
        return {'error': err}
    months, err = _tenure(event)
    if err:
        return {'error': err}
    new_rate, err = _number(
        event, 'new_annual_rate_pct', required=False, maximum=MAX_RATE_PCT
    )
    if err:
        return {'error': err}

    result, emi = repayment(principal, rate, months)
    shown = {
        'principal': el.inr(result['principal']),
        'annual_rate': _rate(rate),
        'tenure': _tenure_text(months),
        'emi': el.inr(result['emi']),
        'total_interest': el.inr(result['total_interest']),
        'total_payable': el.inr(result['total_payable']),
        'split': (
            f"principal {result['principal_pct']}%, "
            f"interest {result['interest_pct']}%"
        ),
    }
    result['display'] = shown
    result['summary'] = (
        f"EMI {shown['emi']} a month on {shown['principal']} at "
        f"{shown['annual_rate']} p.a. over {shown['tenure']}: total interest "
        f"{shown['total_interest']}, total payable {shown['total_payable']} "
        f"({shown['split']})"
    )
    if new_rate is not None:
        result['balance_transfer'] = _balance_transfer(
            principal, rate, new_rate, months, emi, result
        )
    result['method'] = EMI_METHOD
    result['label'] = INDICATIVE
    print(f'emi_calculator months={months} balance_transfer={new_rate is not None}')
    return result


def foir_eligibility(event: dict) -> dict:
    income, err = _number(
        event, 'net_monthly_income', positive=True, maximum=MAX_AMOUNT
    )
    if err:
        return {'error': err}
    existing, err = _number(
        event, 'existing_emis', required=False, maximum=MAX_AMOUNT
    )
    if err:
        return {'error': err}
    foir_pct, err = _number(event, 'foir_pct', positive=True, maximum=Decimal(100))
    if err:
        return {'error': err}
    rate, err = _number(event, 'annual_rate_pct', maximum=MAX_RATE_PCT)
    if err:
        return {'error': err}
    months, err = _tenure(event)
    if err:
        return {'error': err}

    existing = existing or Decimal(0)
    with localcontext() as ctx:
        ctx.prec = el.PRECISION
        limit = income * foir_pct / 100
        max_emi = max(limit - existing, Decimal(0))
        per_lakh = el.emi(el.LAKH, rate, months)
        result = {
            'net_monthly_income': _rupees(income),
            'existing_emis': _rupees(existing),
            'foir_pct': float(foir_pct),
            'annual_rate_pct': float(rate),
            'tenure_months': months,
            'foir_limit': _rupees(limit),
            'max_emi': _rupees(max_emi),
            'max_loan': _rupees(max_emi / per_lakh * el.LAKH),
            'per_lakh_emi': el.money(per_lakh),
        }
    shown = {
        'max_emi': el.inr(result['max_emi']),
        'max_loan': el.inr(result['max_loan']),
        'foir': _rate(foir_pct),
        'annual_rate': _rate(rate),
        'tenure': _tenure_text(months),
    }
    result['display'] = shown
    result['summary'] = (
        f"Maximum EMI {shown['max_emi']} and maximum loan {shown['max_loan']} at "
        f"{shown['foir']} FOIR and {shown['annual_rate']} p.a. over {shown['tenure']}"
    )
    if max_emi == 0:
        result['summary'] += ': the existing EMIs leave no room within the FOIR'
    result['method'] = FOIR_METHOD
    result['label'] = INDICATIVE
    print(f'foir_eligibility months={months}')
    return result


# ------------------------------------------------------------------ saved inputs
def plain(value):
    """DynamoDB Decimals as int / float, recursively (as the backend reads them)."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def full_pan(value):
    """The compact upper-case PAN `value` is, or None."""
    if not isinstance(value, str):
        return None
    compact = re.sub(r'\s', '', value).upper()
    return compact if _PAN_RE.match(compact) else None


def applicant_key(applicant: str) -> str:
    """ELIG# key of an applicant, as the backend saves it (applicant_key of
    app/routers/eligibility.py): SHA-256 of the PAN or the normalised name."""
    compact = re.sub(r'\s', '', applicant).upper()
    if _PAN_RE.match(compact):
        basis = f'pan:{compact}'
    else:
        basis = 'name:' + ' '.join(applicant.split()).casefold()
    return hashlib.sha256(basis.encode('utf-8')).hexdigest()[:40]


def _name(value) -> str:
    if not isinstance(value, str) or full_pan(value):
        return ''
    if _MASKED_PAN_RE.match(re.sub(r'\s', '', value).upper()):
        return ''
    return ' '.join(value.split()).casefold()


def _live(item, now: int) -> bool:
    expires_at = (item or {}).get(TTL_ATTRIBUTE)
    return isinstance(expires_at, int) and expires_at > now


def _saved_items(table, project_id: str, now: int) -> list:
    """Every unexpired ELIG# item of the project (base-table Query, paginated)."""
    from boto3.dynamodb.conditions import Key

    kwargs = {
        'KeyConditionExpression': Key('PK').eq(f'PROJ#{project_id}')
        & Key('SK').begins_with(INPUTS_SK_PREFIX)
    }
    items = []
    while True:
        page = table.query(**kwargs)
        items.extend(i for i in map(plain, page.get('Items', [])) if _live(i, now))
        if not page.get('LastEvaluatedKey'):
            return items
        kwargs['ExclusiveStartKey'] = page['LastEvaluatedKey']


def _identifiers(item) -> list:
    """What a saved item is known by: its name, its identifier and the
    identifiers it was saved under before (`aliases`, as the backend keeps them
    when it moves inputs from the name's key to the PAN's)."""
    profile = (item.get('inputs') or {}).get('profile') or {}
    aliases = item.get('aliases') if isinstance(item.get('aliases'), list) else []
    return [profile.get('name'), item.get('applicant'), *aliases]


def _names(item) -> list:
    return [n for n in _identifiers(item) if _name(n)]


def _pan_of(item):
    profile = (item.get('inputs') or {}).get('profile') or {}
    pans = [full_pan(v) for v in (profile.get('pan'), *_identifiers(item)[1:])]
    return next((p for p in pans if p), None)


def _people(items) -> int:
    """How many applicants `items` are: one per PAN, plus one per other name
    (a save without a PAN under the name of a save with one is that applicant)."""
    pans = {_pan_of(i) for i in items if _pan_of(i)}
    named = {_name(n) for i in items if _pan_of(i) for n in _names(i)}
    others = {
        _name(_names(i)[0])
        for i in items
        if not _pan_of(i) and _names(i) and not {_name(n) for n in _names(i)} & named
    }
    return len(pans) + len(others)


def find_saved_inputs(table, project_id: str, applicant: str, now: int):
    """(item, error): the applicant's unexpired saved inputs under its own key,
    else the one saved applicant with that PAN, or with that name (matched like
    the file check: initials and a missing middle name are fine, a first name
    alone is not)."""
    key = {
        'PK': f'PROJ#{project_id}',
        'SK': f'{INPUTS_SK_PREFIX}{applicant_key(applicant)}',
    }
    item = table.get_item(Key=key, ConsistentRead=True).get('Item')
    item = plain(item) if item else None
    if _live(item, now):
        return item, None

    items = _saved_items(table, project_id, now)
    pan = full_pan(applicant)
    if pan:
        matches = [i for i in items if _pan_of(i) == pan]
    else:
        query = _name(applicant)
        matches = [i for i in items if any(_name(n) == query for n in _names(i))]
        if not matches and engine.name_tokens(applicant):
            matches = [
                i
                for i in items
                if any(
                    engine.name_tokens(n) and engine.names_compatible(applicant, n)
                    for n in _names(i)
                )
            ]
    if not matches:
        return None, (
            'No saved eligibility inputs for this applicant in this project: '
            "open the applicant's Eligibility & lenders page (the CIBIL page), "
            'fill it in and save it, or give the full name or the PAN'
        )
    people = _people(matches)
    if people > 1:
        return None, f'{people} saved applicants match: give the full name or the PAN'
    return max(matches, key=lambda i: str(i.get('updated_at') or '')), None


# ------------------------------------------------------------------ file check figures
def _positive(value) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _text(value, limit: int = 200):
    if not isinstance(value, str):
        return None
    return value.strip()[:limit] or None


def _int(value):
    if isinstance(value, int | float) and not isinstance(value, bool):
        return int(value)
    return None


def verified_income(applicant):
    """{amount, source}: the lower of the slips' median net pay and the bank
    salary credits' median (the backend's verified_income)."""
    income = (applicant or {}).get('income') or {}
    candidates = [
        (value, source)
        for value, source in (
            (income.get('slip_net'), 'salary slips, median net pay'),
            (income.get('bank_salary_credit'), 'bank salary credits, median'),
        )
        if _positive(value)
    ]
    if not candidates:
        return None
    amount, source = min(candidates, key=lambda c: c[0])
    return {'amount': amount, 'source': source}


def bank_statement_emis(applicant) -> list:
    """The loan EMIs the file check found in the bank statement (the backend's
    bank_statement_emis)."""
    obligations = (applicant or {}).get('obligations') or {}
    if not isinstance(obligations, dict) or not obligations.get('available'):
        return []
    rows = []
    for view in obligations.get('fixed_loan_emis') or []:
        if not isinstance(view, dict) or not _positive(view.get('amount')):
            continue
        rows.append(
            {
                'amount': view['amount'],
                'payee': _text(view.get('payee'), 100),
                'lender': _text(view.get('declared_lender'), 100),
                'narration': _text(view.get('narration'), 300),
                'months_seen': _int(view.get('months_seen')),
                'months_total': _int(view.get('months_total')),
                'unverified': view.get('unverified') is True,
            }
        )
    return rows


def _file_check_applicant(run_file_check, project_id: str, identifier: str):
    """(the applicant's file-check result, what happened, the whole check)."""
    try:
        check = run_file_check({'project_id': project_id, 'applicant': identifier})
    except Exception as e:  # noqa: BLE001 - calculated from the entered values instead
        print(f'loan_eligibility: file check failed ({type(e).__name__})')
        return None, 'the file check failed', {}
    if not isinstance(check, dict) or check.get('error'):
        return None, 'the file check failed', {}
    applicants = [a for a in check.get('applicants') or [] if isinstance(a, dict)]
    if not applicants:
        return None, 'no applicant with this name or PAN in the analysed documents', check
    if len(applicants) > 1:
        return None, f'{len(applicants)} applicants in the documents match: use the PAN', check
    return applicants[0], 'verified figures from the file check', check


# ------------------------------------------------------------------ loan eligibility
def uploaded_lists_note(table, project_id: str, now: int):
    """A note when the DSA uploaded a list this tool does not apply, else None."""
    names = []
    for kind, name in REFERENCE_LISTS.items():
        key = {'PK': f'PROJ#{project_id}', 'SK': f'{REFERENCE_SK_PREFIX}{kind}'}
        if _live(plain(table.get_item(Key=key).get('Item')), now):
            names.append(name)
    if not names:
        return None
    lists, them = ('lists are', 'them') if len(names) > 1 else ('list is', 'it')
    return (
        f"Your uploaded {' and '.join(names)} {lists} not applied in this answer: the Eligibility page "
        f'applies {them}, so its figures can differ'
    )


def _lender_line(row: dict) -> str:
    status = row.get('status_label') or row.get('status')
    if row.get('status') != 'eligible':
        return f"{row['lender']}: {status} – " + '; '.join(row.get('reasons') or [])
    line = (
        f"{row['lender']}: {status} – {el.inr(row['eligible_amount'])} at "
        f"{row['roi']:g}% over {row['tenure_months']} months, EMI {el.inr(row['emi'])}"
    )
    if row.get('calculation_tenure_months') != row.get('tenure_months'):
        line += (
            f" (eligibility calculated over {row['calculation_tenure_months']} months; "
            f"EMI {el.inr(row['emi_at_calculation_tenure'])} over that tenure)"
        )
    # A lender with a salary-slab FOIR grid notes the slab and category that set its FOIR.
    grid = next((n for n in row.get('notes') or [] if n.startswith('FOIR ')), None)
    if grid:
        line += f'; {grid}'
    return line


def loan_eligibility(event: dict, table, run_file_check, now=None) -> dict:
    project_id = event.get('project_id')
    if not isinstance(project_id, str) or not project_id.strip():
        return {'error': 'project_id is required'}
    project_id = project_id.strip()
    applicant = event.get('applicant')
    if applicant is not None and not isinstance(applicant, str):
        return {'error': 'applicant must be a string'}
    applicant = (applicant or '').strip()
    if not applicant:
        return {'error': 'applicant is required: the full name or the PAN'}
    if len(applicant) > 200 or _CONTROL_RE.search(applicant):
        return {'error': 'applicant must be a name or a PAN'}

    now = int(time.time()) if now is None else int(now)
    item, err = find_saved_inputs(table, project_id, applicant, now)
    if err:
        print(f'loan_eligibility project={project_id} saved=0')
        return {'error': err}
    inputs = item.get('inputs') or {}
    profile = inputs.get('profile') or {}
    pan = full_pan(profile.get('pan'))
    identifier = pan or str(item.get('applicant') or applicant)
    found, detail, check = _file_check_applicant(run_file_check, project_id, identifier)

    notes = []
    if found is None:
        notes.append(
            f'File check not used ({detail}): the entered net income is used and '
            'no bank-statement EMIs are added'
        )
    elif check.get('pending_documents'):
        notes.append(
            f"{len(check['pending_documents'])} document(s) still being analysed: "
            'the verified figures may change'
        )
    result = el.calculate(
        inputs,
        verified_income=verified_income(found),
        bank_emis=bank_statement_emis(found),
    )
    try:
        uploaded = uploaded_lists_note(table, project_id, now)
    except Exception as e:  # noqa: BLE001 - a note only: never fail the answer for it
        print(f'loan_eligibility project={project_id} lists not read ({type(e).__name__})')
        uploaded = None
    result['notes'] = notes + result['notes'] + ([uploaded] if uploaded else [])
    result['file_check'] = {
        'used': found is not None,
        'detail': detail,
        'applicant': _text(found.get('applicant')) if found else None,
        'verdict': found.get('verdict') if found else None,
    }
    saved_as = item.get('applicant')
    result['applicant'] = (
        _text(profile.get('name'))
        or (None if full_pan(saved_as) else _text(saved_as))
        or engine.mask_pan(identifier)
    )
    result['pan_masked'] = engine.mask_pan(pan) if pan else None
    result['saved'] = {
        'updated_at': item.get('updated_at'),
        'expires_at': dt.datetime.fromtimestamp(item[TTL_ATTRIBUTE], dt.UTC).isoformat(),
    }
    best = result['best_lender']
    lines = [
        f"Best lender: {best} ({result['best_lender_reason']})"
        if best
        else 'No lender is eligible with these inputs'
    ]
    result['summary'] = lines + [_lender_line(row) for row in result['per_lender']]
    result['assistant_instructions'] = ELIGIBILITY_INSTRUCTIONS
    eligible = sum(1 for row in result['per_lender'] if row['status'] == 'eligible')
    print(
        f'loan_eligibility project={project_id} saved=1 '
        f"lenders={len(result['per_lender'])} eligible={eligible} "
        f'file_check={found is not None}'
    )
    return result
