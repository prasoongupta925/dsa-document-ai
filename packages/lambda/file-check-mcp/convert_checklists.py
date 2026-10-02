"""Convert a ready-made loan-product checklist file into the engine catalog.

The ready-made checklists.json has an unknown shape, so this accepts the common
ones and maps each document to an engine checklist item by keyword:

  {'products': [...]} | {'checklists': [...]} | [product, ...]
  | {'<product name>': [documents...]}

  product:  name|product|title|loan_product, optional
            applicant_type|customer_type|profile, and documents under
            documents|items|checklist|required_documents|docs
  document: 'Salary slips (3 months)' or {'name'|'document'|'label'|'title',
            'mandatory'|'required': bool, 'months'|'period': 6 | '6 months'}

Unmapped documents become manual items (status REVIEW), each with an
'unmapped: ...' warning. A manual item keeps the document's required flag, so a
mandatory document the rules cannot verify keeps the verdict NOT READY instead
of being silently skipped. The output always passes engine.validate_catalog.

The research file ready-made/checklists/checklists.json (schema_version
'1.0.0': brand rule sets with checklist_id, documents[].doc_type, period,
severity, checks and cross_check_ids) is recognised and converted by
convert_ready_made(); see that function for the mapping.

Usage:
  python convert_checklists.py INPUT.json [-o OUT.json] [--merge BASE.json]
                               [--replace]

Regenerate the bundled catalog (default checklist first, then the ready-made
rule sets), then format it with prettier:
  python convert_checklists.py .../ready-made/checklists/checklists.json \
      --merge checklists.json --replace -o checklists.json
"""

import argparse
import json
import re
import sys

import engine

NAME_KEYS = ('name', 'product', 'title', 'loan_product')
APPLICANT_KEYS = ('applicant_type', 'customer_type', 'profile')
DOCS_KEYS = ('documents', 'items', 'checklist', 'required_documents', 'docs')
DOC_NAME_KEYS = ('name', 'document', 'label', 'title')
DOC_REQUIRED_KEYS = ('mandatory', 'required')
DOC_MONTHS_KEYS = ('months', 'period')

BASIC_CHECKS = ['pan', 'aadhaar_last4', 'applicant_name']

_MONTHS_RE = re.compile(r'(\d+)\s*months?', re.I)

# (pattern, doc_type, kind, default months, missing_label). Order matters:
# 'bank statement with salary credits' is a bank statement, not a slip.
_RULES = [
    (r'bank\s+statement', 'bank_statement', 'period', 6, 'Bank statement'),
    (
        r'form[\s\-]?16|\bitr\b|income\s+tax\s+return',
        'form16_itr',
        'present',
        None,
        None,
    ),
    (r'salary|pay\s*slip', 'salary_slip', 'monthly', 3, 'Salary slip'),
    (r'application|loan\s+form', 'loan_application', 'present', None, None),
    (
        r'\bpan\b|aadhaa?r|\bkyc\b|identity|\bid\s+proof',
        'identity_details',
        'present',
        None,
        None,
    ),
]


def slug(text, fallback='item') -> str:
    s = re.sub(r'[^a-z0-9]+', '_', str(text or '').lower()).strip('_')
    return s or fallback


def _unique(base: str, used: set) -> str:
    uid, n = base, 2
    while uid in used:
        uid = f'{base}_{n}'
        n += 1
    used.add(uid)
    return uid


def _first(d: dict, keys):
    for k in keys:
        if d.get(k) not in (None, ''):
            return d[k]
    return None


def _months_value(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        m = _MONTHS_RE.search(value) or re.fullmatch(r'\s*(\d+)\s*', value)
        return int(m.group(1)) if m else None
    return None


def _products(data, warnings):
    """Yield (name, applicant_type, description, docs) from any input shape."""
    if isinstance(data, dict):
        for key in ('products', 'checklists'):
            if isinstance(data.get(key), list):
                return _product_list(data[key], warnings)
        if _first(data, NAME_KEYS) and isinstance(_first(data, DOCS_KEYS), list):
            return _product_list([data], warnings)
        out = []
        for name, value in data.items():
            if isinstance(value, list):
                out.append((str(name), None, None, value))
            elif isinstance(value, dict):
                out.extend(_product_list([{'name': name, **value}], warnings))
            else:
                warnings.append(f'skipped {name!r}: not a list of documents')
        return out
    if isinstance(data, list):
        return _product_list(data, warnings)
    raise ValueError('input must be a JSON object or list of products')


def _product_list(items, warnings):
    out = []
    for i, p in enumerate(items):
        if not isinstance(p, dict):
            warnings.append(f'skipped product #{i + 1}: not an object')
            continue
        name = _first(p, NAME_KEYS)
        docs = _first(p, DOCS_KEYS)
        if not name:
            warnings.append(f'skipped product #{i + 1}: no name')
            continue
        if not isinstance(docs, list):
            warnings.append(f'product {name!r}: no document list')
            docs = []
        out.append(
            (str(name), _first(p, APPLICANT_KEYS), p.get('description'), docs)
        )
    return out


def _convert_doc(doc, used_ids, warnings, product):
    if isinstance(doc, str):
        label, required, months = doc.strip(), True, None
    elif isinstance(doc, dict):
        label = str(_first(doc, DOC_NAME_KEYS) or '').strip()
        req = _first(doc, DOC_REQUIRED_KEYS)
        required = req if isinstance(req, bool) else True
        months = _months_value(_first(doc, DOC_MONTHS_KEYS))
    else:
        warnings.append(f'{product}: skipped a document that is not text')
        return None
    if not label:
        warnings.append(f'{product}: skipped a document without a name')
        return None

    item = {'id': _unique(slug(label), used_ids), 'label': label}
    for pattern, doc_type, kind, default_months, missing_label in _RULES:
        if not re.search(pattern, label, re.I):
            continue
        item.update(doc_types=[doc_type], required=required)
        if kind == 'present':
            item['rule'] = {'kind': 'present'}
            return item
        if months is None:
            m = _MONTHS_RE.search(label)
            months = int(m.group(1)) if m else default_months
        if not 1 <= months <= 24:
            warnings.append(
                f'{product}: {label!r} months {months} out of 1..24; '
                f'using {default_months}'
            )
            months = default_months
        item['missing_label'] = missing_label
        if kind == 'monthly':
            item['rule'] = {'kind': 'monthly', 'months': months, 'field': 'month'}
        else:
            item['rule'] = {
                'kind': 'period',
                'months': months,
                'from_field': 'statement_from',
                'to_field': 'statement_to',
            }
        return item

    warnings.append(f'unmapped: {label} ({product}); added as a manual review item')
    item.update(doc_types=[], required=required, rule={'kind': 'manual'})
    return item


def convert(data):
    """Return (catalog, warnings) for a ready-made checklist document."""
    if is_ready_made(data):
        return convert_ready_made(data)
    warnings = []
    checklists, used_ids = [], set()
    for name, applicant_type, description, docs in _products(data, warnings):
        item_ids, items = set(), []
        for doc in docs:
            item = _convert_doc(doc, item_ids, warnings, name)
            if item:
                items.append(item)
        if not items:
            warnings.append(f'product {name!r}: no documents')
        types = {t for it in items for t in it['doc_types']}
        checks = (
            list(engine.CHECK_IDS)
            if types & {'salary_slip', 'bank_statement'}
            else list(BASIC_CHECKS)
        )
        checklists.append(
            {
                'id': _unique(slug(name, 'checklist'), used_ids),
                'name': name,
                'product': slug(name, 'product'),
                'applicant_type': str(applicant_type or 'any'),
                'description': str(
                    description or f'Converted from ready-made checklist: {name}'
                ),
                'tolerance_pct': engine.DEFAULT_TOLERANCE_PCT,
                'form16_tolerance_pct': engine.DEFAULT_FORM16_TOLERANCE_PCT,
                'items': items,
                'consistency_checks': checks,
            }
        )
    if not checklists:
        raise ValueError('no products found in the input')
    catalog = {
        'schema_version': 1,
        'default_checklist': checklists[0]['id'],
        'checklists': checklists,
    }
    errors = engine.validate_catalog(catalog)
    if errors:
        raise ValueError('converted catalog is invalid: ' + '; '.join(errors))
    return catalog, warnings


# ------------------------------------------------------------ ready-made 1.0.0
# ready-made doc_type -> engine doc type. Only types whose accepted variants
# the facts step classifies exactly are mapped. address_proof is deliberately
# NOT mapped although the research file's lean_check_type says
# identity_details: its variants (Passport, Utility Bill) cannot be told apart
# from a PAN / Aadhaar identity document, so it stays a manual (REVIEW) item.
READY_MADE_DOC_TYPES = {
    'loan_application': 'loan_application',
    'identity_proof': 'identity_details',
    'kyc': 'identity_details',
    'salary_slip': 'salary_slip',
    'bank_statement': 'bank_statement',
    'itr': 'form16_itr',
    'form16_itr': 'form16_itr',
}

# ready-made cross_check_ids -> engine consistency checks. X10 (gross declared
# as net) is part of declared_vs_slip_net's detail and X11 (statement coverage)
# is the bank statement period item, so neither needs a row of its own. X12
# (EMI debits vs declared EMIs) and X14 (FOIR, indicative) are computed by the
# engine from the obligations facts; both are warnings (REVIEW, never MISMATCH).
READY_MADE_CROSS_CHECKS = {
    'X01': ['applicant_name'],
    'X03': ['pan'],
    'X04': ['aadhaar_last4'],
    'X06': ['employer', 'employer_vs_bank_credits'],
    'X07': ['declared_vs_slip_net'],
    'X08': ['declared_vs_bank_credits'],
    'X09': ['slip_net_vs_bank_credits'],
    'X10': [],
    'X11': [],
    'X12': ['declared_emis_vs_bank_debits'],
    'X14': ['foir'],
}

_APPLICANT_LABELS = {
    'salaried': 'Salaried',
    'self_employed': 'Self-employed',
    'student': 'Student',
}
_CO_APPLICANT_SUFFIX = {
    'COSAL': 'salaried co-applicant',
    'COSE': 'self-employed co-applicant',
}


def is_ready_made(data) -> bool:
    """True for the research file's shape (brand rule sets, schema '1.0.0')."""
    if not isinstance(data, dict) or not isinstance(data.get('doc_types'), dict):
        return False
    lists = data.get('checklists')
    return (
        isinstance(lists, list)
        and bool(lists)
        and all(
            isinstance(c, dict)
            and 'checklist_id' in c
            and isinstance(c.get('documents'), list)
            for c in lists
        )
    )


def _ready_made_name(cl) -> str:
    brand = cl.get('brand_name') or cl.get('brand') or ''
    applicant = _APPLICANT_LABELS.get(
        cl.get('applicant_type'), str(cl.get('applicant_type') or 'any')
    )
    suffix = _CO_APPLICANT_SUFFIX.get(str(cl['checklist_id']).rsplit('-', 1)[-1])
    if suffix:
        applicant = f'{applicant} ({suffix})'
    return ' - '.join(p for p in (brand, cl.get('product'), applicant) if p)


def _foir_source(brand_name, e) -> str:
    return f"{brand_name} {e.get('where')}: '{e.get('verbatim')}'"


def _ready_made_foir(data, cl):
    """Checklist FOIR from parameters.foir (default_for_checker by brand /
    brand:product, cited with the published evidence). None when absent."""
    foir = (data.get('parameters') or {}).get('foir')
    if not isinstance(foir, dict):
        return None
    brand = str(cl.get('brand') or '')
    product = str(cl.get('product_key') or '')
    defaults = foir.get('default_for_checker') or {}
    value = defaults.get(f'{brand}:{product}', defaults.get(brand))
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= 1:
        return None
    evidence = [
        e for e in (foir.get('by_brand') or {}).get(brand) or [] if isinstance(e, dict)
    ]
    same = [e for e in evidence if e.get('value') == value]
    words = product.replace('_', ' ')
    primary = next(
        (e for e in same if words and words in str(e.get('where') or '').lower()),
        same[0] if same else None,
    )
    brand_name = cl.get('brand_name') or brand
    others = [e for e in evidence if e.get('value') != value]
    out = {
        'value': value,
        'hard_limit': False,
        'basis': 'PUBLISHED' if not others and primary else defaults.get('basis', 'DEMO-POLICY'),
        'source': _foir_source(brand_name, primary) if primary else f'{brand_name} (no published FOIR)',
        'url': (primary or {}).get('url'),
    }
    if others:
        out['alternatives'] = [
            {'value': e.get('value'), 'source': _foir_source(brand_name, e), 'url': e.get('url')}
            for e in others
        ]
        shown = ', '.join(
            f"{e.get('value') * 100:g}% ({e.get('where')})" for e in others
        )
        out['note'] = (
            f'{brand_name} also shows {shown}; {value * 100:g}% is used for eligibility. '
            'Confirm with the lender.'
        )
    return out


def _ready_made_doc(doc, cl_type, used_ids, warnings, where):
    """One ready-made document -> engine item (machine rule or manual)."""
    label = str(doc.get('label') or doc.get('verbatim') or '').strip()
    rid = doc.get('rule_id') or doc.get('id') or label
    if not label:
        warnings.append(f'{where}: skipped document {rid!r} without a label')
        return None
    applies = str(doc.get('applies_to') or 'both')
    blocker = doc.get('required') is True and doc.get('severity') == 'blocker'
    item = {'id': _unique(slug(rid), used_ids), 'label': label}

    conditional = applies.startswith('conditional(')
    if conditional:
        item['label'] = f'{label} (only if {applies[12:-1]})'
    doc_type = READY_MADE_DOC_TYPES.get(doc.get('doc_type'))
    if applies not in ('both', cl_type) or doc_type is None:
        # A co-applicant's documents, a conditional document or a document
        # type the facts step does not classify: a person must check it. It
        # blocks READY only when it is a required blocker for this applicant.
        in_scope = applies in ('both', cl_type, 'co_applicant')
        if not in_scope and not conditional:
            warnings.append(f'{where}: {rid} applies to {applies!r}')
        item.update(
            doc_types=[],
            required=blocker and in_scope,
            rule={'kind': 'manual'},
        )
        return item

    period = doc.get('period') if isinstance(doc.get('period'), dict) else {}
    length = period.get('length')
    item.update(doc_types=[doc_type], required=blocker)
    if doc_type == 'salary_slip' and period.get('unit') == 'month' and length:
        item['missing_label'] = 'Salary slip'
        item['rule'] = {'kind': 'monthly', 'months': int(length), 'field': 'month'}
    elif doc_type == 'bank_statement' and period.get('unit') == 'month' and length:
        item['missing_label'] = 'Bank statement'
        item['rule'] = {
            'kind': 'period',
            'months': int(length),
            'from_field': 'statement_from',
            'to_field': 'statement_to',
        }
    else:
        # Present: one document per required FY (ITR) or min_count documents.
        count = length if period.get('unit') == 'fy' else doc.get('min_count')
        count = count if isinstance(count, int) and count >= 1 else 1
        item['rule'] = (
            {'kind': 'present', 'min_count': count}
            if count > 1
            else {'kind': 'present'}
        )
    return item


def _ready_made_check(check, used_ids):
    """An eligibility / trigger check -> manual item (never computed here)."""
    cid = check.get('check_id') or check.get('field') or 'check'
    note = str(check.get('note') or '').split('. ')[0].strip().rstrip('.')
    text = check.get('verbatim') or note or check.get('field') or cid
    return {
        'id': _unique(slug(cid), used_ids),
        'label': f'{str(check.get("kind") or "check").capitalize()}: {text}',
        'doc_types': [],
        'required': check.get('severity') == 'blocker',
        'rule': {'kind': 'manual'},
    }


def convert_ready_made(data):
    """Convert the research file's brand rule sets into engine checklists.

    Every published document, eligibility check and cross-check appears in
    the converted checklist:
    - documents of a type the facts step classifies (READY_MADE_DOC_TYPES)
      become machine rules: salary slips 'monthly', bank statements 'period',
      ITR / KYC 'present' (min_count = FYs or documents);
    - anything else (address proof, property papers, co-applicant documents,
      eligibility checks, cross-checks without an engine check) becomes a
      manual REVIEW item. It is required, so it keeps the verdict NOT READY,
      when the research file marks it a required blocker;
    - severity 'warning' / 'info' items never block the verdict.
    """
    warnings = []
    checks_by_id = {
        x.get('check_id'): x
        for x in data.get('cross_checks') or []
        if isinstance(x, dict)
    }
    tolerance = (data.get('parameters') or {}).get('income_tolerance_pct') or {}
    tol = tolerance.get('value', engine.DEFAULT_TOLERANCE_PCT)
    checklists, used_ids = [], set()
    for cl in data['checklists']:
        rid = str(cl['checklist_id'])
        where = f'checklist {rid}'
        cl_type = str(cl.get('applicant_type') or 'any')
        item_ids, items = set(), []
        for doc in cl['documents']:
            if not isinstance(doc, dict):
                warnings.append(f'{where}: skipped a document that is not an object')
                continue
            item = _ready_made_doc(doc, cl_type, item_ids, warnings, where)
            if item:
                items.append(item)
        for check in cl.get('checks') or []:
            if isinstance(check, dict):
                items.append(_ready_made_check(check, item_ids))

        enabled = set()
        for xid in cl.get('cross_check_ids') or []:
            if xid in READY_MADE_CROSS_CHECKS:
                enabled.update(READY_MADE_CROSS_CHECKS[xid])
                continue
            x = checks_by_id.get(xid) or {}
            items.append(
                {
                    'id': _unique(slug(xid), item_ids),
                    'label': f'Cross-check: {x.get("name") or xid}',
                    'doc_types': [],
                    'required': x.get('severity') == 'blocker',
                    'rule': {'kind': 'manual'},
                }
            )
        types = {t for it in items for t in it['doc_types']}
        if {'salary_slip', 'form16_itr'} <= types:
            enabled.add('form16_vs_slip_gross')  # INFO only, never MISMATCH

        product = cl.get('product') or rid
        foir = _ready_made_foir(data, cl) if 'foir' in enabled else None
        checklists.append(
            {
                'id': _unique(slug(rid, 'checklist'), used_ids),
                'name': _ready_made_name(cl),
                'product': str(cl.get('product_key') or slug(product, 'product')),
                'applicant_type': cl_type,
                'description': (
                    f'{cl.get("brand_name") or cl.get("brand")} {product} '
                    f"({rid}), demo copy of the DSA's own document list. "
                    'Items the rules cannot verify are '
                    f'marked REVIEW. Source: {cl.get("source_url")}'
                ),
                'tolerance_pct': tol,
                'form16_tolerance_pct': engine.DEFAULT_FORM16_TOLERANCE_PCT,
                **({'foir': foir} if foir else {}),
                'items': items,
                'consistency_checks': [c for c in engine.CHECK_IDS if c in enabled],
            }
        )
    catalog = {
        'schema_version': 1,
        'default_checklist': checklists[0]['id'],
        'checklists': checklists,
    }
    errors = engine.validate_catalog(catalog)
    if errors:
        raise ValueError('converted catalog is invalid: ' + '; '.join(errors))
    return catalog, warnings


def merge(base, converted, replace=False):
    """Append converted checklists whose ids are new; keep base's default.

    With replace=True a converted checklist replaces the base checklist with
    the same id (in place), except the base default, which is always kept.
    """
    warnings = []
    ids = {c['id'] for c in base['checklists']}
    merged = {**base, 'checklists': list(base['checklists'])}
    for cl in converted['checklists']:
        if cl['id'] in ids:
            if replace and cl['id'] != base.get('default_checklist'):
                merged['checklists'] = [
                    cl if c['id'] == cl['id'] else c for c in merged['checklists']
                ]
                continue
            warnings.append(f"skipped {cl['id']!r}: already in the base catalog")
            continue
        ids.add(cl['id'])
        merged['checklists'].append(cl)
    errors = engine.validate_catalog(merged)
    if errors:
        raise ValueError('merged catalog is invalid: ' + '; '.join(errors))
    return merged, warnings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('input', help='ready-made checklists JSON')
    ap.add_argument('-o', '--output', help='write the catalog here (default: stdout)')
    ap.add_argument('--merge', metavar='BASE', help='append to this catalog')
    ap.add_argument(
        '--replace',
        action='store_true',
        help='with --merge: replace base checklists that have the same id',
    )
    args = ap.parse_args(argv)

    with open(args.input, encoding='utf-8') as fh:
        data = json.load(fh)
    try:
        catalog, warnings = convert(data)
        if args.merge:
            base = engine.load_catalog(args.merge)
            catalog, more = merge(base, catalog, replace=args.replace)
            warnings += more
    except ValueError as e:
        print(f'error: {e}', file=sys.stderr)
        return 1
    for w in warnings:
        print(f'warning: {w}', file=sys.stderr)

    text = json.dumps(catalog, indent=2, ensure_ascii=False) + '\n'
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
