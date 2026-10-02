"""Deterministic loan-file check engine (no LLM, stdlib only).

Generalises the Plan B lean-file-check checks.py: the same reasons, tolerances
and helpers, driven by a checklist from checklists.json instead of a fixed
salaried personal-loan list. Only this code decides READY / NOT READY; the
chat model reports the result.

Inputs are FACTS# data dicts (one per analysed document) and, optionally, the
project's DOC# data dicts so that pending / failed / unsupported documents can
be reported. See the shared interfaces, sections 4 and 5.

Obligations and FOIR (research checks X12 / X14): bank-statement
recurring_debits and the application's declared_existing_emis give an
obligations summary, the 'declared_emis_vs_bank_debits' check and an
indicative FOIR at the checklist's FOIR. Both are warnings: a finding is a
NEEDS REVIEW item (consistency status REVIEW, listed in needs_review) and
never changes READY / NOT READY, except a FOIR the checklist marks
hard_limit. Only loan EMIs count toward FOIR; rent, SIP, utilities and
credit-card payments do not.

Confirmations: a needs-review (manual) checklist item that a person confirmed
in the web app (backend POST .../file-check/confirmations, stored as
PROJ#{pid} / FCCONF#... items with the retention TTL) is CONFIRMED and counts
as met, so a checklist with required manual items can reach READY. It stays
confirmed while every document it was made on is still in the applicant's
file; otherwise the item is REVIEW again and says why.

Call recordings (audio / video documents without extracted facts) are never
part of a loan file: they are listed in recording_documents and do not keep
an applicant NOT READY while they are transcribed.
"""

import hashlib
import json
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from statistics import median

ENGINE_VERSION = '1.0'

DOC_TYPES = [
    'loan_application',
    'identity_details',
    'salary_slip',
    'bank_statement',
    'form16_itr',
    # Read for the eligibility page (the CIBIL block, other income); no
    # checklist item or consistency check uses them yet.
    'credit_report',
    'rent_agreement',
    'pension_slip',
    'other',
]

# Fixed consistency-check registry (Plan B checks.py semantics).
CHECK_IDS = [
    'pan',
    'aadhaar_last4',
    'applicant_name',
    'employer',
    'employer_vs_bank_credits',
    'declared_vs_slip_net',
    'declared_vs_bank_credits',
    'slip_net_vs_bank_credits',
    'form16_vs_slip_gross',
    'declared_emis_vs_bank_debits',
    'foir',
]

CHECK_NAMES = {
    'pan': 'PAN',
    'aadhaar_last4': 'Aadhaar (masked)',
    'applicant_name': 'Applicant name',
    'employer': 'Employer',
    'employer_vs_bank_credits': 'Employer vs bank salary credits',
    'declared_vs_slip_net': 'Declared net salary vs salary slips',
    'declared_vs_bank_credits': 'Declared net salary vs bank credits',
    'slip_net_vs_bank_credits': 'Salary slips vs bank credits',
    'form16_vs_slip_gross': 'Form-16 gross vs 12 × slip gross',
    'declared_emis_vs_bank_debits': 'Declared EMIs vs bank debits',
    'foir': 'FOIR (indicative)',
}

RULE_KINDS = {'present', 'monthly', 'period', 'manual'}

DEFAULT_TOLERANCE_PCT = 5
DEFAULT_FORM16_TOLERANCE_PCT = 10
# A declared EMI matches a bank debit within this % (min ₹1), on a day of the
# month within EMI_DAY_TOLERANCE days (weekends / holidays shift the debit).
DEFAULT_EMI_TOLERANCE_PCT = 2
EMI_DAY_TOLERANCE = 3

FOIR_LABEL = "indicative — the lender's policy decides"
# Used only when a checklist enables the 'foir' check without a 'foir' block.
DEFAULT_FOIR = {
    'value': 0.7,
    'hard_limit': False,
    'basis': 'DEMO-POLICY',
    'source': 'engine default (this checklist defines no FOIR)',
}

DEBIT_CATEGORIES = [
    'loan_emi', 'rent', 'investment', 'utility', 'credit_card', 'insurance', 'other',
]
AUTO_DEBIT_CHANNELS = ('ACH', 'NACH', 'ECS', 'SI')

MONTH_ABBR = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

# Nouns used in item details so the default checklist keeps Plan B wording.
_DOC_NOUNS = {
    'loan_application': 'loan application',
    'identity_details': 'identity details',
    'salary_slip': 'salary slips',
    'bank_statement': 'bank statement',
    'form16_itr': 'Form-16 / ITR',
    'credit_report': 'credit report',
    'rent_agreement': 'rent agreement',
    'pension_slip': 'pension slips',
    'other': 'documents',
}

UNSUPPORTED_FILE_TYPES = {
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-excel',
    'text/csv',
    'text/tab-separated-values',
}
# Fallback when a DOC# item carries no usable MIME type.
UNSUPPORTED_EXTENSIONS = ('.xlsx', '.xls', '.csv', '.tsv')
UNSUPPORTED_REASON = (
    'spreadsheet documents are not read by the file check; '
    'upload the statement as PDF'
)

# Audio / video documents are call recordings (reviewed with Call QA). Without
# extracted facts they are listed in recording_documents, never as a loan
# document that is pending or missing its facts.
RECORDING_MIME_PREFIXES = ('audio/', 'video/')
# Fallback when a DOC# item carries no usable MIME type.
RECORDING_EXTENSIONS = (
    '.wav', '.mp3', '.m4a', '.aac', '.ogg', '.oga', '.opus', '.flac', '.amr',
    '.wma', '.mp4', '.m4v', '.mov', '.webm', '.mkv', '.avi', '.3gp',
)

# Confirmed needs-review items (see the module docstring): the SK prefix of the
# backend's items, whose data index.py passes as `confirmations`.
CONFIRMATION_SK_PREFIX = 'FCCONF#'
CONFIRMED = 'CONFIRMED'
# Statuses that meet a required checklist item.
MET_STATUSES = ('PRESENT', CONFIRMED)
# Confirmation times are shown in India Standard Time.
IST = timezone(timedelta(hours=5, minutes=30))

ASSISTANT_INSTRUCTIONS = (
    "Report overall_verdict and each applicant's verdict, reasons, checklist "
    'and consistency exactly as returned. Do not re-compute, soften or add '
    'findings. Letters and artifacts must list exactly missing_items and '
    'mismatches. Report needs_review items as "needs review" (they do not '
    'change the verdict). A checklist item with status CONFIRMED was checked '
    'by the person named in its confirmation and counts as met; say who '
    'confirmed it. recording_documents are call recordings, not loan '
    'documents: when a project holds only recordings, say the file check '
    'applies to loan files and suggest the Call QA Reviewer agent. For '
    'obligations, EMIs and FOIR use only the obligations and foir objects, '
    f'and always label FOIR and the maximum new EMI "{FOIR_LABEL}"; never '
    'state approval, rates or sanction amounts.'
)

_ID_RE = re.compile(r'^[a-z0-9_]+$')
_PAN_RE = re.compile(r'^[A-Z]{5}[0-9]{4}[A-Z]$')
_YM_RE = re.compile(r'^\d{4}-\d{2}$')
_MONTHS = {m.lower(): i for i, m in enumerate(MONTH_ABBR, 1)}

DEFAULT_CATALOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'checklists.json'
)


# ------------------------------------------------------------------ helpers
def to_date(v):
    """Parse '01-Jun-2026', '2026-06-01', '01/06/2026', '01-06-2026' -> date."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in (
        '%Y-%m-%d',
        '%d-%b-%Y',
        '%d-%B-%Y',
        '%d/%m/%Y',
        '%d-%m-%Y',
        '%d %b %Y',
        '%d %B %Y',
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def to_month(v):
    """'2026-07' / 'July 2026' / 'Jul-2026' / '2026-07-01' -> (2026, 7)."""
    if not v:
        return None
    s = str(v).strip().lower()
    m = re.match(r'^(\d{4})-(\d{1,2})', s)
    if m:
        month = int(m.group(2))
        return (int(m.group(1)), month) if 1 <= month <= 12 else None
    m = re.search(r'([a-z]{3})[a-z]*[\s\-/,]*(\d{4})', s)
    if m and m.group(1) in _MONTHS:
        return int(m.group(2)), _MONTHS[m.group(1)]
    d = to_date(v)
    return (d.year, d.month) if d else None


def ym_add(ym, delta):
    y, m = ym
    idx = y * 12 + (m - 1) + delta
    return idx // 12, idx % 12 + 1


def ym_label(ym) -> str:
    return f'{MONTH_ABBR[ym[1] - 1]} {ym[0]}'


def ym_str(ym) -> str:
    return f'{ym[0]:04d}-{ym[1]:02d}'


def month_span(a, b):
    out, cur = [], a
    while cur <= b and len(out) < 60:
        out.append(cur)
        cur = ym_add(cur, 1)
    return out


def name_tokens(name) -> list:
    s = re.sub(r'[^a-z ]', ' ', str(name or '').lower())
    return [
        t
        for t in s.split()
        if t not in {'mr', 'mrs', 'ms', 'dr', 'shri', 'smt'}
    ]


def names_compatible(a, b) -> bool:
    """'Amit S. Patil' ~ 'Amit Suresh Patil' ~ 'AMIT SURESH PATIL'.

    Initials and a missing middle name are OK.
    """
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return True
    if ta[0] != tb[0] or ta[-1] != tb[-1]:
        return False
    ma, mb = ta[1:-1], tb[1:-1]
    for x, y in zip(ma, mb):
        if not (
            x == y
            or (len(x) == 1 and y.startswith(x))
            or (len(y) == 1 and x.startswith(y))
        ):
            return False
    return True


_EMP_STOP = {
    'pvt', 'private', 'ltd', 'limited', 'llp', 'inc', 'co', 'company',
    'the', 'india', 'corp', 'corporation',
}


def employer_core(name) -> str:
    s = re.sub(r'[^a-z0-9 ]', ' ', str(name or '').lower())
    return ' '.join(t for t in s.split() if t not in _EMP_STOP)


def pct_diff(declared, actual) -> float:
    return abs(declared - actual) / declared if declared else 0.0


def inr(v) -> str:
    """Indian digit grouping: 600000 -> ₹6,00,000."""
    if v is None:
        return '–'
    neg, v = v < 0, abs(v)
    whole, frac = divmod(round(v * 100), 100)
    s = str(int(whole))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        # groups of 2 from the right
        head = ','.join(re.findall(r'\d{1,2}', head[::-1]))[::-1]
        s = f'{head},{tail}'
    return f"{'-' if neg else ''}₹{s}" + (f'.{frac:02d}' if frac else '')


def lev(a: str, b: str) -> int:
    """Levenshtein distance."""
    a, b = a or '', b or ''
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(
                min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            )
        prev = cur
    return prev[-1]


def _plural(n: int, word: str) -> str:
    return f'{n} {word}' if n == 1 else f'{n} {word}s'


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ------------------------------------------------------------------ catalog
def validate_catalog(catalog) -> list:
    """Return a list of human-readable errors (empty when the catalog is valid)."""
    errors = []
    if not isinstance(catalog, dict):
        return ['catalog must be a JSON object']
    if catalog.get('schema_version') != 1:
        errors.append('schema_version must be 1')
    checklists = catalog.get('checklists')
    if not isinstance(checklists, list) or not checklists:
        errors.append('checklists must be a non-empty list')
        checklists = []

    ids = []
    for ci, cl in enumerate(checklists):
        where = f'checklists[{ci}]'
        if not isinstance(cl, dict):
            errors.append(f'{where} must be an object')
            continue
        cid = cl.get('id')
        if not isinstance(cid, str) or not _ID_RE.match(cid):
            errors.append(f'{where}: id {cid!r} must match ^[a-z0-9_]+$')
        else:
            where = f'checklist {cid}'
            if cid in ids:
                errors.append(f'duplicate checklist id {cid!r}')
            ids.append(cid)
        if not isinstance(cl.get('name'), str) or not cl.get('name'):
            errors.append(f'{where}: name must be a non-empty string')
        for key in ('tolerance_pct', 'form16_tolerance_pct', 'emi_tolerance_pct'):
            if key in cl and (not _is_number(cl[key]) or cl[key] < 0):
                errors.append(f'{where}: {key} must be a number >= 0')
        if 'foir' in cl:
            foir = cl['foir']
            if not isinstance(foir, dict):
                errors.append(f'{where}: foir must be an object')
            else:
                value = foir.get('value')
                if not _is_number(value) or not 0 < value <= 1:
                    errors.append(f'{where}: foir.value must be a number in (0, 1]')
                if 'hard_limit' in foir and not isinstance(foir['hard_limit'], bool):
                    errors.append(f'{where}: foir.hard_limit must be true or false')

        items = cl.get('items')
        if not isinstance(items, list):
            errors.append(f'{where}: items must be a list')
            items = []
        item_ids = []
        for ii, item in enumerate(items):
            iwhere = f'{where} items[{ii}]'
            if not isinstance(item, dict):
                errors.append(f'{iwhere} must be an object')
                continue
            iid = item.get('id')
            if not isinstance(iid, str) or not iid:
                errors.append(f'{iwhere}: id must be a non-empty string')
            else:
                iwhere = f'{where} item {iid}'
                if iid in item_ids:
                    errors.append(f'{where}: duplicate item id {iid!r}')
                item_ids.append(iid)
            if not isinstance(item.get('label'), str) or not item.get('label'):
                errors.append(f'{iwhere}: label must be a non-empty string')
            required = item.get('required', True)
            if not isinstance(required, bool):
                errors.append(f'{iwhere}: required must be true or false')
            rule = item.get('rule')
            if not isinstance(rule, dict):
                errors.append(f'{iwhere}: rule must be an object')
                rule = {}
            kind = rule.get('kind')
            if kind not in RULE_KINDS:
                errors.append(
                    f'{iwhere}: rule.kind {kind!r} must be one of '
                    + ', '.join(sorted(RULE_KINDS))
                )
            doc_types = item.get('doc_types', [])
            if not isinstance(doc_types, list):
                errors.append(f'{iwhere}: doc_types must be a list')
                doc_types = []
            bad = [t for t in doc_types if t not in DOC_TYPES]
            if bad:
                errors.append(
                    f'{iwhere}: unknown doc_types {bad}; '
                    f'allowed: {", ".join(DOC_TYPES)}'
                )
            if not doc_types and kind != 'manual':
                errors.append(f'{iwhere}: doc_types must not be empty')
            if kind in ('monthly', 'period'):
                months = rule.get('months')
                if not _is_int(months) or not 1 <= months <= 24:
                    errors.append(
                        f'{iwhere}: rule.months must be an integer 1..24'
                    )
                fields = (
                    ('field',)
                    if kind == 'monthly'
                    else ('from_field', 'to_field')
                )
                for fk in fields:
                    if fk in rule and not (
                        isinstance(rule[fk], str) and rule[fk]
                    ):
                        errors.append(
                            f'{iwhere}: rule.{fk} must be a non-empty string'
                        )
            if kind == 'present' and 'min_count' in rule:
                mc = rule['min_count']
                if not _is_int(mc) or mc < 1:
                    errors.append(
                        f'{iwhere}: rule.min_count must be an integer >= 1'
                    )

        checks = cl.get('consistency_checks', [])
        if not isinstance(checks, list):
            errors.append(f'{where}: consistency_checks must be a list')
        else:
            bad = [c for c in checks if c not in CHECK_IDS]
            if bad:
                errors.append(
                    f'{where}: unknown consistency_checks {bad}; '
                    f'allowed: {", ".join(CHECK_IDS)}'
                )

    default = catalog.get('default_checklist')
    if default not in ids:
        errors.append(
            f'default_checklist {default!r} is not a checklist id '
            f'({", ".join(ids) or "none"})'
        )
    return errors


def load_catalog(path=None) -> dict:
    """Load and validate checklists.json (default: next to this file)."""
    with open(path or DEFAULT_CATALOG_PATH, encoding='utf-8') as fh:
        catalog = json.load(fh)
    errors = validate_catalog(catalog)
    if errors:
        raise ValueError('invalid checklist catalog: ' + '; '.join(errors))
    return catalog


def get_checklist(catalog, checklist_id=None) -> dict:
    cid = checklist_id or catalog.get('default_checklist')
    for cl in catalog.get('checklists', []):
        if cl.get('id') == cid:
            return cl
    raise ValueError(f'Unknown checklist_id: {cid}')


def list_checklists(catalog) -> dict:
    return {
        'default_checklist': catalog.get('default_checklist'),
        'checklists': [
            {
                'id': cl['id'],
                'name': cl.get('name'),
                'product': cl.get('product'),
                'applicant_type': cl.get('applicant_type'),
                'description': cl.get('description'),
                'items': [
                    {
                        'id': it['id'],
                        'label': it.get('label'),
                        'required': _item_required(it),
                        'doc_types': list(it.get('doc_types', [])),
                        'rule': dict(it.get('rule', {})),
                    }
                    for it in cl.get('items', [])
                ],
                'consistency_checks': list(cl.get('consistency_checks', [])),
                'foir': dict(cl['foir']) if isinstance(cl.get('foir'), dict) else None,
            }
            for cl in catalog.get('checklists', [])
        ],
    }


def _item_required(item) -> bool:
    # A manual item blocks READY only when the catalog says required: true
    # explicitly (a check the rules cannot verify, e.g. a document type the
    # facts step does not classify); by default it is review-only.
    if (item.get('rule') or {}).get('kind') == 'manual':
        return item.get('required') is True
    return item.get('required', True) is not False


# ------------------------------------------------------------------ documents
def _doc_type(doc) -> str:
    t = doc.get('doc_type') or (doc.get('fields') or {}).get('doc_type')
    return t if t in DOC_TYPES else 'other'


def _usage_view(rec):
    """Tokens and cost of a document's facts extraction, or None when unknown.

    The facts step records usage = {model_id, input_tokens, output_tokens,
    cost_usd}. Records written before it recorded the cost (usage without
    cost_usd, or no usage at all) and malformed values give None. Idempotent:
    a view passed back in comes out unchanged.
    """
    u = rec.get('usage')
    if not isinstance(u, dict):
        return None
    tin, tout, cost = u.get('input_tokens'), u.get('output_tokens'), u.get('cost_usd')
    if not all(_is_number(v) and 0 <= v < float('inf') for v in (tin, tout, cost)):
        return None
    model_id = u.get('model_id') or rec.get('model_id')
    return {
        'model_id': model_id if isinstance(model_id, str) else None,
        'input_tokens': int(tin),
        'output_tokens': int(tout),
        'cost_usd': float(cost),
    }


def _usage_total(documents) -> dict:
    """Sum of the documents' usage; documents without usage count only in documents_total."""
    known = [d['usage'] for d in documents if d.get('usage')]
    return {
        'input_tokens': sum(u['input_tokens'] for u in known),
        'output_tokens': sum(u['output_tokens'] for u in known),
        'cost_usd': round(sum(u['cost_usd'] for u in known), 8),
        'documents_with_usage': len(known),
        'documents_total': len(documents),
    }


def _to_engine_doc(rec) -> dict:
    """FACTS# data (or any doc-like dict) -> engine doc."""
    return {
        'document_id': rec.get('document_id'),
        'document_name': rec.get('document_name') or rec.get('document_id') or '',
        'doc_type': _doc_type(rec),
        'fields': dict(rec.get('fields') or {}),
        'grounding': rec.get('grounding'),
        'usage': _usage_view(rec),
    }


def _sort_key(doc):
    return (str(doc.get('document_name') or ''), str(doc.get('document_id') or ''))


def _norm_pan(v):
    s = re.sub(r'\s', '', str(v or '')).upper()
    return s or None


def _name_of(doc):
    n = doc['fields'].get('applicant_name')
    return n if n and name_tokens(n) else None


def _display_name(docs):
    id_names = [
        d['fields']['applicant_name']
        for d in docs
        if d['doc_type'] == 'identity_details' and d['fields'].get('applicant_name')
    ]
    if id_names:
        return id_names[0]
    names = [
        d['fields']['applicant_name']
        for d in docs
        if d['fields'].get('applicant_name')
    ]
    return max(names, key=len) if names else 'Unknown'


def _group_pan(docs):
    id_pans = [
        _norm_pan(d['fields'].get('pan'))
        for d in docs
        if d['doc_type'] == 'identity_details' and d['fields'].get('pan')
    ]
    if id_pans:
        return id_pans[0]
    pans = [_norm_pan(d['fields'].get('pan')) for d in docs if d['fields'].get('pan')]
    if not pans:
        return None
    counts = Counter(pans)
    best = max(counts.values())
    # ties: first in document_name order
    return next(p for p in pans if counts[p] == best)


def group_applicants(docs):
    """Group documents by applicant (deterministic union-find).

    Returns (groups, unassigned); both in document_name order.
    """
    docs = sorted((_to_engine_doc(d) for d in docs), key=_sort_key)
    keyed, loose = [], []
    for d in docs:
        has_key = _norm_pan(d['fields'].get('pan')) or _name_of(d)
        (keyed if has_key else loose).append(d)

    parent = list(range(len(keyed)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    for i in range(len(keyed)):
        for j in range(i + 1, len(keyed)):
            a, b = keyed[i], keyed[j]
            pa, pb = _norm_pan(a['fields'].get('pan')), _norm_pan(b['fields'].get('pan'))
            na, nb = _name_of(a), _name_of(b)
            names_ok = bool(na and nb and names_compatible(na, nb))
            if pa and pb:
                if pa == pb or (lev(pa, pb) <= 2 and names_ok):
                    union(i, j)
            elif names_ok:
                union(i, j)

    buckets = {}
    for i, d in enumerate(keyed):
        buckets.setdefault(find(i), []).append(d)
    groups = [buckets[k] for k in sorted(buckets)]
    unassigned = []
    if len(groups) == 1:
        groups[0] = sorted(groups[0] + loose, key=_sort_key)
    else:
        unassigned = loose
    return groups, unassigned


# ------------------------------------------------------------------ confirmations
def applicant_key(applicant) -> str:
    """Key of an applicant's confirmations: SHA-256 of the PAN (any case /
    spacing) or of the name (case and spacing ignored).

    The backend derives the same key from the PAN or name the verdict shows,
    so a stored confirmation holds neither.
    """
    text = str(applicant or '')
    compact = re.sub(r'\s', '', text).upper()
    if _PAN_RE.match(compact):
        basis = f'pan:{compact}'
    else:
        basis = 'name:' + ' '.join(text.split()).casefold()
    return hashlib.sha256(basis.encode('utf-8')).hexdigest()[:40]


def _applicant_keys(docs) -> set:
    """The keys a confirmation of this applicant may carry: PAN and name."""
    keys = set()
    pan = _group_pan(docs)
    if pan:
        keys.add(applicant_key(pan))
    name = _display_name(docs)
    if name != 'Unknown':
        keys.add(applicant_key(name))
    return keys


def _epoch(now) -> float:
    if isinstance(now, datetime):
        return (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).timestamp()
    if _is_number(now):
        return float(now)
    return datetime.now(timezone.utc).timestamp()


def _confirmation_index(confirmations, now=None) -> dict:
    """{(applicant_key, item_id): confirmation} of the well-formed confirmations
    not past expires_at (DynamoDB TTL removes items late); the latest wins."""
    now_s = _epoch(now)
    index = {}
    for c in confirmations or []:
        if not isinstance(c, dict):
            continue
        key, item_id = c.get('applicant_key'), c.get('item_id')
        if not (isinstance(key, str) and key and isinstance(item_id, str) and item_id):
            continue
        expires_at = c.get('expires_at')
        if _is_number(expires_at) and expires_at <= now_s:
            continue
        ids = c.get('document_ids') if isinstance(c.get('document_ids'), list) else []
        doc_ids = sorted({d for d in ids if isinstance(d, str) and d})
        if not doc_ids:
            continue
        at = c.get('confirmed_at') if isinstance(c.get('confirmed_at'), str) else ''
        by = c.get('confirmed_by') if isinstance(c.get('confirmed_by'), str) else ''
        prev = index.get((key, item_id))
        if prev is None or at > (prev['confirmed_at'] or ''):
            index[(key, item_id)] = {
                'confirmed_by': by.strip()[:256] or None,
                'confirmed_at': at or None,
                'document_ids': doc_ids,
            }
    return index


def _confirmation_for(index, keys, item_id):
    """The latest confirmation of `item_id` under any of the applicant's keys."""
    found = [index[(k, item_id)] for k in keys if (k, item_id) in index]
    return max(found, key=lambda c: c['confirmed_at'] or '') if found else None


def _when(iso) -> str:
    """'2026-10-01T08:35:00+00:00' -> '01 Oct 2026, 14:05 IST'."""
    try:
        ts = datetime.fromisoformat(str(iso))
    except ValueError:
        return str(iso or 'an unknown time')
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(IST).strftime('%d %b %Y, %H:%M IST')


def _confirmed_by(conf) -> str:
    who = conf['confirmed_by'] or 'an unnamed user'
    return f"confirmed by {who} on {_when(conf['confirmed_at'])}"


def _public_confirmation(conf) -> dict:
    return {'confirmed_by': conf['confirmed_by'], 'confirmed_at': conf['confirmed_at']}


# ------------------------------------------------------------------ evaluation
def _parse_ym(v):
    """reference_month override 'YYYY-MM' -> (y, m); None when not given."""
    if v is None or v == '':
        return None
    if isinstance(v, tuple):
        return v
    ym = to_month(v) if isinstance(v, str) and _YM_RE.match(v.strip()) else None
    if not ym:
        raise ValueError(f'reference_month must be YYYY-MM, got {v!r}')
    return ym


def _item_docs(docs, item):
    types = set(item.get('doc_types') or [])
    return [d for d in docs if d['doc_type'] in types]


def _noun(item):
    types = item.get('doc_types') or []
    if len(types) == 1:
        return _DOC_NOUNS.get(types[0], 'documents')
    return 'documents'


def _reference_month(docs, items, override=None):
    ref = _parse_ym(override)
    if ref:
        return ref
    months = []
    for item in items:
        rule = item.get('rule') or {}
        if rule.get('kind') == 'monthly':
            fld = rule.get('field') or 'month'
            for d in _item_docs(docs, item):
                ym = to_month(d['fields'].get(fld))
                if ym:
                    months.append(ym)
    if months:
        return max(months)
    ends = []
    for item in items:
        rule = item.get('rule') or {}
        if rule.get('kind') == 'period':
            tf = rule.get('to_field') or 'statement_to'
            for d in _item_docs(docs, item):
                t = to_date(d['fields'].get(tf))
                if t:
                    ends.append((t.year, t.month))
    return max(ends) if ends else None


def _eval_present(item, docs):
    rule = item.get('rule') or {}
    min_count = rule.get('min_count', 1) or 1
    names = [d['document_name'] for d in docs]
    ok = len(docs) >= min_count
    if not docs:
        detail = 'not found in the file'
    elif ok:
        detail = ', '.join(names)
    else:
        detail = f'{len(docs)} of {min_count} found: ' + ', '.join(names)
    return {'ok': ok, 'detail': detail, 'documents': names}, None


def _eval_monthly(item, docs, ref):
    rule = item.get('rule') or {}
    months_n = rule.get('months')
    fld = rule.get('field') or 'month'
    noun = _noun(item)
    unit = 'slip(s)' if noun == 'salary slips' else 'document(s)'
    by_month = {}
    for d in docs:
        ym = to_month(d['fields'].get(fld))
        if ym:
            by_month.setdefault(ym, []).append(d['document_name'])
    names = [d['document_name'] for d in docs]
    if not ref:
        detail = (
            f'no {noun} found'
            if not docs
            else f'no {noun} with a readable {fld} found'
        )
        return (
            {'ok': False, 'detail': detail, 'documents': names if docs else []},
            ([], []),
        )
    need = [ym_add(ref, -i) for i in range(months_n - 1, -1, -1)]
    missing = [m for m in need if m not in by_month]
    found = (
        ', '.join(
            f"{ym_label(m)} ({', '.join(by_month[m])})" for m in sorted(by_month)
        )
        or 'none'
    )
    if not missing:
        detail = (
            f'{len(need)} of {months_n} present '
            f'({ym_label(need[0])} – {ym_label(need[-1])})'
        )
    else:
        detail = (
            f"missing {', '.join(ym_label(m) for m in missing)} {unit}; "
            f'found: {found}'
        )
    return {'ok': not missing, 'detail': detail, 'documents': names}, (need, missing)


def _eval_period(item, docs, ref):
    rule = item.get('rule') or {}
    months_n = rule.get('months')
    ff = rule.get('from_field') or 'statement_from'
    tf = rule.get('to_field') or 'statement_to'
    noun = _noun(item)
    covered_months, end = set(), None
    for d in docs:
        f, t = to_date(d['fields'].get(ff)), to_date(d['fields'].get(tf))
        if f and t:
            covered_months.update(month_span((f.year, f.month), (t.year, t.month)))
            end = max(end or (t.year, t.month), (t.year, t.month))
    names = [d['document_name'] for d in docs]
    if not (docs and end):
        detail = f'no {noun} (with a readable period) found'
        if ref:
            need = [ym_add(ref, -i) for i in range(months_n - 1, -1, -1)]
            return {'ok': False, 'detail': detail, 'documents': names}, (need, need)
        return {'ok': False, 'detail': detail, 'documents': names}, ([], [])
    end = max(end, ref) if ref else end
    need = [ym_add(end, -i) for i in range(months_n - 1, -1, -1)]
    missing = [m for m in need if m not in covered_months]
    covered = sorted(m for m in covered_months if m in need)
    files = ', '.join(names)
    if missing:
        detail = (
            f'covers {len(covered)} of {months_n} months '
            f'({ym_label(min(covered_months))} – {ym_label(max(covered_months))}); '
            f"missing {', '.join(ym_label(m) for m in missing)} [{files}]"
        )
    else:
        detail = (
            f'{months_n} of {months_n} months '
            f'({ym_label(need[0])} – {ym_label(need[-1])}) [{files}]'
        )
    return {'ok': not missing, 'detail': detail, 'documents': names}, (need, missing)


def _eval_manual(item, docs):
    names = [d['document_name'] for d in docs]
    detail = 'cannot be verified automatically; review manually'
    if names:
        detail += ' (related: ' + ', '.join(names) + ')'
    return {'ok': None, 'detail': detail, 'documents': names}, None


# ------------------------------------------------------------------ obligations
# Narration tokens that name a channel / direction, not a payee.
_NARR_NOISE = {
    'ACH', 'NACH', 'ENACH', 'ECS', 'SI', 'UPI', 'NEFT', 'IMPS', 'RTGS', 'DR', 'CR',
    'DEBIT', 'BILLPAY', 'POS', 'TRF', 'TO', 'BY', 'AUTOPAY', 'MANDATE',
}
_MONTH_WORDS = {
    'JAN', 'JANUARY', 'FEB', 'FEBRUARY', 'MAR', 'MARCH', 'APR', 'APRIL', 'MAY',
    'JUN', 'JUNE', 'JUL', 'JULY', 'AUG', 'AUGUST', 'SEP', 'SEPT', 'SEPTEMBER',
    'OCT', 'OCTOBER', 'NOV', 'NOVEMBER', 'DEC', 'DECEMBER',
}
# Narration keywords -> category, in priority order. 'ACH DR/SIP/...' is an
# ACH debit but not an EMI: SIP, RD and insurance debits are never loans.
_CATEGORY_KEYWORDS = [
    ('loan_emi', re.compile(r'\b(EMI|LOAN|LN)\b')),
    ('credit_card', re.compile(r'\bCC\b|CREDIT\s*CARD')),
    ('investment', re.compile(r'\b(SIP|MF|MUTUAL|RD|PPF|NPS|ELSS|DEPOSIT)\b')),
    ('insurance', re.compile(r'INSURANCE|PREMIUM|\bLIC\b|\bPOLICY\b')),
    ('rent', re.compile(r'\bRENT\b')),
    ('utility', re.compile(
        r'ELECTRICITY|MOBILE|BROADBAND|\bDTH\b|WATER|\bGAS\b|POSTPAID|INTERNET|RECHARGE')),
]
# Grouping splits these categories by amount (two EMIs to one lender are two loans).
_FIXED_AMOUNT_CATEGORIES = {'loan_emi', 'rent', 'investment'}
_LENDER_STOP = {
    'ltd', 'limited', 'pvt', 'private', 'llp', 'inc', 'co', 'company', 'the', 'india',
    'corp', 'corporation', 'sample', 'bank', 'finance', 'financial', 'fin', 'finserv',
    'services', 'capital', 'credit', 'loan', 'loans', 'housing', 'home', 'auto',
    'motor', 'motors', 'car', 'personal', 'nbfc', 'and',
}
_CHANNEL_RE = re.compile(r'^\s*(E-?NACH|NACH|ACH|ECS|SI|UPI)\b', re.I)
# A credit or balance row the model listed among the debits ('NEFT CR/SAL ...',
# 'UPI/CR/...', 'INT CR/...', 'OPENING BALANCE') is never an obligation.
_CR_TOKEN_RE = re.compile(r'(?:^|[\s/|-])CR(?:$|[\s/|-])')
_DR_TOKEN_RE = re.compile(r'(?:^|[\s/|-])DR(?:$|[\s/|-])')
_BALANCE_ROW_RE = re.compile(r'\b(?:OPENING|CLOSING)\s+BALANCE\b')
# The 'Total existing EMI' row of the application's obligations table, copied
# as if it were one more loan, would double-count the EMIs.
_TOTAL_ROW_RE = re.compile(r'^\s*(?:grand\s+)?total\b', re.I)


def _is_credit_row(narration) -> bool:
    s = str(narration or '').upper()
    return bool(
        _BALANCE_ROW_RE.search(s) or (_CR_TOKEN_RE.search(s) and not _DR_TOKEN_RE.search(s))
    )


def _ordinal(n) -> str:
    n = int(n)
    suffix = 'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f'{n}{suffix}'


def _months_label(months) -> str:
    """[(2026, 3) .. (2026, 8)] -> 'Mar 2026 – Aug 2026'; gaps -> 'Mar 2026, May 2026'."""
    months = sorted(months)
    if not months:
        return 'no months'
    if len(months) == 1:
        return ym_label(months[0])
    if months == month_span(months[0], months[-1]):
        return f'{ym_label(months[0])} – {ym_label(months[-1])}'
    return ', '.join(ym_label(m) for m in months)


def _within(a, b, tol) -> bool:
    return abs(a - b) <= max(tol * abs(b), 1.0)


def _pct1(ratio) -> str:
    return f'{ratio * 100:.1f}%'


def _money(v):
    return round(float(v), 2)


def _useful_tokens(text):
    """Payee words of a narration ('&' kept for display): no channel, month or reference tokens."""
    return [
        t
        for t in re.findall(r'[A-Z0-9&.]+', str(text or '').upper())
        if (t == '&' or t.strip('&.'))
        and not re.search(r'\d', t)
        and t not in _NARR_NOISE
        and t not in _MONTH_WORDS
    ]


def _payee_tokens(narration) -> frozenset:
    return frozenset(t for t in _useful_tokens(narration) if t.strip('&.'))


def _payee_label(narration) -> str:
    """'NEFT DR/RENT MAR26/VASANT JOSHI' -> 'RENT / VASANT JOSHI'."""
    parts = []
    for seg in re.split(r'[/|]+', str(narration or '')):
        tokens = _useful_tokens(seg)
        while tokens and tokens[0] == '&':
            tokens.pop(0)
        while tokens and tokens[-1] == '&':
            tokens.pop()
        if tokens:
            parts.append(' '.join(tokens))
    return ' / '.join(parts) or str(narration or '').strip() or '(no narration)'


def _keyword_category(narration):
    s = str(narration or '').upper()
    for cat, rx in _CATEGORY_KEYWORDS:
        if rx.search(s):
            return cat
    return None


def debit_category(model_category, narration) -> str:
    """The model's category, corrected by unambiguous narration keywords."""
    cat = model_category if model_category in DEBIT_CATEGORIES else 'other'
    kw = _keyword_category(narration)
    if cat == 'other' and kw:
        return kw
    if cat == 'loan_emi' and kw not in (None, 'loan_emi'):
        return kw  # e.g. 'ACH DR/SIP/...' labelled EMI
    return cat


def debit_channel(channel, narration) -> str:
    c = re.sub(r'[^A-Z]', '', str(channel or '').upper())
    if c == 'ENACH':
        c = 'NACH'
    if c in AUTO_DEBIT_CHANNELS or c == 'UPI':
        return c
    m = _CHANNEL_RE.match(str(narration or ''))
    if m:
        c = re.sub(r'[^A-Z]', '', m.group(1).upper())
        return 'NACH' if c == 'ENACH' else c
    return 'other'


def _statement_months(stmts):
    months = set()
    for d in stmts:
        f, t = to_date(d['fields'].get('statement_from')), to_date(d['fields'].get('statement_to'))
        if f and t and f <= t:
            months.update(month_span((f.year, f.month), (t.year, t.month)))
    return sorted(months)


def _collect_debits(stmts):
    """Bank-statement recurring_debits -> (engine debits deduplicated across
    statements, undated count, credit rows ignored, statements without a usable debit).

    An amount is unverified when grounding lists it, or when the statement had
    no machine text layer to verify against (grounded is False).
    """
    out, seen, undated, credits, empty = [], set(), 0, 0, []
    for d in stmts:
        grounded, _, unverified = _grounding_view(d)
        unverified = set(unverified)
        usable = 0
        for i, r in enumerate(d['fields'].get('recurring_debits') or []):
            if not isinstance(r, dict) or not _is_number(r.get('amount')) or not r['amount']:
                continue
            narration = str(r.get('narration') or '').strip()
            if _is_credit_row(narration):
                credits += 1
                continue
            dt = to_date(r.get('date'))
            if not dt:
                undated += 1
                continue
            usable += 1
            amount = abs(float(r['amount']))
            key = (dt, round(amount, 2), narration.upper())
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    'document_name': d['document_name'],
                    'date': dt,
                    'ym': (dt.year, dt.month),
                    'day': dt.day,
                    'amount': amount,
                    'narration': narration,
                    'channel': debit_channel(r.get('channel'), narration),
                    'category': debit_category(r.get('category'), narration),
                    'tokens': _payee_tokens(narration),
                    'payee': _payee_label(narration),
                    'unverified': grounded is False
                    or f'recurring_debits[{i}].amount' in unverified,
                }
            )
        if not usable and 'recurring_debits' in d['fields']:
            empty.append(d['document_name'])
    out.sort(key=lambda x: (x['date'], x['narration'], x['amount']))
    return out, undated, credits, empty


def _similar(a, b) -> bool:
    if not a and not b:
        return True
    return bool(a and b) and len(a & b) / len(a | b) >= 0.5


def _group_debits(debits, tol):
    groups = []
    for x in debits:
        for g in groups:
            if g['category'] != x['category'] or not _similar(g['tokens'], x['tokens']):
                continue
            if x['category'] in _FIXED_AMOUNT_CATEGORIES and not _within(
                x['amount'], g['ref_amount'], tol
            ):
                continue
            g['items'].append(x)
            break
        else:
            groups.append(
                {
                    'category': x['category'],
                    'tokens': x['tokens'],
                    'ref_amount': x['amount'],
                    'items': [x],
                }
            )
    return groups


def _most_common(values):
    counts = Counter(values)
    best = max(counts.values())
    return next(v for v in values if counts[v] == best)


def _group_view(g, n_months, tol):
    items = g['items']
    amounts = [x['amount'] for x in items]
    med = median(amounts)
    fixed = all(_within(a, med, tol) for a in amounts)
    days = sorted(x['day'] for x in items)
    day = days[(len(days) - 1) // 2]
    months = sorted({x['ym'] for x in items})
    return {
        'payee': _most_common([x['payee'] for x in items]),
        'narration': items[0]['narration'],
        'category': g['category'],
        'channel': _most_common([x['channel'] for x in items]),
        'amount': _money(med if fixed else sum(amounts) / len(amounts)),
        'min_amount': _money(min(amounts)),
        'max_amount': _money(max(amounts)),
        'fixed': fixed,
        'day_of_month': day,
        'day_consistent': all(abs(dd - day) <= EMI_DAY_TOLERANCE for dd in days),
        'months': [ym_str(m) for m in months],
        'months_seen': len(months),
        'months_total': n_months,
        'count': len(items),
        'unverified': any(x['unverified'] for x in items),
        'documents': sorted({x['document_name'] for x in items}),
        'evidence': [
            {
                'document_name': x['document_name'],
                'date': x['date'].isoformat(),
                'month': ym_str(x['ym']),
                'amount': _money(x['amount']),
                'narration': x['narration'],
            }
            for x in items
        ],
    }


def _lender_tokens(name) -> set:
    s = re.sub(r'\(.*?\)', ' ', str(name or '').lower())
    return {
        t.upper()
        for t in re.findall(r'[a-z0-9]+', s)
        if len(t) >= 3 and t not in _LENDER_STOP and not t.isdigit()
    }


def _declared_emis(app):
    """(declared EMIs or None when not extracted, reason when None)."""
    if not app:
        return None, 'no loan application in the file, so declared EMIs are unknown'
    f = app['fields']
    if 'declared_existing_emis' not in f and 'declared_total_existing_emi' not in f:
        return None, (
            f"declared obligations were not extracted from {app['document_name']} "
            '(analysed before obligations extraction); re-run the analysis'
        )
    grounded, _, unverified = _grounding_view(app)
    unverified = set(unverified)
    out, total_rows = [], []
    for i, e in enumerate(f.get('declared_existing_emis') or []):
        if not isinstance(e, dict) or not _is_number(e.get('amount')) or e['amount'] <= 0:
            continue
        if _TOTAL_ROW_RE.match(str(e.get('lender') or '')):
            total_rows.append((i, e['amount']))  # the table's total, not a loan
            continue
        out.append(
            {
                'lender': e.get('lender'),
                'loan_type': e.get('loan_type'),
                'amount': _money(e['amount']),
                'document_name': app['document_name'],
                'unverified': grounded is False
                or f'declared_existing_emis[{i}].amount' in unverified,
            }
        )
    total = f.get('declared_total_existing_emi')
    total_key = 'declared_total_existing_emi'
    if not (_is_number(total) and total > 0) and total_rows:
        i, total = max(total_rows, key=lambda t: t[1])
        total_key = f'declared_existing_emis[{i}].amount'
    if not out and _is_number(total) and total > 0:
        out.append(
            {
                'lender': None,
                'loan_type': None,
                'amount': _money(total),
                'document_name': app['document_name'],
                'unverified': grounded is False or total_key in unverified,
                'from_total': True,
            }
        )
    return out, None


def _declared_label(dec) -> str:
    who = dec.get('lender') or 'lender not named'
    kind = f" ({dec['loan_type']})" if dec.get('loan_type') else ''
    return f"{inr(dec['amount'])} {who}{kind} [{dec['document_name']}]"


def _debit_label(v) -> str:
    return (
        f"{v['channel'] if v['channel'] != 'other' else ''} debit {inr(v['amount'])} "
        f"'{v['payee']}' on the {_ordinal(v['day_of_month'])} in {v['months_seen']} of "
        f"{v['months_total']} months ({_months_label([to_month(m) for m in v['months']])}) "
        f"[{', '.join(v['documents'])}]"
    ).strip()


def _match_declared(declared, groups, views, stmt_months, tol):
    """Match each declared EMI to a bank debit group. Mutates group['_declared']."""
    n = len(stmt_months)
    results = []
    for dec in declared:
        lt = _lender_tokens(dec.get('lender'))
        best = None
        for gi, g in enumerate(groups):
            if g.get('_declared') is not None:
                continue
            lender_ok = bool(lt & g['tokens'])
            # a loan EMI, or an uncategorised debit naming the lender (never rent,
            # SIP, insurance, utilities or credit-card payments)
            if not (g['category'] == 'loan_emi' or (lender_ok and g['category'] == 'other')):
                continue
            hits = [x for x in g['items'] if _within(x['amount'], dec['amount'], tol)]
            if not hits:
                continue
            score = (lender_ok, g['category'] == 'loan_emi', len({x['ym'] for x in hits}))
            if best is None or score > best[0]:
                best = (score, gi, hits)
        res = {**dec, 'status': 'not_found', 'matched_payee': None, 'months_matched': 0,
               'months_total': n, 'day_of_month': None, 'bank_amount': None}
        if best:
            _, gi, hits = best
            g, v = groups[gi], views[gi]
            g['_declared'] = dec
            months = {x['ym'] for x in hits}
            days = [x['day'] for x in hits]
            majority = 2 * len(months) > n if n else len(months) >= 2
            day_ok = max(days) - min(days) <= 2 * EMI_DAY_TOLERANCE
            res.update(
                status='matched' if majority and day_ok else 'partial',
                matched_payee=v['payee'],
                months_matched=len(months),
                matched_months=[ym_str(m) for m in sorted(months)],
                day_of_month=v['day_of_month'],
                bank_amount=v['amount'],
                group=gi,
                day_consistent=day_ok,
                days=sorted(days),
            )
        else:
            same_lender = [
                gi
                for gi, g in enumerate(groups)
                if g.get('_declared') is None
                and lt & g['tokens']
                and (
                    g['category'] == 'loan_emi'
                    or (g['category'] == 'other' and views[gi]['channel'] in AUTO_DEBIT_CHANNELS)
                )
            ]
            if same_lender:
                gi = max(same_lender, key=lambda i: views[i]['months_seen'])
                groups[gi]['_declared'] = dec
                v = views[gi]
                res.update(
                    status='amount_differs',
                    matched_payee=v['payee'],
                    months_matched=0,
                    day_of_month=v['day_of_month'],
                    bank_amount=v['amount'],
                    group=gi,
                )
        results.append(res)
    return results


def _public_declared(res) -> dict:
    return {
        k: res.get(k)
        for k in (
            'lender', 'loan_type', 'amount', 'document_name', 'unverified', 'status',
            'matched_payee', 'bank_amount', 'months_matched', 'months_total',
            'matched_months', 'day_of_month',
        )
    }


def _obligations(docs_by_type, emi_tol):
    """Obligations summary + inputs of the EMI check (see module docstring)."""
    stmts = docs_by_type.get('bank_statement', [])
    app = (docs_by_type.get('loan_application') or [None])[0]
    stmt_names = [d['document_name'] for d in stmts]
    bank_available = any('recurring_debits' in d['fields'] for d in stmts)
    notes = []
    if not stmts:
        bank_reason = 'no bank statement in the file, so bank debits cannot be checked'
    elif not bank_available:
        bank_reason = (
            f"debit details were not extracted from {', '.join(stmt_names)} "
            '(analysed before obligations extraction); re-run the analysis'
        )
    else:
        bank_reason = None

    debits, undated, credit_rows, empty_stmts = (
        _collect_debits(stmts) if bank_available else ([], 0, 0, [])
    )
    if undated:
        notes.append(f'{_plural(undated, "debit")} without a readable date ignored')
    if credit_rows:
        notes.append(f'{_plural(credit_rows, "credit / balance row")} listed as debits ignored')
    stmt_months = _statement_months(stmts)
    if not stmt_months:
        stmt_months = sorted({x['ym'] for x in debits})
    n = len(stmt_months)
    latest = stmt_months[-1] if stmt_months else None
    groups = _group_debits(debits, emi_tol)
    views = [_group_view(g, n, emi_tol) for g in groups]

    declared, declared_reason = _declared_emis(app)
    matches = (
        _match_declared(declared, groups, views, stmt_months, emi_tol)
        if declared and bank_available
        else []
    )
    for gi, g in enumerate(groups):
        dec = g.get('_declared')
        views[gi]['declared'] = dec is not None
        views[gi]['declared_lender'] = (dec or {}).get('lender')
        views[gi]['declared_amount'] = (dec or {}).get('amount')

    fixed_loan, other_fixed, variable, one_off, undeclared = [], [], [], [], []
    for gi, g in enumerate(groups):
        v = views[gi]
        loan_like = g['category'] == 'loan_emi' or v['declared']
        recurring = v['months_seen'] >= 2
        # a loan EMI seen once, in the latest statement month, is a new loan
        # still running: it counts toward FOIR (a one-off earlier is not)
        current = bool(v['months']) and to_month(v['months'][-1]) == latest
        if loan_like and (recurring or v['declared'] or current):
            fixed_loan.append({**v, 'category': 'loan_emi'})
        elif recurring and v['fixed']:
            other_fixed.append(v)
        elif recurring:
            variable.append(v)
        else:
            one_off.append(v)
        if v['declared']:
            continue
        if g['category'] == 'loan_emi':
            undeclared.append({**v, 'kind': 'loan_emi'})
        elif (
            g['category'] == 'other'
            and v['channel'] in AUTO_DEBIT_CHANNELS
            and recurring
            and v['fixed']
        ):
            undeclared.append({**v, 'kind': 'possible_emi'})

    declared_out = (
        [_public_declared(m) for m in matches]
        if matches
        else [{**_public_declared({**d, 'months_total': n}), 'status': 'not_checked'}
              for d in declared or []]
    )
    not_found = [m for m in matches if m['status'] == 'not_found']
    loan_total = sum(v['amount'] for v in fixed_loan)
    if bank_available and declared is not None:
        foir_emis = loan_total + sum(m['amount'] for m in not_found)
        basis = 'bank debits and declared EMIs'
    elif bank_available:
        foir_emis = loan_total
        basis = 'bank debits only (declared EMIs not available)'
    elif declared is not None:
        foir_emis = sum(d['amount'] for d in declared)
        basis = 'declared EMIs only (bank debits not available)'
    else:
        foir_emis, basis = None, None

    other_total = sum(v['amount'] for v in other_fixed)
    review_notes = []
    # statements whose debit list is missing or may be incomplete: the EMI
    # check and FOIR are REVIEW, never a pass, until a person checks them
    incomplete = []
    old_stmts = [d['document_name'] for d in stmts if 'recurring_debits' not in d['fields']]
    if bank_available and old_stmts:
        incomplete += old_stmts
        review_notes.append(
            f"debit details were not extracted from {', '.join(old_stmts)} (analysed before "
            'obligations extraction); re-run the analysis'
        )
    for d in stmts if bank_available else []:
        g = d.get('grounding') if isinstance(d.get('grounding'), dict) else {}
        name = d['document_name']
        if name in old_stmts:
            continue
        if g.get('truncated') is True:
            incomplete.append(name)
            review_notes.append(
                f'the debit list of {name} may be incomplete (document text truncated for '
                'extraction); check the statement'
            )
        elif g.get('output_truncated') is True:
            incomplete.append(name)
            review_notes.append(
                f'the debit list of {name} may be incomplete (extraction output hit the '
                'token limit); re-run the analysis'
            )
        elif name in empty_stmts:
            incomplete.append(name)
            review_notes.append(
                f'no obligation debits (EMI, rent, SIP, bills) were extracted from {name}; '
                'confirm the statement has none'
            )
    total_declared = (app or {}).get('fields', {}).get('declared_total_existing_emi')
    if declared and _is_number(total_declared) and not any(d.get('from_total') for d in declared):
        listed = sum(d['amount'] for d in declared)
        if not _within(listed, total_declared, emi_tol):
            review_notes.append(
                f'declared total existing EMI {inr(total_declared)} differs from the listed '
                f'EMIs {inr(listed)} [{app["document_name"]}]'
            )
    notes.extend(review_notes)
    obligations = {
        'available': bank_available,
        'declared_available': declared is not None,
        'unavailable_reasons': [r for r in (bank_reason, declared_reason) if r],
        'statement_months': [ym_str(m) for m in stmt_months],
        'documents': ([app['document_name']] if app else []) + stmt_names,
        'fixed_loan_emis': fixed_loan,
        'other_fixed_debits': other_fixed,
        'variable_debits': variable,
        'one_off_debits': one_off,
        'declared_emis': declared_out,
        'undeclared_loan_debits': undeclared,
        'totals': {
            'loan_emis': _money(loan_total),
            'other_fixed': _money(other_total),
            'fixed_monthly': _money(loan_total + other_total),
            'variable_monthly_average': _money(sum(v['amount'] for v in variable)),
        },
        'foir_emi_total': _money(foir_emis) if foir_emis is not None else None,
        'foir_emi_basis': basis,
        'notes': notes
        + ['Only loan EMIs count toward FOIR; rent, SIP, utilities and credit-card '
           'payments do not.'],
    }
    if bank_available and declared is not None:
        counted_unverified = any(v['unverified'] for v in fixed_loan) or any(
            m['unverified'] for m in not_found
        )
    elif bank_available:
        counted_unverified = any(v['unverified'] for v in fixed_loan)
    else:
        counted_unverified = any(d['unverified'] for d in declared or [])
    return obligations, {
        'matches': matches,
        'views': views,
        'app': app,
        'stmt_months': stmt_months,
        'review_notes': review_notes,
        'incomplete_statements': incomplete,
        'counted_unverified': counted_unverified,
    }


def _emi_check(obligations, ctx):
    """(status, detail) of the 'declared_emis_vs_bank_debits' check."""
    matches, views, app = ctx['matches'], ctx['views'], ctx['app']
    stmt_months = ctx['stmt_months']
    problems, oks = [], []
    reasons = obligations['unavailable_reasons']
    months = _months_label(stmt_months)
    stmts = ', '.join(d for d in obligations['documents'] if not app or d != app['document_name'])
    if not obligations['available']:
        declared = obligations['declared_emis']
        if obligations['declared_available']:
            listed = '; '.join(_declared_label(d) for d in declared) or (
                f"no existing EMIs declared [{app['document_name']}]"
            )
            reasons = reasons + [f'declared: {listed}']
        return 'REVIEW', 'cannot be checked: ' + '; '.join(reasons)
    if not obligations['declared_available']:
        found = '; '.join(_debit_label(v) for v in obligations['fixed_loan_emis'])
        seen = (
            f'bank shows {found}'
            if found
            else f'no loan EMI debits in the bank statement ({months}) [{stmts}]'
        )
        return 'REVIEW', 'cannot be checked: ' + '; '.join(reasons + [seen])
    for m in matches:
        v = views[m['group']] if 'group' in m else None
        dec = _declared_label(m)
        if m['status'] == 'matched':
            oks.append(f'declared {dec} = {_debit_label(v)}')
        elif m['status'] == 'partial':
            why = []
            if m['months_matched'] * 2 <= m['months_total']:
                found = _months_label([to_month(x) for x in m['matched_months']])
                why.append(f"found in only {m['months_matched']} of {m['months_total']} months ({found})")
            if not m.get('day_consistent', True):
                why.append(f"debit day varies ({_ordinal(m['days'][0])}–{_ordinal(m['days'][-1])})")
            channel = f"{v['channel']} " if v['channel'] != 'other' else ''
            problems.append(
                f"declared {dec}: {channel}debit {inr(v['amount'])} '{v['payee']}' "
                f"{', '.join(why)} [{', '.join(v['documents'])}]"
            )
        elif m['status'] == 'amount_differs':
            problems.append(
                f"declared {dec} but the bank debits to '{v['payee']}' are {inr(v['amount'])} "
                f"({pct_diff(m['amount'], v['amount']):.1%} apart) [{', '.join(v['documents'])}]"
            )
        else:
            problems.append(
                f'declared {dec} not found in the bank debits ({months}) [{stmts}]; it may be '
                'paid from another account, so ask for that statement'
            )
    for u in obligations['undeclared_loan_debits']:
        if u['kind'] == 'loan_emi':
            problems.append(
                f"undeclared loan debit: {_debit_label(u)}; not on the application "
                f"[{app['document_name']}]"
            )
        else:
            problems.append(
                f'recurring auto-debit {_debit_label(u)}; confirm whether it is a loan EMI'
            )
    for v in obligations['fixed_loan_emis']:
        if v['unverified']:
            problems.append(
                f"amount of '{v['payee']}' not found in the document text; verify it "
                f"[{', '.join(v['documents'])}]"
            )
    for d in obligations['declared_emis']:
        if d.get('unverified'):
            problems.append(
                f"declared EMI {inr(d['amount'])} not found in the document text; verify it "
                f"[{d['document_name']}]"
            )
    problems.extend(ctx['review_notes'])
    if not matches and not obligations['undeclared_loan_debits']:
        oks.append(
            f"no existing EMIs declared [{app['document_name']}] and no loan EMI debits in "
            f'the bank statement ({months}) [{stmts}]'
        )
    if problems:
        return 'REVIEW', '; '.join(problems + oks)
    return 'OK', '; '.join(oks)


def _foir(obligations, income, checklist, octx=None):
    """Indicative FOIR block and (status, detail) of the 'foir' check."""
    octx = octx or {}
    cfg = checklist.get('foir') if isinstance(checklist.get('foir'), dict) else DEFAULT_FOIR
    limit = float(cfg.get('value', DEFAULT_FOIR['value']))
    hard = cfg.get('hard_limit') is True
    verified = [
        (v, label)
        for v, label in (
            (income.get('slip_net'), 'salary slips, median net pay'),
            (income.get('bank_salary_credit'), 'bank salary credits, median'),
        )
        if v
    ]
    if verified:
        net, net_source = min(verified, key=lambda t: t[0])
        income_verified = True
    elif income.get('declared_net'):
        net, net_source = income['declared_net'], 'declared on the loan application, not verified'
        income_verified = False
    else:
        net, net_source, income_verified = None, None, False
    emis = obligations['foir_emi_total']
    block = {
        'label': FOIR_LABEL,
        'indicative': True,
        'foir_limit': limit,
        'foir_limit_pct': round(limit * 100, 1),
        'hard_limit': hard,
        'limit_basis': cfg.get('basis'),
        'limit_source': cfg.get('source'),
        'limit_url': cfg.get('url'),
        'limit_note': cfg.get('note'),
        'limit_alternatives': list(cfg.get('alternatives') or []),
        'net_monthly_income': _money(net) if net else None,
        'income_source': net_source,
        'income_verified': income_verified,
        'existing_emis': emis,
        'existing_emis_basis': obligations['foir_emi_basis'],
        'existing_emi_ratio': None,
        'existing_emi_ratio_pct': None,
        'max_new_emi': None,
        'within_limit': None,
        'status': 'REVIEW',
        'detail': '',
    }
    limit_txt = f'FOIR {limit * 100:g}%'
    if not net or emis is None:
        missing = []
        if not net:
            missing.append('no net monthly income found (salary slips, bank salary credits or application)')
        if emis is None:
            missing.append('existing EMIs unknown: ' + '; '.join(obligations['unavailable_reasons']))
        block['detail'] = 'not computed: ' + '; '.join(missing)
        if hard:
            # a hard limit that cannot be checked must not let the file pass
            block['status'] = 'MISMATCH'
            block['detail'] += (
                f'; this checklist makes {limit_txt} a hard limit, so the file is not ready '
                'until FOIR can be computed'
            )
        return block
    ratio = emis / net
    max_new = max(0.0, round(limit * net - emis, 2))
    within = ratio <= limit + 1e-9
    block.update(
        existing_emi_ratio=round(ratio, 4),
        existing_emi_ratio_pct=round(ratio * 100, 1),
        max_new_emi=_money(max_new),
        within_limit=within,
    )
    detail = (
        f'existing loan EMIs {inr(emis)} / net monthly income {inr(net)} ({net_source}) = '
        f'{_pct1(ratio)}; at {limit_txt} the max new EMI is {inr(max_new)} '
        f'({limit:.2f} × {inr(net)} − {inr(emis)})'
    )
    partial = []
    if not income_verified:
        partial.append('income not verified')
    if obligations['foir_emi_basis'] != 'bank debits and declared EMIs':
        partial.append(f"EMIs from {obligations['foir_emi_basis']}")
    if octx.get('counted_unverified'):
        partial.append('an EMI amount not verified against the document text')
    if octx.get('incomplete_statements'):
        partial.append(
            'bank debits missing or incomplete for '
            + ', '.join(octx['incomplete_statements'])
        )
    possible = [
        u for u in obligations['undeclared_loan_debits'] if u.get('kind') == 'possible_emi'
    ]
    if possible:
        extra = sum(u['amount'] for u in possible)
        partial.append(
            f"{_plural(len(possible), 'possible EMI')} not counted ("
            + ', '.join(f"{inr(u['amount'])} '{u['payee']}'" for u in possible)
            + f'; with {"it" if len(possible) == 1 else "them"} FOIR is {_pct1((emis + extra) / net)})'
        )
    if not within:
        status = 'MISMATCH' if hard else 'REVIEW'
        detail = f'existing EMIs exceed {limit_txt}: ' + detail
    else:
        status = 'REVIEW' if partial else 'OK'
    if partial:
        detail += '; computed on partial data: ' + ', '.join(partial)
    block['status'] = status
    block['detail'] = f'{detail}. {FOIR_LABEL[0].upper()}{FOIR_LABEL[1:]}.'
    return block


def _consistency(docs, checklist, tol, f16_tol, emi_tol):
    """Plan B consistency checks + obligations / FOIR, limited to checklist.consistency_checks."""
    enabled = set(checklist.get('consistency_checks') or [])
    by_type = {}
    for d in docs:
        by_type.setdefault(d['doc_type'], []).append(d)
    rows, reasons, mismatches = [], [], []

    def add_check(check_id, status, detail, files=(), name=None):
        name = name or CHECK_NAMES.get(check_id, check_id)
        rows.append(
            {
                'check_id': check_id,
                'check': name,
                'status': status,
                'detail': detail,
                'documents': list(files),
            }
        )
        if status == 'MISMATCH':
            reasons.append(f'MISMATCH – {name}: {detail}')
            mismatches.append(f'{name}: {detail}')

    # PAN
    if 'pan' in enabled:
        pans = [
            (d['document_name'], _norm_pan(d['fields']['pan']))
            for d in docs
            if d['fields'].get('pan')
        ]
        distinct = sorted({p for _, p in pans})
        if not pans:
            add_check('pan', 'N/A', 'no PAN found on any document')
        elif len(distinct) == 1:
            add_check(
                'pan',
                'OK',
                f'{distinct[0]} on all {len(pans)} documents that carry a PAN',
                [f for f, _ in pans],
            )
        else:
            groups = {p: [f for f, q in pans if q == p] for p in distinct}
            parts = [
                f"{p} on {', '.join(fs)}"
                for p, fs in sorted(groups.items(), key=lambda kv: len(kv[1]))
            ]
            extra = ''
            if len(distinct) == 2 and len(distinct[0]) == len(distinct[1]):
                pos = [
                    i + 1
                    for i, (x, y) in enumerate(zip(*distinct))
                    if x != y
                ]
                extra = f" (differs at character {', '.join(map(str, pos))})"
            add_check(
                'pan', 'MISMATCH', '; '.join(parts) + extra, [f for f, _ in pans]
            )

    # Aadhaar (masked last 4)
    if 'aadhaar_last4' in enabled:
        aad = [
            (d['document_name'], str(d['fields']['masked_aadhaar_last4']))
            for d in docs
            if d['fields'].get('masked_aadhaar_last4')
        ]
        if aad:
            vals = sorted({a for _, a in aad})
            if len(vals) == 1:
                add_check(
                    'aadhaar_last4',
                    'OK',
                    f"XXXX-XXXX-{vals[0]} on {', '.join(f for f, _ in aad)}",
                    [f for f, _ in aad],
                )
            else:
                add_check(
                    'aadhaar_last4',
                    'MISMATCH',
                    '; '.join(f'XXXX-XXXX-{a} on {f}' for f, a in aad),
                    [f for f, _ in aad],
                )

    # Applicant name
    names = [
        (d['document_name'], d['fields']['applicant_name'])
        for d in docs
        if d['fields'].get('applicant_name')
    ]
    if 'applicant_name' in enabled and names:
        id_names = [
            d['fields']['applicant_name']
            for d in by_type.get('identity_details', [])
            if d['fields'].get('applicant_name')
        ]
        anchor = id_names[0] if id_names else max((n for _, n in names), key=len)
        bad = [(f, n) for f, n in names if not names_compatible(anchor, n)]
        variants = sorted({str(n).strip() for _, n in names})
        if bad:
            add_check(
                'applicant_name',
                'MISMATCH',
                f"'{anchor}' vs " + '; '.join(f"'{n}' on {f}" for f, n in bad),
                [f for f, _ in names],
            )
        else:
            note = (
                'identical'
                if len({' '.join(name_tokens(n)) for _, n in names}) == 1
                else 'acceptable variants: '
                + ' / '.join(f"'{v}'" for v in variants)
            )
            add_check(
                'applicant_name',
                'OK',
                f'{note} across {len(names)} documents',
                [f for f, _ in names],
            )

    # Employer (documents) + bank salary-credit narration
    stmts = by_type.get('bank_statement', [])
    slips = by_type.get('salary_slip', [])
    emps = [
        (d['document_name'], d['fields']['employer'])
        for d in docs
        if d['fields'].get('employer') and d['doc_type'] != 'bank_statement'
    ]
    credits = [
        (d['document_name'], c)
        for d in stmts
        for c in d['fields'].get('salary_credits') or []
        if isinstance(c, dict) and c.get('amount')
    ]
    if emps:
        cores = {employer_core(e) for _, e in emps}
        variants = sorted({str(e) for _, e in emps})
        if len(cores) > 1:
            if 'employer' in enabled:
                add_check(
                    'employer',
                    'MISMATCH',
                    '; '.join(f"'{e}' on {f}" for f, e in emps),
                    [f for f, _ in emps],
                )
        else:
            core = cores.pop()
            if 'employer' in enabled:
                note = (
                    f"'{variants[0]}'"
                    if len(variants) == 1
                    else 'acceptable variants: '
                    + ' / '.join(f"'{v}'" for v in variants)
                )
                add_check(
                    'employer',
                    'OK',
                    f'{note} across {len(emps)} documents',
                    [f for f, _ in emps],
                )
            if 'employer_vs_bank_credits' in enabled and credits:
                narr = sorted({c.get('narration') or '' for _, c in credits})
                ok = all(
                    set(core.split())
                    <= set(re.sub(r'[^a-z0-9 ]', ' ', n.lower()).split())
                    for n in narr
                )
                add_check(
                    'employer_vs_bank_credits',
                    'OK' if ok else 'MISMATCH',
                    (
                        'narration matches employer: '
                        if ok
                        else f"employer '{variants[0]}' not in narration: "
                    )
                    + '; '.join(f"'{n}'" for n in narr[:2])
                    + f' [{credits[0][0]}]',
                    [credits[0][0]],
                )

    # Income: declared (application) vs slips vs bank credits
    app = (by_type.get('loan_application') or [None])[0]
    declared = app['fields'].get('declared_net_salary') if app else None
    slip_net = [
        (d['document_name'], d['fields']['net_salary'])
        for d in slips
        if d['fields'].get('net_salary')
    ]
    slip_gross = [
        (d['document_name'], d['fields']['gross_salary'])
        for d in slips
        if d['fields'].get('gross_salary')
    ]
    net_med = median(v for _, v in slip_net) if slip_net else None
    gross_med = median(v for _, v in slip_gross) if slip_gross else None
    bank_med = median(c['amount'] for _, c in credits) if credits else None
    f16 = [
        (d['document_name'], d['fields']['gross_salary'])
        for d in by_type.get('form16_itr', [])
        if d['fields'].get('gross_salary')
    ]
    income = {
        'declared_net': declared,
        'slip_net': net_med,
        'slip_gross': gross_med,
        'bank_salary_credit': bank_med,
        'form16_gross': f16[0][1] if f16 else None,
        'slip_gross_x12': gross_med * 12 if gross_med else None,
        'bank_credits': [
            {'document_name': f, 'date': c.get('date'), 'amount': c['amount']}
            for f, c in credits
        ],
    }

    if 'declared_vs_slip_net' in enabled and declared and net_med:
        d = pct_diff(declared, net_med)
        status = 'MISMATCH' if d > tol else 'OK'
        detail = (
            f"declared {inr(declared)} [{app['document_name']}] vs slip net "
            f'{inr(net_med)} [{", ".join(f for f, _ in slip_net)}] – {d:.1%} apart'
        )
        if status == 'MISMATCH' and gross_med and pct_diff(declared, gross_med) <= 0.01:
            detail += (
                f'; declared amount equals slip GROSS {inr(gross_med)} '
                '→ gross appears declared as net'
            )
        add_check(
            'declared_vs_slip_net',
            status,
            detail,
            [app['document_name']] + [f for f, _ in slip_net],
        )
    if 'declared_vs_bank_credits' in enabled and declared and bank_med:
        d = pct_diff(declared, bank_med)
        dates = ', '.join(str(c.get('date')) for _, c in credits)
        detail = (
            f"declared {inr(declared)} [{app['document_name']}] vs bank salary "
            f'credits {inr(bank_med)} ({len(credits)} credits on {dates}) '
            f'[{credits[0][0]}] – {inr(abs(declared - bank_med))}/month, '
            f'{d:.1%} apart'
        )
        add_check(
            'declared_vs_bank_credits',
            'MISMATCH' if d > tol else 'OK',
            detail,
            [app['document_name'], credits[0][0]],
        )
    if 'slip_net_vs_bank_credits' in enabled and net_med and bank_med:
        d = pct_diff(net_med, bank_med)
        add_check(
            'slip_net_vs_bank_credits',
            'MISMATCH' if d > tol else 'OK',
            f'slip net {inr(net_med)} vs bank credit {inr(bank_med)} – '
            f'{d:.1%} apart',
            [f for f, _ in slip_net] + [credits[0][0]],
        )
    # Form-16 annual gross vs 12 x slip gross (informational only)
    if 'form16_vs_slip_gross' in enabled and f16 and gross_med:
        f16_file, f16_gross = f16[0]
        annual = gross_med * 12
        d = pct_diff(f16_gross, annual)
        add_check(
            'form16_vs_slip_gross',
            'OK' if d <= f16_tol else 'INFO',
            f'Form-16 annual gross {inr(f16_gross)} [{f16_file}] vs 12 × slip '
            f'gross {inr(gross_med)} = {inr(annual)} – {d:.1%} apart'
            + (' (reconciles)' if d <= f16_tol else ' (check increments / arrears)'),
            [f16_file] + [f for f, _ in slip_gross],
        )
    # Obligations (X12) and indicative FOIR (X14): REVIEW findings, never MISMATCH
    # (except a FOIR the checklist marks hard_limit).
    obligations, octx = _obligations(by_type, emi_tol)
    foir = None
    if 'declared_emis_vs_bank_debits' in enabled:
        status, detail = _emi_check(obligations, octx)
        add_check('declared_emis_vs_bank_debits', status, detail, obligations['documents'])
    if 'foir' in enabled:
        foir = _foir(obligations, income, checklist, octx)
        foir_docs = obligations['documents'] + [
            f for f, _ in slip_net if f not in obligations['documents']
        ]
        add_check('foir', foir['status'], foir['detail'], foir_docs)

    declared_checks = {'declared_vs_slip_net', 'declared_vs_bank_credits'}
    if not declared and enabled & declared_checks:
        add_check(
            'declared_net_salary',
            'N/A',
            'no declared net salary found (application missing or blank)',
            name='Declared net salary',
        )

    unknown = by_type.get('other', [])
    if unknown:
        rows.append(
            {
                'check_id': 'unclassified_documents',
                'check': 'Unclassified documents',
                'status': 'INFO',
                'detail': ', '.join(d['document_name'] for d in unknown),
                'documents': [d['document_name'] for d in unknown],
            }
        )
    return rows, reasons, mismatches, income, obligations, foir


def _grounding_view(doc):
    g = doc.get('grounding')
    if isinstance(g, dict):
        return (
            g.get('grounded'),
            list(g.get('notes') or []),
            list(g.get('unverified_fields') or []),
        )
    if isinstance(g, list):
        return None, list(g), []
    return None, [], []


def evaluate_applicant(
    docs, checklist, reference_month=None, confirmations=None, now=None
) -> dict:
    """Apply one checklist to one applicant's documents (see interfaces s.4/5).

    `confirmations`: the project's confirmation records (see the module
    docstring); only this applicant's apply, and only to manual items.
    """
    docs = sorted((_to_engine_doc(d) for d in docs), key=_sort_key)
    items = checklist.get('items') or []
    tol = checklist.get('tolerance_pct', DEFAULT_TOLERANCE_PCT) / 100
    f16_tol = (
        checklist.get('form16_tolerance_pct', DEFAULT_FORM16_TOLERANCE_PCT) / 100
    )
    emi_tol = checklist.get('emi_tolerance_pct', DEFAULT_EMI_TOLERANCE_PCT) / 100
    ref = _reference_month(docs, items, reference_month)
    confirmed_index = _confirmation_index(confirmations, now)
    keys = _applicant_keys(docs) if confirmed_index else set()
    doc_ids = {d['document_id'] for d in docs if d['document_id']}

    rows, reasons, missing_items, manual_review, confirmed = [], [], [], [], []
    for item in items:
        rule = item.get('rule') or {}
        kind = rule.get('kind')
        label = item.get('label') or item.get('id')
        required = _item_required(item)
        idocs = _item_docs(docs, item)
        months = None
        conf = None
        if kind == 'present':
            res, _ = _eval_present(item, idocs)
        elif kind == 'monthly':
            res, months = _eval_monthly(item, idocs, ref)
        elif kind == 'period':
            res, months = _eval_period(item, idocs, ref)
        else:
            res, _ = _eval_manual(item, idocs)
            conf = _confirmation_for(confirmed_index, keys, item.get('id'))
        # A confirmation holds while every document it was made on is still
        # in this applicant's file (not deleted, erased or moved elsewhere).
        stale = bool(conf) and not set(conf['document_ids']) <= doc_ids
        if kind == 'manual' and conf and not stale:
            status = CONFIRMED
            res = {**res, 'ok': True, 'detail': _confirmed_by(conf)}
            confirmed.append(
                {'item_id': item.get('id'), 'item': label, **_public_confirmation(conf)}
            )
        elif kind == 'manual':
            status = 'REVIEW'
            manual_review.append(label)
            if stale:
                res = {
                    **res,
                    'detail': f"{res['detail']}; {_confirmed_by(conf)}, but a document it "
                    'was made on is no longer in this file: confirm again',
                }
        else:
            status = 'PRESENT' if res['ok'] else 'MISSING'
        row = {
            'item_id': item.get('id'),
            'item': label,
            'required': required,
            'status': status,
            'ok': res['ok'],
            'detail': res['detail'],
            'documents': res['documents'],
        }
        if months is not None:
            row['required_months'] = [ym_str(m) for m in months[0]]
            row['missing_months'] = [ym_str(m) for m in months[1]]
        if status == CONFIRMED:
            row['confirmation'] = _public_confirmation(conf)
        elif stale:
            row['stale_confirmation'] = _public_confirmation(conf)
        rows.append(row)
        if status == 'REVIEW' and required:
            reasons.append(f"REVIEW – {label}: {res['detail']}")
        if status == 'MISSING' and required:
            reasons.append(f"MISSING – {label}: {res['detail']}")
            missing = months[1] if months else []
            if missing:
                prefix = item.get('missing_label') or label
                missing_items.append(
                    f"{prefix}: {', '.join(ym_label(m) for m in missing)}"
                )
            else:
                missing_items.append(label)

    consistency, c_reasons, mismatches, income, obligations, foir = _consistency(
        docs, checklist, tol, f16_tol, emi_tol
    )
    reasons.extend(c_reasons)
    # NEEDS REVIEW findings: reported, never a silent pass, verdict unchanged.
    needs_review = [
        f"{c['check']}: {c['detail']}" for c in consistency if c['status'] == 'REVIEW'
    ]

    ready = all(
        r['status'] in MET_STATUSES for r in rows if r['required']
    ) and not any(c['status'] == 'MISMATCH' for c in consistency)

    documents = []
    for d in docs:
        grounded, notes, unverified = _grounding_view(d)
        documents.append(
            {
                'document_id': d['document_id'],
                'document_name': d['document_name'],
                'doc_type': d['doc_type'],
                'grounded': grounded,
                'grounding_notes': notes,
                'unverified_fields': unverified,
                # tokens and cost of this document's facts extraction (None: not recorded)
                'usage': d['usage'],
            }
        )

    return {
        'applicant': _display_name(docs),
        'pan': _group_pan(docs),
        'verdict': 'READY' if ready else 'NOT READY',
        'reference_month': ym_str(ref) if ref else None,
        'reference_month_label': ym_label(ref) if ref else None,
        'documents': documents,
        'usage_total': _usage_total(documents),
        'checklist': rows,
        'consistency': consistency,
        'income': income,
        'obligations': obligations,
        'foir': foir,
        'reasons': reasons,
        'missing_items': missing_items,
        'mismatches': mismatches,
        'needs_review': needs_review,
        'manual_review': manual_review,
        # manual items a person confirmed: {item_id, item, confirmed_by, confirmed_at}
        'confirmed_items': confirmed,
    }


# ------------------------------------------------------------------ file check
def _pan_matches(group, applicant) -> bool:
    """`applicant` is a PAN carried by a document of the group."""
    want_pan = _norm_pan(applicant)
    pans = {_norm_pan(d['fields'].get('pan')) for d in group if d['fields'].get('pan')}
    return bool(want_pan and want_pan in pans)


def _name_matches(group, applicant) -> bool:
    """`applicant` is a name compatible with the group's display name."""
    display = _display_name(group)
    return bool(
        name_tokens(applicant)
        and display != 'Unknown'
        and names_compatible(applicant, display)
    )


def _applicant_matches(group, applicant) -> bool:
    return _pan_matches(group, applicant) or _name_matches(group, applicant)


def mask_pan(pan):
    """'BQXPD4821K' -> 'XXXXXX821K': only the last 4 characters (like the masked Aadhaar)."""
    p = _norm_pan(pan)
    if not p:
        return None
    if len(p) <= 4:
        return 'X' * len(p)
    return 'X' * (len(p) - 4) + p[-4:]


def applicant_documents(facts, applicant, documents=None) -> dict:
    """The documents of ONE applicant, grouped exactly as run_file_check groups them.

    For the erase-applicant flow (DPDP right to erasure): it must remove the
    documents the verdict shows under that applicant, no more, no fewer.
    `applicant` is a PAN or a name as the verdict shows it. PAN first: a group
    carrying that PAN is the match; else every group whose display name is
    compatible with the name (names_compatible). Only the analysed documents of
    the group are listed (pending, failed, no-facts, unsupported and
    unassigned documents belong to no applicant, as in the verdict).

    Returns {applicant_name, pan_masked, documents: [{document_id, name}],
    matches}. `matches` is the number of groups that matched; unless it is 1,
    applicant_name and pan_masked are None and documents is empty.
    """
    engine_docs = _classify(facts, documents)[0]
    groups, _ = group_applicants(engine_docs)
    query = str(applicant or '').strip()
    matched = [g for g in groups if _pan_matches(g, query)] if query else []
    if query and not matched:
        matched = [g for g in groups if _name_matches(g, query)]
    result = {'applicant_name': None, 'pan_masked': None, 'documents': [], 'matches': len(matched)}
    if len(matched) == 1:
        group = matched[0]
        result.update(
            applicant_name=_display_name(group),
            pan_masked=mask_pan(_group_pan(group)),
            documents=[
                {'document_id': d['document_id'], 'name': d['document_name']} for d in group
            ],
        )
    return result


def _mime(file_type) -> str:
    return str(file_type or '').split(';')[0].strip().lower()


def _is_recording(file_type, name) -> bool:
    """An audio / video document (by MIME type, else by file extension)."""
    mime = _mime(file_type)
    if mime.startswith(RECORDING_MIME_PREFIXES):
        return True
    return mime in ('', 'application/octet-stream') and str(name or '').lower().endswith(
        RECORDING_EXTENSIONS
    )


def _recording(document_id, name, file_type) -> dict:
    return {'document_id': document_id, 'document_name': name, 'file_type': file_type or None}


def _classify(facts, documents):
    """Split project items into engine docs and the reporting buckets.

    A recording only counts as a loan document when facts were extracted from
    it; otherwise it goes to `recordings`, never pending or no-facts.
    """
    pending, failed, no_facts, unsupported, recordings = [], [], [], [], []
    engine_docs = []
    if documents is None:
        for f in facts or []:
            if not isinstance(f, dict):
                continue
            name = f.get('document_name') or f.get('document_id')
            if f.get('status', 'completed') == 'completed':
                engine_docs.append(_to_engine_doc(f))
            elif f.get('reason') == 'media' or _is_recording(f.get('file_type'), name):
                recordings.append(_recording(f.get('document_id'), name, f.get('file_type')))
            else:
                no_facts.append(
                    {
                        'document_id': f.get('document_id'),
                        'document_name': name,
                        'reason': _no_facts_reason(f),
                    }
                )
        return engine_docs, pending, failed, no_facts, unsupported, recordings

    facts_by_id = {
        f.get('document_id'): f for f in facts or [] if isinstance(f, dict)
    }
    docs = [d for d in documents if isinstance(d, dict)]
    for doc in sorted(
        docs, key=lambda d: (str(d.get('name') or ''), str(d.get('document_id') or ''))
    ):
        did = doc.get('document_id')
        fact = facts_by_id.get(did)
        name = doc.get('name') or (fact or {}).get('document_name') or did
        status = doc.get('status')
        file_type = _mime(doc.get('file_type'))
        fact_ok = bool(fact) and fact.get('status') == 'completed'
        spreadsheet = file_type in UNSUPPORTED_FILE_TYPES or (
            file_type in ('', 'application/octet-stream')
            and str(name or '').lower().endswith(UNSUPPORTED_EXTENSIONS)
        )
        if not fact_ok and _is_recording(file_type, name):
            recordings.append(_recording(did, name, doc.get('file_type')))
        elif spreadsheet:
            unsupported.append(
                {
                    'document_id': did,
                    'document_name': name,
                    'file_type': doc.get('file_type'),
                    'reason': UNSUPPORTED_REASON,
                }
            )
        elif status not in ('completed', 'failed'):
            pending.append({'document_id': did, 'document_name': name, 'status': status})
        elif status == 'failed' and not fact_ok:
            failed.append({'document_id': did, 'document_name': name})
        elif not fact_ok:
            no_facts.append(
                {
                    'document_id': did,
                    'document_name': name,
                    'reason': _no_facts_reason(fact),
                }
            )
        else:
            engine_docs.append(_to_engine_doc({**fact, 'document_name': name}))
    return engine_docs, pending, failed, no_facts, unsupported, recordings


def _no_facts_reason(fact):
    if not fact:
        return 'no facts extracted for this document yet'
    status = fact.get('status')
    if status == 'skipped':
        return f"facts skipped: {fact.get('reason') or 'no text'}"
    if status == 'failed':
        return 'facts extraction failed'
    return f'facts status {status}'


def _as_of(as_of):
    if isinstance(as_of, datetime):
        return as_of.date().isoformat()
    if isinstance(as_of, date):
        return as_of.isoformat()
    if as_of:
        return str(as_of)
    return datetime.now(timezone.utc).date().isoformat()


def run_file_check(
    facts,
    checklist,
    documents=None,
    reference_month=None,
    applicant=None,
    as_of=None,
    project_id=None,
    confirmations=None,
    now=None,
) -> dict:
    """Full run_file_check response (interfaces section 5).

    `confirmations`: the data of the project's FCCONF# items (confirmed
    needs-review items); `now` (datetime or epoch seconds, default the clock)
    decides which of them have expired.
    """
    engine_docs, pending, failed, no_facts, unsupported, recordings = _classify(facts, documents)
    groups, unassigned = group_applicants(engine_docs)
    if applicant:
        groups = [g for g in groups if _applicant_matches(g, applicant)]

    applicants = [
        evaluate_applicant(g, checklist, reference_month, confirmations=confirmations, now=now)
        for g in groups
    ]
    if pending:
        names = ', '.join(p['document_name'] or p['document_id'] for p in pending)
        reason = (
            f'PENDING – {len(pending)} document(s) still being analysed: {names}'
        )
        for a in applicants:
            a['verdict'] = 'NOT READY'
            a['reasons'].insert(0, reason)

    overall = (
        'READY'
        if applicants
        and not pending
        and all(a['verdict'] == 'READY' for a in applicants)
        else 'NOT READY'
    )

    if applicants:
        parts = []
        for a in applicants:
            part = f"{a['applicant']} {a['verdict']}"
            notes = []
            if a['verdict'] != 'READY' and a['reasons']:
                notes.append(_plural(len(a['reasons']), 'issue'))
            if a['needs_review']:
                notes.append(f"{len(a['needs_review'])} to review")
            if a['confirmed_items']:
                notes.append(f"{len(a['confirmed_items'])} confirmed")
            if notes:
                part += f" ({', '.join(notes)})"
            parts.append(part)
        summary = f"{_plural(len(applicants), 'applicant')}: " + '; '.join(parts)
    elif applicant:
        summary = f"No applicant matching '{applicant}'"
    elif unassigned:
        summary = (
            'No applicant could be identified '
            f'({_plural(len(unassigned), "unassigned document")})'
        )
    elif recordings and not (pending or failed or no_facts or unsupported):
        summary = (
            'No loan documents in this project, only '
            f"{_plural(len(recordings), 'call recording')}: the file check applies "
            'to loan files; review calls with Call QA'
        )
    else:
        summary = 'No analysed documents found in this project'

    if project_id is None:
        for src in list(documents or []) + list(facts or []):
            if isinstance(src, dict) and src.get('project_id'):
                project_id = src['project_id']
                break

    return {
        'project_id': project_id,
        'engine_version': ENGINE_VERSION,
        'as_of': _as_of(as_of),
        'checklist': {'id': checklist.get('id'), 'name': checklist.get('name')},
        'overall_verdict': overall,
        'summary': summary,
        'applicants': applicants,
        'pending_documents': pending,
        'failed_documents': failed,
        'no_facts_documents': no_facts,
        'unsupported_documents': unsupported,
        'unassigned_documents': [
            {
                'document_id': d['document_id'],
                'document_name': d['document_name'],
                'doc_type': d['doc_type'],
            }
            for d in unassigned
        ],
        # audio / video documents: call recordings, not part of any loan file
        'recording_documents': recordings,
        'assistant_instructions': ASSISTANT_INSTRUCTIONS,
    }
