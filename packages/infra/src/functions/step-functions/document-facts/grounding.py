"""Grounding of model-extracted fields against the document's machine text (pure, no AWS).

Port of Plan B lean-file-check extract._lev / inr / ground_fields: the model can
mis-read single characters of IDs (PAN, mobile) or drop/add a digit of an amount
(salaries, salary credits, recurring debits, declared EMIs, other income, credit
score, tradeline amounts). When the document has a machine text layer (PDF
text, OCR, BDA, text file), the exact characters printed there win; a PAN or a
mobile the model left out is filled when the text prints exactly one.

field_pages tells on which page each value is printed, so the CIBIL page can
show "from <file>, page N".
"""
import calendar
import re

from tool_schema import (
    DOC_TYPE_FIELDS,
    ENQUIRY_WINDOWS,
    NUMERIC_FIELDS,
    TRADELINE_AMOUNT_FIELDS,
)

PAN_RE = re.compile(r'\b[A-Z]{5}\d{4}[A-Z]\b')
AADHAAR_MASKED_RE = re.compile(r'XXXX[- ]XXXX[- ](\d{4})')
NUMBER_RE = re.compile(r'\d[\d,]*(?:\.\d+)?')
# An Indian mobile number as printed: '+91 90000 00101', '9000000101', '090000-00101'.
MOBILE_RE = re.compile(r'(?<!\d)(?:\+?91[\s-]?|0)?([6-9]\d{4}[\s-]?\d{5})(?!\d)')

# Below this many characters the text is not a usable text layer.
MIN_TEXT_CHARS = 50
# A mobile number is corrected only to a printed one within this edit distance.
MAX_MOBILE_EDITS = 2

# Digit-string fields reported unverified when the text layer does not print them.
PRINTED_ID_FIELDS = ('mobile', 'current_pincode', 'permanent_pincode')


def lev(a: str, b: str) -> int:
    """Levenshtein edit distance."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def inr(v) -> str:
    """Indian digit grouping: 600000 -> ₹6,00,000."""
    if v is None:
        return '–'
    neg, v = v < 0, abs(v)
    whole, frac = divmod(round(v * 100), 100)
    s = str(int(whole))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = ','.join(re.findall(r'\d{1,2}', head[::-1]))[::-1]  # groups of 2 from the right
        s = f'{head},{tail}'
    return f"{'-' if neg else ''}₹{s}" + (f'.{frac:02d}' if frac else '')


def _has_text_layer(text) -> bool:
    return bool(text) and len(text.strip()) >= MIN_TEXT_CHARS


def printed_numbers(text: str) -> set:
    """Every number printed in the text, as float ('11,40,000' -> 1140000.0)."""
    return {float(x.replace(',', '')) for x in NUMBER_RE.findall(text or '')}


def printed_mobiles(text: str) -> set:
    """Every mobile number printed in the text, as 10 digits."""
    return {re.sub(r'\D', '', m) for m in MOBILE_RE.findall(text or '')}


def _digits_printed(digits: str, text: str) -> bool:
    """'411045' is printed as a whole number ('411045' / '411 045'), not inside a longer one."""
    pattern = r'(?<!\d)' + r'[\s-]?'.join(map(re.escape, digits)) + r'(?!\d)'
    return bool(re.search(pattern, text or ''))


def ground_fields(fields: dict, text: str) -> tuple:
    """Correct IDs and amounts from the machine text. Returns (fields, audit notes).

    A no-op when the text has fewer than 50 characters.
    """
    notes = []
    if not _has_text_layer(text):
        return fields, notes
    f = dict(fields)
    pans = sorted(set(PAN_RE.findall(text)))
    if f.get('pan') and pans and f['pan'] not in pans:
        best = min(pans, key=lambda p: lev(p, f['pan']))
        notes.append(f"PAN corrected from text layer: model read '{f['pan']}', document text says '{best}'")
        f['pan'] = best
    elif not f.get('pan') and len(pans) == 1 and f.get('doc_type') not in ('bank_statement', 'rent_agreement'):
        # a rent agreement may print only the tenant's PAN; the record is the landlord's
        notes.append(f"PAN filled from text layer: '{pans[0]}'")
        f['pan'] = pans[0]
    aad = sorted(set(AADHAAR_MASKED_RE.findall(text)))
    if f.get('masked_aadhaar_last4') and aad and f['masked_aadhaar_last4'] not in aad:
        notes.append(f"Aadhaar last-4 corrected from text layer: '{f['masked_aadhaar_last4']}' -> '{aad[0]}'")
        f['masked_aadhaar_last4'] = aad[0]
    mobiles = printed_mobiles(text)
    if f.get('mobile') and mobiles and f['mobile'] not in mobiles:
        best = min(sorted(mobiles), key=lambda m: lev(m, f['mobile']))
        if lev(best, f['mobile']) <= MAX_MOBILE_EDITS:
            notes.append(f"Mobile corrected from text layer: model read '{f['mobile']}', document text says '{best}'")
            f['mobile'] = best
    elif not f.get('mobile') and len(mobiles) == 1 and f.get('doc_type') in DOC_TYPE_FIELDS['mobile']:
        # omitted, or copied with a digit too many ('90000 00103' as 90000000103) and dropped by the normaliser
        mobile = next(iter(mobiles))
        notes.append(f"Mobile filled from text layer: '{mobile}'")
        f['mobile'] = mobile
    # amounts: Indian grouping (11,40,000) sometimes gets a digit dropped/added by the model
    printed = printed_numbers(text)

    def fix(v, label):
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v in printed:
            return v
        for c in (v / 10, v * 10, v / 100, v * 100):
            if c in printed:
                notes.append(f'{label} corrected from text layer: model read {inr(v)}, document text says {inr(c)}')
                return c
        return v

    for k in NUMERIC_FIELDS:
        f[k] = fix(f.get(k), k)
    f['salary_credits'] = [{**c, 'amount': fix(c.get('amount'), f"salary credit {c.get('date')}")}
                           for c in f.get('salary_credits') or [] if isinstance(c, dict)]
    if 'recurring_debits' in f:
        f['recurring_debits'] = [{**r, 'amount': fix(r.get('amount'), f"debit {r.get('date')}")}
                                 for r in f.get('recurring_debits') or [] if isinstance(r, dict)]
    if 'declared_existing_emis' in f:
        f['declared_existing_emis'] = [
            {**e, 'amount': fix(e.get('amount'), f"declared EMI ({e.get('lender') or 'lender not named'})")}
            for e in f.get('declared_existing_emis') or [] if isinstance(e, dict)]
    if 'tradelines' in f:
        f['tradelines'] = [
            {**t, **{k: fix(t.get(k), f"tradeline ({t.get('lender') or 'lender not named'}) {k}")
                     for k in TRADELINE_AMOUNT_FIELDS if k in t}}
            for t in f.get('tradelines') or [] if isinstance(t, dict)]
    return f, notes


def unverified_numbers(fields: dict, text: str) -> list:
    """Names of numeric fields whose value is not among the printed numbers.

    List amounts are reported as 'salary_credits[i].amount',
    'recurring_debits[i].amount', 'declared_existing_emis[i].amount' and
    'tradelines[i].<amount>'; the mobile and pincodes too when they are not
    printed. The file-check engine flags obligations built on them as NEEDS
    REVIEW. Returns [] when the text has fewer than 50 characters (nothing to
    verify against).
    """
    if not _has_text_layer(text):
        return []
    printed = printed_numbers(text)
    out = []
    for k in NUMERIC_FIELDS:
        v = fields.get(k)
        if v is not None and v not in printed:
            out.append(k)
    for k in PRINTED_ID_FIELDS:
        v = fields.get(k)
        if v and not (v in printed_mobiles(text) if k == 'mobile' else _digits_printed(v, text)):
            out.append(k)
    for key in ('salary_credits', 'recurring_debits', 'declared_existing_emis'):
        for i, c in enumerate(fields.get(key) or []):
            v = c.get('amount') if isinstance(c, dict) else None
            if v is not None and v not in printed:
                out.append(f'{key}[{i}].amount')
    for i, t in enumerate(fields.get('tradelines') or []):
        for k in TRADELINE_AMOUNT_FIELDS:
            v = t.get(k) if isinstance(t, dict) else None
            if v is not None and v not in printed:
                out.append(f'tradelines[{i}].{k}')
    return out


# --------------------------------------------------------------------------- #
# field_pages: on which page each value is printed
# --------------------------------------------------------------------------- #
# Values the normaliser maps to an id (or counts) are found by their label.
_LABEL_HINTS = {
    'house_ownership': ('ownership', 'residence', 'accommodation'),
    'employment_type': ('employment', 'occupation', 'constitution'),
    'registration': ('notar', 'regist'),
    'bureau': ('cibil', 'transunion', 'experian', 'equifax', 'crif', 'bureau'),
    **dict.fromkeys(ENQUIRY_WINDOWS, ('enquir', 'inquir')),
    'credit_score': ('score',),
}
_DATE_FIELDS = ('statement_from', 'statement_to', 'dob', 'report_date', 'agreement_from', 'agreement_to')
_ITEM_PARTS = {
    'salary_credits': ('date', 'amount', 'narration'),
    'recurring_debits': ('date', 'amount', 'narration'),
    'declared_existing_emis': ('lender', 'amount'),
    'enquiries': ('date', 'lender'),
    'tradelines': ('lender', 'account_last4', *TRADELINE_AMOUNT_FIELDS),
}
_SKIP_FIELDS = ('doc_type',)
# Rows the CIBIL page lists one by one: each also carries its own page.
PAGED_ROWS = ('enquiries', 'tradelines')


def _squash(text) -> str:
    """Letters and digits only, lower case: 'Flat B-702,\\nSample Heights' -> 'flatb702sampleheights'."""
    return re.sub(r'[^a-z0-9]', '', str(text or '').lower())


class _Page:
    def __init__(self, number: int, text: str):
        self.number = number
        self.text = text or ''
        self.lower = self.text.lower()
        self.squashed = _squash(self.text)
        self.words = set(re.findall(r'[a-z0-9]+', self.lower))
        self.numbers = printed_numbers(self.text)


def _date_forms(iso: str) -> list:
    """'1992-02-14' -> its usual printed forms, squashed: '14021992', '14feb1992', '19920214', ..."""
    m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', str(iso or ''))
    if not m:
        return []
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    if not 1 <= mo <= 12:
        return []
    abbr, full = calendar.month_abbr[mo].lower(), calendar.month_name[mo].lower()
    return [f'{d:02d}{mo:02d}{y}', f'{d:02d}{abbr}{y}', f'{d}{abbr}{y}', f'{d:02d}{full}{y}', f'{d}{full}{y}',
            f'{y}{mo:02d}{d:02d}', f'{abbr}{d:02d}{y}', f'{full}{d}{y}']


def _month_forms(ym: str) -> list:
    m = re.fullmatch(r'(\d{4})-(\d{2})', str(ym or ''))
    if not m or not 1 <= int(m.group(2)) <= 12:
        return []
    y, mo = m.group(1), int(m.group(2))
    return [f'{calendar.month_name[mo].lower()}{y}', f'{calendar.month_abbr[mo].lower()}{y}', f'{mo:02d}{y}']


def _string_on(value: str, page: _Page) -> bool:
    """The string is printed (separators and case ignored), or 3 in 4 of its words are on the page."""
    squashed = _squash(value)
    if len(squashed) >= 3 and squashed in page.squashed:
        return True
    words = [w for w in re.findall(r'[a-z0-9]+', str(value).lower()) if len(w) > 1]
    return len(words) >= 2 and sum(w in page.words for w in words) >= 0.75 * len(words)


def _on_page(field: str, value, page: _Page) -> bool:
    if value is None or value == '' or value == []:
        return False
    if field in _LABEL_HINTS:
        found = any(h in page.lower for h in _LABEL_HINTS[field])
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            found = found and float(value) in page.numbers
        return found
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return float(value) in page.numbers
    if field in _DATE_FIELDS or field in ('date', 'open_date', 'last_payment_date'):
        return any(form in page.squashed for form in _date_forms(value))
    if field == 'month':
        return any(form in page.squashed for form in _month_forms(value))
    if field == 'financial_year':  # '2025-26' printed as 'FY 2025-26' or '2025-2026'
        m = re.fullmatch(r'(\d{4})-\d{2}', str(value))
        forms = [_squash(value)] + ([f'{m.group(1)}{int(m.group(1)) + 1}'] if m else [])
        return any(form in page.squashed for form in forms)
    if field == 'mobile':
        return value in printed_mobiles(page.text)
    if field in ('current_pincode', 'permanent_pincode', 'masked_aadhaar_last4'):
        return _digits_printed(value, page.text)
    if field == 'account_last4':  # the end of the printed account number
        return bool(re.search(re.escape(str(value)) + r'(?![0-9A-Z])', page.text.upper()))
    if field == 'pan':
        return str(value).upper() in page.text.upper()
    return _string_on(str(value), page)


def _item_page(key: str, item: dict, pages: list):
    """The page that prints most identifying parts of a list item (first page on ties); None if none."""
    best, best_score = None, 0
    for page in pages:
        score = sum(1 for part in _ITEM_PARTS[key] if _on_page(part, item.get(part), page))
        if score > best_score:
            best, best_score = page.number, score
    return best


def field_pages(fields: dict, pages: list) -> dict:
    """{field: page number} of every value of `fields` found in the pages' machine text.

    `pages` is [(page number, machine text)] (extractor.page_texts). List items
    are keyed 'tradelines[0]', 'recurring_debits[3]', ... and the list itself
    by the first page of its items; a scalar is on the first page that prints
    it; an item on the page that prints most of its parts (date, amount,
    lender, ...). On a one-page document every value is on that page, text
    layer or not. Values printed nowhere are left out.
    """
    pages = [_Page(n, t) for n, t in pages or []]
    single = pages[0].number if len(pages) == 1 else None
    fields = {k: v for k, v in (fields or {}).items() if k not in _SKIP_FIELDS and v is not None and v != ''}
    lists, items = {}, {}
    for field, value in fields.items():
        if field in _ITEM_PARTS and isinstance(value, list):
            for i, item in enumerate(value):
                page = single or (_item_page(field, item, pages) if isinstance(item, dict) else None)
                if page:
                    items[f'{field}[{i}]'] = page
                    lists[field] = min(page, lists.get(field, page))
    out = {}
    for field, value in fields.items():
        if isinstance(value, (list, dict)):
            continue
        page = single or next((p.number for p in pages if _on_page(field, value, p)), None)
        if page is None and field in ENQUIRY_WINDOWS:
            # a count computed from the listed enquiries: the page of the list
            page = lists.get('enquiries') or next(
                (p.number for p in pages if any(h in p.lower for h in _LABEL_HINTS[field])), None)
        if page:
            out[field] = page
    return {**out, **lists, **items}


def with_row_pages(fields: dict, pages: dict) -> dict:
    """`fields` with 'page' (from field_pages; None when not found) on every enquiries and tradelines row."""
    f = dict(fields)
    for key in PAGED_ROWS:
        if key in f:
            f[key] = [{**row, 'page': pages.get(f'{key}[{i}]')} if isinstance(row, dict) else row
                      for i, row in enumerate(f.get(key) or [])]
    return f
