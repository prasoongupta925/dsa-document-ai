"""The DSA's own reference lists, uploaded as CSV (POST .../eligibility/reference-data).

Kinds and columns (headers in any case, common aliases accepted: Bank, Pin Code, Branch Name,
Company Name ...):
- pincode_serviceability: lender, pincode[, serviceable]. A lender with rows here is
  serviceable at exactly the pincodes listed as serviceable (blank, yes, y, true, 1); a pincode
  listed as not serviceable (no, n, false, 0) or not listed is not.
- lender_branches: lender, branch, pincode[, address, city, district, state, ifsc]. A lender
  with rows here gets these branches instead of the public or SAMPLE ones (app/branches.py).
- company_categories: lender, company, category. The lender must be one of the policy file
  (app/data/lender_policies.json) and the category one of its categories; a company listed
  here has this category with that lender (StoredList.company_category).

The eligibility calculation, "Check Availability" and "Check Category" (app/routers/eligibility.py)
use the serviceability and company lists through CalculationLists / CalculationBook: a lender a list
has is answered by that list alone (a company missing from its company list is unlisted with it),
the others by the shipped SAMPLE lists.

The file is UTF-8 (with or without a BOM), UTF-16 or Windows-1252, separated by commas,
semicolons or tabs, at most MAX_UPLOAD_BYTES and MAX_ROWS rows. A file with any bad row is
refused with its problems (the first MAX_ERRORS), so a list is never half applied; identical
duplicate rows are dropped. An accepted list comes with notes on what to check: pincodes the
India Post directory does not know, and lenders the app does not know.

Storage (DynamoDB, deleted by TTL on expires_at, and with the project): one list per kind,
PK = PROJ#{project_id}, with
- SK = REFDATA#{kind}: upload_id, filename, row and chunk counts, lenders, uploaded_at and
  expires_at = upload time + the retention period (default 7 days);
- SK = REFDATA#{kind}#{upload_id}#{n:04d}: the parsed rows as compact JSON, at most
  CHUNK_BYTES each (a DynamoDB item holds 400 KB), same expires_at.
A new upload writes its chunks, then points the header at them, then deletes the previous
upload's chunks, so a reader never sees half a list. The lists hold lender, branch, pincode
and company names only: no applicant data.
"""

import csv
import datetime as dt
import io
import json
import re
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal

from boto3.dynamodb.conditions import Key

from app import branches, eligibility
from app.config import get_config
from app.ddb import get_table
from app.ddb.ask_usage import TTL_ATTRIBUTE

Kind = Literal["pincode_serviceability", "lender_branches", "company_categories"]
KINDS: tuple[Kind, ...] = ("pincode_serviceability", "lender_branches", "company_categories")
KIND_LABELS = {
    "pincode_serviceability": "Pincode serviceability",
    "lender_branches": "Lender branches",
    "company_categories": "Company categories",
}
KIND_DESCRIPTIONS = {
    "pincode_serviceability": "Where each lender lends: a lender listed here is serviceable only at the pincodes "
    "marked serviceable (a blank serviceable column means yes), also in Check availability and the eligibility "
    "calculation.",
    "lender_branches": "Your lenders' branches: a lender listed here shows these branches instead of the public "
    "or sample ones.",
    "company_categories": "Your lenders' company lists: the category (for example CAT A) a company has with each "
    "lender, also in Check category and the eligibility calculation; a company missing from a lender's list is "
    "unlisted with it.",
}
COLUMNS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "pincode_serviceability": (("lender", "pincode"), ("serviceable",)),
    "lender_branches": (("lender", "branch", "pincode"), ("address", "city", "district", "state", "ifsc")),
    "company_categories": (("lender", "company", "category"), ()),
}
HEADER_ALIASES = {
    "lender": ("lender", "lender name", "bank", "bank name", "nbfc", "financier"),
    "pincode": ("pincode", "pin code", "pin", "postal code", "zip", "zip code"),
    "serviceable": ("serviceable", "serviceability", "available", "availability", "status"),
    "branch": ("branch", "branch name", "name"),
    "address": ("address", "branch address"),
    "city": ("city", "centre", "center", "location", "town"),
    "district": ("district",),
    "state": ("state",),
    "ifsc": ("ifsc", "ifsc code"),
    "company": ("company", "company name", "employer", "employer name"),
    "category": ("category", "company category", "cat"),
}
MAX_LENGTHS = {"lender": 100, "branch": 200, "address": 300, "city": 100, "district": 100, "state": 100}

SK_PREFIX = "REFDATA#"
MAX_UPLOAD_BYTES = 4 * 1024 * 1024  # under Lambda's 6 MB request limit, also as multipart
MAX_ROWS = 100_000
MAX_ERRORS = 20
CHUNK_BYTES = 300_000
MAX_LISTED_LENDERS = 200  # names kept in the header item
_CACHE_SIZE = 16

_YES = frozenset({"", "yes", "y", "true", "1", "serviceable", "available", "active"})
_NO = frozenset(
    {"no", "n", "false", "0", "not serviceable", "non serviceable", "unserviceable", "not available", "inactive"}
)
_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_PINCODE = re.compile(r"^[1-9][0-9]{5}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class CsvError(ValueError):
    """The file cannot be used: `errors` says why (row numbers count the header as row 1)."""

    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.errors = errors or []


@dataclass
class ParsedList:
    kind: Kind
    rows: list[list]
    lenders: list[str]
    duplicates: int = 0
    # What to check in a list that was accepted (unknown pincodes, unknown lenders).
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ parsing
def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _header_key(name: str) -> str:
    return " ".join(re.sub(r"[_\-./]+", " ", str(name or "").casefold()).split())


def _columns(kind: Kind, header: list[str]) -> dict[str, int]:
    """column name -> index in the file; CsvError when a required column is missing."""
    required, optional = COLUMNS[kind]
    found: dict[str, int] = {}
    keys = [_header_key(h) for h in header]
    for column in (*required, *optional):
        for alias in HEADER_ALIASES[column]:
            if alias in keys and keys.index(alias) not in found.values():
                found[column] = keys.index(alias)
                break
    missing = [c for c in required if c not in found]
    if missing:
        raise CsvError(
            f"The file has no {', '.join(missing)} column: a {KIND_LABELS[kind].lower()} list needs "
            f"{', '.join(required)}" + (f" (optional: {', '.join(optional)})" if optional else ""),
            [f"Columns found: {', '.join(h.strip() for h in header if h.strip()) or 'none'}"],
        )
    return found


def _pincode(value: str) -> str | None:
    text = re.sub(r"\s", "", value)
    text = text[:-2] if text.endswith(".0") else text  # 400606.0 from a spreadsheet's number column
    return text if _PINCODE.match(text) else None


def _text(value: str, column: str) -> str:
    return " ".join(_CONTROL.sub(" ", value).split())[: MAX_LENGTHS.get(column, 200)]


def _category(lender: eligibility.LenderPolicy, value: str) -> str | None:
    wanted = _header_key(value).replace(" ", "")
    return next((c for c in lender.company_categories if _header_key(c).replace(" ", "") == wanted), None)


def parse_csv(kind: Kind, data: bytes) -> ParsedList:
    """The rows of an uploaded list, checked; CsvError lists what is wrong."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise CsvError(f"The file is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if data.startswith(b"PK\x03\x04"):
        raise CsvError("This is an Excel workbook, not a CSV file: save it as CSV (File > Save As > CSV UTF-8)")
    text = _decode(data)
    first_line, _, rest = text.partition("\n")
    if first_line.strip().casefold().startswith("sep=") and len(first_line.strip()) == 5:
        delimiter, text = first_line.strip()[4], rest  # Excel's "sep=;" line
    else:
        delimiter = max((",", ";", "\t"), key=first_line.count)
    try:
        return _parse_rows(kind, csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    except csv.Error as e:
        raise CsvError(f"The file is not a readable CSV ({e})") from e


def _parse_rows(kind: Kind, reader) -> ParsedList:
    header = next(reader, None)
    if not header or not any(h.strip() for h in header):
        raise CsvError("The file is empty")
    columns = _columns(kind, header)
    book = eligibility.load_policy_book()

    rows: list[list] = []
    errors: list[str] = []
    seen: dict[tuple, tuple[Any, int]] = {}
    lenders: dict[str, None] = {}
    duplicates = 0
    total_errors = 0

    def fail(line: int, message: str) -> None:
        nonlocal total_errors
        total_errors += 1
        if len(errors) < MAX_ERRORS:
            errors.append(f"Row {line}: {message}")

    for line, record in enumerate(reader, start=2):
        if not any(cell.strip() for cell in record):
            continue
        if len(rows) >= MAX_ROWS:
            raise CsvError(f"The file has over {MAX_ROWS:,} rows")
        value = {c: _text(record[i], c) if i < len(record) else "" for c, i in columns.items()}
        lender = value["lender"]
        if not lender:
            fail(line, "the lender is empty")
            continue
        pincode = None
        if "pincode" in columns:
            pincode = _pincode(value["pincode"])
            if pincode is None:
                fail(line, f"the pincode {value['pincode']!r} is not 6 digits")
                continue

        if kind == "pincode_serviceability":
            answer = value.get("serviceable", "").casefold()
            if answer not in _YES and answer not in _NO:
                fail(line, f"serviceable is {value['serviceable']!r}: use yes or no")
                continue
            key, row = (branches.match_key(lender), pincode), [lender, pincode, int(answer in _YES)]
            payload = row[2]
        elif kind == "lender_branches":
            if not value["branch"]:
                fail(line, "the branch name is empty")
                continue
            ifsc = value.get("ifsc", "").replace(" ", "").upper()
            if ifsc and not _IFSC.match(ifsc):
                fail(line, f"the IFSC {value['ifsc']!r} is not 11 characters like HDFC0001234")
                continue
            row = [
                lender,
                value["branch"],
                pincode,
                value.get("address") or None,
                value.get("city") or None,
                value.get("district") or None,
                value.get("state") or None,
                ifsc or None,
            ]
            key, payload = tuple(row), None
        else:
            policy = book.lender(lender) or book.lender(branches.resolve_lender(lender).id)
            if policy is None:
                fail(line, f"{lender!r} is not a lender of the policy file ({', '.join(x.name for x in book.lenders)})")
                continue
            category = _category(policy, value["category"])
            if category is None:
                fail(
                    line,
                    f"{policy.name} has no category {value['category']!r} "
                    f"(its categories: {', '.join(policy.company_categories)})",
                )
                continue
            company_key = eligibility.company_key(value["company"])
            if not company_key:
                fail(line, "the company is empty")
                continue
            key, row, payload = (policy.id, company_key), [policy.id, value["company"], category], category
            lender = policy.name

        if key in seen:
            earlier, earlier_line = seen[key]
            if earlier != payload:
                fail(line, f"it contradicts row {earlier_line} ({lender}{', ' + pincode if pincode else ''})")
            else:
                duplicates += 1
            continue
        seen[key] = (payload, line)
        lenders[lender] = None
        rows.append(row)

    if total_errors:
        more = f" (the first {MAX_ERRORS} are listed)" if total_errors > MAX_ERRORS else ""
        raise CsvError(f"The file has {total_errors} problem{'s' if total_errors > 1 else ''}{more}", errors)
    if not rows:
        raise CsvError("The file has a header but no rows")
    return ParsedList(kind, rows, list(lenders), duplicates, _notes(kind, rows, list(lenders)))


def _notes(kind: Kind, rows: list[list], lenders: list[str]) -> list[str]:
    """What to check in an accepted list: pincodes the India Post directory does not know (a
    typo, or a branch that cannot be placed) and lenders the app does not know (a typo, or a
    lender used only when asked for by name)."""
    notes = []
    if kind != "company_categories":
        directory = branches.load_directory()
        pincodes = {row[1 if kind == "pincode_serviceability" else 2] for row in rows}
        unknown = sorted(p for p in pincodes if p not in directory)
        if unknown:
            examples = ", ".join(unknown[:5]) + (" ..." if len(unknown) > 5 else "")
            effect = (
                "those branches cannot be placed, so they are never shown as nearest"
                if kind == "lender_branches"
                else "check for typos"
            )
            notes.append(
                f"{len(unknown)} pincode{'s are' if len(unknown) > 1 else ' is'} not in the India Post directory "
                f"({examples}): {effect}"
            )
        others = [name for name in lenders if branches.resolve_lender(name).id is None]
        if others:
            notes.append(
                f"Not lenders of the eligibility policies, so used only when asked for by name: "
                f"{', '.join(others[:10])}{' ...' if len(others) > 10 else ''}"
            )
    return notes


# ------------------------------------------------------------------ stored lists
@dataclass
class StoredList:
    kind: Kind
    upload_id: str
    filename: str | None
    uploaded_at: str
    expires_at: int
    rows: list[list] = field(repr=False)
    lenders: list[str]
    _index: Any = field(default=None, repr=False)

    def source(self) -> dict[str, Any]:
        """The `sources` entry of an answer that used this list."""
        expires = dt.datetime.fromtimestamp(self.expires_at, dt.UTC).strftime("%d %b %Y")
        try:
            uploaded = dt.datetime.fromisoformat(self.uploaded_at).strftime("%d %b %Y")
        except ValueError:
            uploaded = self.uploaded_at
        name = f"Your {KIND_LABELS[self.kind].lower()} list"
        detail = f"{self.filename}, uploaded {uploaded}" if self.filename else f"uploaded {uploaded}"
        return {
            "name": f"{name} ({detail})",
            "licence": f"Your own data, deleted on {expires}",
            "url": None,
        }

    def index(self) -> Any:
        """The rows arranged for lookups (built once per upload)."""
        if self._index is None:
            if self.kind == "pincode_serviceability":
                index: dict[str, dict[str, bool]] = {}
                for lender, pincode, yes in self.rows:
                    index.setdefault(branches.match_key(lender), {})[pincode] = bool(yes)
            elif self.kind == "lender_branches":
                grouped: dict[str, dict[str, list]] = {}
                for lender, name, pincode, address, city, district, state, ifsc in self.rows:
                    branch = branches.Branch(name, pincode, address, city, ifsc, district, state)
                    grouped.setdefault(branches.match_key(lender), {}).setdefault(pincode, []).append(branch)
                index = {k: {p: tuple(b) for p, b in by_pincode.items()} for k, by_pincode in grouped.items()}
            else:
                index = {}
                for lender_id, company, category in self.rows:
                    index.setdefault(lender_id, {})[eligibility.company_key(company)] = category
            self._index = index
        return self._index

    def company_category(self, lender_id: str, company: str | None) -> str | None:
        """The category this list gives `company` with the lender; None when it does not list it."""
        if self.kind != "company_categories":
            raise ValueError("not a company_categories list")
        return self.index().get(lender_id, {}).get(eligibility.company_key(company or ""))

    def serviceable(self, lender: str, pincode: str) -> bool | None:
        """True / False when this list covers the lender, None when it does not."""
        if self.kind != "pincode_serviceability":
            raise ValueError("not a pincode_serviceability list")
        listed = self.index().get(branches.match_key(lender))
        return None if listed is None else listed.get(pincode, False)


_cache: OrderedDict[tuple[str, str, str], StoredList] = OrderedDict()
_cache_lock = threading.Lock()


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _header_key_of(project_id: str, kind: str) -> dict[str, str]:
    return {"PK": f"PROJ#{project_id}", "SK": f"{SK_PREFIX}{kind}"}


def _chunk_prefix(kind: str, upload_id: str) -> str:
    return f"{SK_PREFIX}{kind}#{upload_id}#"


def _iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.UTC).isoformat(timespec="seconds")


def _chunks(rows: list[list]) -> list[str]:
    """The rows as JSON arrays of at most CHUNK_BYTES each."""
    out, current, size = [], [], 2
    for row in rows:
        encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        length = len(encoded.encode("utf-8")) + 1
        if current and size + length > CHUNK_BYTES:
            out.append("[" + ",".join(current) + "]")
            current, size = [], 2
        current.append(encoded)
        size += length
    out.append("[" + ",".join(current) + "]")
    return out


def _header(project_id: str, kind: str, now: dt.datetime) -> dict[str, Any] | None:
    """The kind's live header item, or None (also when past expires_at but not yet removed by TTL)."""
    item = get_table().get_item(Key=_header_key_of(project_id, kind), ConsistentRead=True).get("Item")
    if not item or int(item.get(TTL_ATTRIBUTE) or 0) <= int(now.timestamp()):
        return None
    return item


def _delete_chunks(project_id: str, kind: str, upload_id: str) -> None:
    table = get_table()
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(f"PROJ#{project_id}")
        & Key("SK").begins_with(_chunk_prefix(kind, upload_id)),
        "ProjectionExpression": "PK, SK",
    }
    while True:
        page = table.query(**kwargs)
        for item in page.get("Items", []):
            table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
        if not page.get("LastEvaluatedKey"):
            return
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def save(project_id: str, parsed: ParsedList, filename: str | None, now: dt.datetime) -> StoredList:
    """Store `parsed` as the project's list of its kind, replacing the previous one."""
    table = get_table()
    previous = _header(project_id, parsed.kind, now)
    upload_id = uuid.uuid4().hex
    expires_at = int(now.timestamp()) + get_config().retention_days * 86400
    chunks = _chunks(parsed.rows)
    for n, chunk in enumerate(chunks):
        table.put_item(
            Item={
                "PK": f"PROJ#{project_id}",
                "SK": f"{_chunk_prefix(parsed.kind, upload_id)}{n:04d}",
                "rows": chunk,
                TTL_ATTRIBUTE: expires_at,
            }
        )
    stored = StoredList(
        kind=parsed.kind,
        upload_id=upload_id,
        filename=filename,
        uploaded_at=_iso(now),
        expires_at=expires_at,
        rows=parsed.rows,
        lenders=parsed.lenders,
    )
    table.put_item(
        Item={
            **_header_key_of(project_id, parsed.kind),
            "kind": parsed.kind,
            "upload_id": upload_id,
            "filename": filename,
            "row_count": len(parsed.rows),
            "chunks": len(chunks),
            "lenders": parsed.lenders[:MAX_LISTED_LENDERS],
            "uploaded_at": stored.uploaded_at,
            TTL_ATTRIBUTE: expires_at,
        }
    )
    if previous and previous.get("upload_id"):
        _delete_chunks(project_id, parsed.kind, str(previous["upload_id"]))
    with _cache_lock:
        _cache[(project_id, parsed.kind, upload_id)] = stored
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return stored


def load(project_id: str, kind: Kind, now: dt.datetime) -> StoredList | None:
    """The project's live list of `kind`, or None."""
    header = _header(project_id, kind, now)
    if header is None:
        return None
    upload_id = str(header["upload_id"])
    cache_key = (project_id, kind, upload_id)
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached is not None:
            _cache.move_to_end(cache_key)
            return cached
    table = get_table()
    rows: list[list] = []
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(f"PROJ#{project_id}")
        & Key("SK").begins_with(_chunk_prefix(kind, upload_id)),
        "ConsistentRead": True,
    }
    while True:
        page = table.query(**kwargs)
        for item in page.get("Items", []):
            rows.extend(json.loads(item["rows"]))
        if not page.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    stored = StoredList(
        kind=kind,
        upload_id=upload_id,
        filename=header.get("filename"),
        uploaded_at=str(header.get("uploaded_at") or ""),
        expires_at=int(header[TTL_ATTRIBUTE]),
        rows=rows,
        lenders=[str(x) for x in header.get("lenders") or []],
    )
    with _cache_lock:
        _cache[cache_key] = stored
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return stored


def delete(project_id: str, kind: Kind, now: dt.datetime) -> bool:
    """Remove the project's list of `kind`; False when there was none."""
    item = get_table().get_item(Key=_header_key_of(project_id, kind), ConsistentRead=True).get("Item")
    if not item:
        return False
    get_table().delete_item(Key=_header_key_of(project_id, kind))
    if item.get("upload_id"):
        _delete_chunks(project_id, kind, str(item["upload_id"]))
    return int(item.get(TTL_ATTRIBUTE) or 0) > int(now.timestamp())


def status(project_id: str, now: dt.datetime) -> dict[Kind, dict[str, Any] | None]:
    """Each kind's live header (filename, row_count, lenders, uploaded_at, expires_at), or None."""
    return {kind: _header(project_id, kind, now) for kind in KINDS}


def own_lists(project_id: str, now: dt.datetime) -> branches.OwnLists:
    """The project's serviceability and branch lists, as app.branches.find() takes them."""
    serviceability = load(project_id, "pincode_serviceability", now)
    branch_list = load(project_id, "lender_branches", now)
    return branches.OwnLists(
        serviceable=serviceability.index() if serviceability else {},
        branches=branch_list.index() if branch_list else {},
        serviceable_source=serviceability.source() if serviceability else None,
        branches_source=branch_list.source() if branch_list else None,
    )


# ------------------------------------------------------------------ for the eligibility calculation
class CalculationLists:
    """The DSA's uploaded serviceability and company lists as the eligibility calculation, "Check
    Availability" and "Check Category" use them: a lender a list has is answered by that list alone
    (serviceable at exactly its serviceable pincodes; exactly its companies listed, each with its
    category), any other lender by the shipped SAMPLE lists. `used` collects the lenders a list
    answered for (by kind), for the result's notes."""

    def __init__(self, serviceability: StoredList | None, companies: StoredList | None):
        self.serviceability = serviceability
        self.companies = companies
        self.used: dict[Kind, set[str]] = {"pincode_serviceability": set(), "company_categories": set()}

    def serviceable(self, lender: eligibility.LenderPolicy, pincode: str) -> bool | None:
        """Whether the uploaded list has `lender` serving `pincode`; None when it does not cover it."""
        own = self.serviceability.serviceable(lender.name, pincode) if self.serviceability else None
        if own is not None:
            self.used["pincode_serviceability"].add(lender.name)
        return own

    def covers_companies(self, lender: eligibility.LenderPolicy) -> bool:
        return self.companies is not None and lender.id in self.companies.index()

    def company_category(self, lender: eligibility.LenderPolicy, names: list[str]) -> str | None:
        """The category the uploaded list gives the company (any of its `names`) with a lender it
        covers (covers_companies); None: not on the lender's list, so unlisted with it."""
        listed = self.companies.index().get(lender.id, {}) if self.companies else {}
        self.used["company_categories"].add(lender.name)
        return next((listed[k] for k in map(eligibility.company_key, names) if k and k in listed), None)

    def notes(self) -> list[str]:
        """What the calculation took from the uploaded lists."""
        out = []
        for kind, stored in (("pincode_serviceability", self.serviceability), ("company_categories", self.companies)):
            if stored and self.used[kind]:
                what = "Serviceability" if kind == "pincode_serviceability" else "Company category"
                source = stored.source()["name"]
                out.append(f"{what} of {', '.join(sorted(self.used[kind]))} from {source[0].lower()}{source[1:]}")
        return out


def calculation_lists(project_id: str, now: dt.datetime) -> CalculationLists | None:
    """The project's uploaded serviceability and company lists; None when neither is uploaded."""
    serviceability = load(project_id, "pincode_serviceability", now)
    companies = load(project_id, "company_categories", now)
    if serviceability is None and companies is None:
        return None
    return CalculationLists(serviceability, companies)


class CalculationBook:
    """The policy book with the DSA's lists first (CalculationLists), as eligibility.calculate takes
    a book: serviceable() and find_company() answer from the lists; everything else is the book's."""

    def __init__(self, book: eligibility.PolicyBook, lists: CalculationLists):
        self._book = book
        self.lists = lists

    def __getattr__(self, name: str) -> Any:
        return getattr(self._book, name)

    def serviceable(self, lender_id: str, pincode: str) -> bool:
        lender = self._book.lender(lender_id)
        own = self.lists.serviceable(lender, pincode) if lender else None
        return self._book.serviceable(lender_id, pincode) if own is None else own

    def find_company(self, name: str | None) -> eligibility.Company | None:
        company = self._book.find_company(name)
        covered = [lender for lender in self._book.lenders if self.lists.covers_companies(lender)]
        if not covered or not (name or "").strip():
            return company
        names = [name, *([company.name, *company.aliases] if company else [])]
        categories = dict(company.categories) if company else {}
        for lender in covered:
            category = self.lists.company_category(lender, names)
            if category is None:
                categories.pop(lender.id, None)
            else:
                categories[lender.id] = category
        if company is not None:
            return company.model_copy(update={"categories": categories})
        return eligibility.Company(name=" ".join(name.split()), categories=categories) if categories else None


def company_category(project_id: str, lender_id: str, company: str | None, now: dt.datetime) -> str | None:
    """The category the DSA's uploaded company list gives `company` with the lender, else None
    (then the shipped SAMPLE list applies)."""
    stored = load(project_id, "company_categories", now)
    return stored.company_category(lender_id, company) if stored else None


def serviceable(project_id: str, lender: str, pincode: str, now: dt.datetime) -> bool | None:
    """Whether the DSA's uploaded serviceability list has the lender serving `pincode`; None when
    the list does not cover the lender (then the shipped SAMPLE list applies)."""
    stored = load(project_id, "pincode_serviceability", now)
    return stored.serviceable(lender, pincode) if stored else None
