#!/usr/bin/env python3
"""Builds the compact public data of app/branches.py (app/data/branches, see its README.md).

Inputs, both open data (licences in app/data/branches/README.md):
- India Post's All India Pincode Directory with latitude and longitude, published on
  data.gov.in under the Government Open Data License - India: one row per post office.
- IFSC.csv of a razorpay/ifsc release: RBI's list of bank branches (IFSC), public domain.

Outputs (gzip JSON, byte-for-byte the same for the same inputs):
- pincode_directory.json.gz: every pincode's delivery office, district, state and centre.
  Some offices have wrong coordinates (a typo, a copy of the head office's, the sea, or
  degrees-minutes-seconds written as a decimal: 22.3434 for 22 deg 34' 34"), and whole
  postal divisions gave all their offices one point (a town's, or a typo: 12 Pune offices
  sit 55 km west of Pune, 9 Mumbai ones at Malabar Hill), so a point that offices of
  SHARED_POINT_PINCODES or more pincodes share is nobody's own: it never makes a neighbour
  centre and is used only for a pincode with no point of its own. The centre is the median
  of its offices' own points that lie near the neighbouring pincodes (same first four
  digits, else three: the median of their offices' own unambiguous points, within
  max(MIN_RADIUS_KM, 2 x their median distance to it)), each office read the way that puts
  it nearest them, and moved to its head and sub offices when they are within TOWN_KM of
  that median, or when the points do not agree at all (the town, where the branches are).
  The district's offices are the last neighbours tried (an island district shares its
  first digits with the mainland). Else 3+ offices that agree, if not too far. Else, with
  approx = 1: the shared points that agree with the nearest neighbours, else the
  district's centre.
- bank_branches.json.gz: the own branches (IFSC XXXX0 + six digits) of HDFC Bank, ICICI
  Bank and Axis Bank with the pincode parsed from the published address. A branch whose
  address has no pincode the directory knows cannot be placed and is left out, and so are
  back-office units (service branches, card operations, treasury, currency chests ...).
  Names, cities and addresses are re-cased for display; otherwise as published.

Usage (from packages/backend; downloads the inputs that are not given):
    uv run python scripts/build_branch_data.py [--pincode-csv FILE] [--ifsc-csv FILE]
"""

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import re
import statistics
import sys
import tempfile
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

PINCODE_URL = "https://data.gov.in/files/ogdpv2dms/s3fs-public/dataurl03122020/pincode.csv"
IFSC_RELEASE = "v2.0.62"
IFSC_URL = f"https://github.com/razorpay/ifsc/releases/download/{IFSC_RELEASE}/IFSC.csv"
OUT_DIR = Path(__file__).resolve().parent.parent / "app" / "data" / "branches"
DIRECTORY_FILE = "pincode_directory.json.gz"
BRANCHES_FILE = "bank_branches.json.gz"

# lender id (app/data/lender_policies.json) -> the bank's name in the RBI list and its IFSC prefix
BANKS = {
    "hdfc_bank": ("HDFC Bank", "HDFC"),
    "icici_bank": ("ICICI Bank", "ICIC"),
    "axis_bank": ("Axis Bank", "UTIB"),
}
# A pincode's offices lie near its neighbours (same first digits): at least this close,
# more in a spread-out rural area; a group of fewer points borrows its 3-digit group's.
MIN_RADIUS_KM = 25
MIN_GROUP_POINTS = 5
# Else 3+ offices within CLUSTER_KM of each other's median are trusted up to this far away.
CLUSTER_KM = 15
MAX_FROM_GROUP_KM = 150
# The head and sub offices within this of the median point mark the town.
TOWN_KM = 10
# A point (as written) that the offices of this many pincodes share is a placeholder.
SHARED_POINT_PINCODES = 3
COORD_DIGITS = 4  # about 11 m
MAX_ADDRESS = 300

_PINCODE = re.compile(r"^[1-9][0-9]{5}$")
# dd.mmss with zeros after: what a degrees-minutes-seconds value written as a decimal looks like.
_DMS = re.compile(r"^(\d{1,2})\.(\d{2})(\d{2})?0*$")
# Six digits, optionally split 3 + 3 by a space or hyphen ("400 001", "401-202").
_PINCODE_IN_TEXT = re.compile(r"(?<!\d)([1-9]\d{2})[ -]?(\d{3})(?!\d)")
_OFFICE_SUFFIX = re.compile(r"\s+[BSH]\.?\s?O\.?(?=\s*(\(|$))", re.IGNORECASE)
# Back-office units, not branches a customer walks into.
_NOT_A_BRANCH = re.compile(
    r"\b(SERVICE (BR|BRANCH|CENTRE)|CREDIT CARDS?|PREPAID CARD|CARD OPERATIONS|CENTRAL PROCESSING|"
    r"CENTRALI[SZ]ED|CENTRAL PAYMENT|PROCESSING (UNIT|CENTER|CENTRE)|TREASURY|CURRENCY CHEST|"
    r"CORPORATE (BANKING|OFFICE|CREDIT)|CORPORATE BRANCH|HEAD OFFICE|DIGITAL CHANNEL|RECONC?ILIATION|"
    r"OPERATIONS HUB|FINANCE HUB|ACCELERATE HUB|COLLECTION HUB|PAYABLE HUB|FUND TRANSFER|RTGS|ZONAL OFFICE)\b"
)

# Kept in capitals when re-casing; small words stay lower case inside a name.
_ACRONYMS = frozenset(
    {
        "AIIMS",
        "APMC",
        "ATM",
        "BKC",
        "BSNL",
        "CBD",
        "CHS",
        "CHSL",
        "CIDCO",
        "CTS",
        "DLF",
        "GIDC",
        "GPO",
        "HDFC",
        "ICICI",
        "IDBI",
        "IFSC",
        "IIM",
        "IIT",
        "ITI",
        "LIC",
        "MG",
        "MIDC",
        "MHADA",
        "NH",
        "NIT",
        "NRI",
        "RBI",
        "RTO",
        "SBI",
        "SCO",
        "SEZ",
        "ST",
        "SV",
        "TV",
        "UCO",
    }
)
_SMALL_WORDS = frozenset({"and", "of", "the", "at", "in", "on", "to", "by", "for"})
_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def display_case(text: str) -> str:
    """'MUMBAI - THANE EAST' -> 'Mumbai - Thane East'; acronyms such as MIDC stay in capitals."""
    text = " ".join(str(text or "").split())

    def word(match: re.Match) -> str:
        w = match.group(0)
        upper = w.upper()
        if upper in _ACRONYMS:
            return upper
        if match.start() > 0 and w.lower() in _SMALL_WORDS:
            return w.lower()
        return w[0].upper() + w[1:].lower()

    return _WORD.sub(word, text)


def state_key(name: str) -> str:
    """Comparison key of a state name across the two lists (old names, '&', 'The ...')."""
    key = re.sub(r"[^A-Z]+", " ", str(name or "").upper().replace("&", " AND ")).strip()
    key = re.sub(r"^THE ", "", key)
    return {
        "DADAR AND NAGAR HAVELI": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
        "DADRA AND NAGAR HAVELI": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
        "DAMAN AND DIU": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
        "ORISSA": "ODISHA",
        "PONDICHERRY": "PUDUCHERRY",
        "UTTARANCHAL": "UTTARAKHAND",
        "TAMILNADU": "TAMIL NADU",
    }.get(key, key)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def _in_india(lat: float, lon: float) -> bool:
    return 6.0 <= lat <= 37.5 and 68.0 <= lon <= 97.5 and lat != lon


def _dms(text: str) -> float | None:
    """'22.3434' read as degrees.minutes-seconds (22 deg 34' 34" = 22.576); None if it cannot be."""
    match = _DMS.match(text)
    if not match:
        return None
    degrees, minutes, seconds = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
    return degrees + minutes / 60 + seconds / 3600 if minutes < 60 and seconds < 60 else None


def _readings(row: dict) -> list[tuple[float, float]]:
    """The office's plausible (lat, lon): the decimal value and, when the digits allow, the
    degrees-minutes-seconds reading (some circles wrote 22 deg 34' 34" as 22.3434)."""
    lat_text, lon_text = row["Latitude"].strip(), row["Longitude"].strip()
    try:
        lat, lon = float(lat_text), float(lon_text)
    except ValueError:
        return []
    readings = [(lat, lon)] if _in_india(lat, lon) else []
    dms = (_dms(lat_text), _dms(lon_text))
    if dms[0] is not None and dms[1] is not None and _in_india(*dms) and dms != (lat, lon):
        readings.append((dms[0], dms[1]))
    return readings


def _median(points: list[tuple[float, float]]) -> tuple[float, float]:
    return statistics.median(p[0] for p in points), statistics.median(p[1] for p in points)


def _office_rank(row: dict) -> tuple:
    # The pincode's delivery office first: head, then sub, then branch offices.
    kind = {"HO": 0, "PO": 1}.get(row["OfficeType"], 2)
    return (row["Delivery"] != "Delivery", kind, row["OfficeName"].casefold())


def office_name(name: str) -> str:
    """'Jekegram S.O' -> 'Jekegram'; 'Azad Nagar S.O (Mumbai)' -> 'Azad Nagar (Mumbai)'."""
    return " ".join(_OFFICE_SUFFIX.sub("", name).split())


def build_directory(rows: list[dict]) -> tuple[dict, dict]:
    """(output JSON, pincode -> (state key, lat, lon, district)) from the post office rows."""
    by_pincode: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        pincode = row["Pincode"].strip()
        if _PINCODE.match(pincode) and pincode != "999999":
            by_pincode[pincode].append(row)

    def district(row: dict) -> tuple[str, str]:
        return display_case(row["StateName"]), display_case(row["District"])

    # A pincode's district is the one most of its offices name (the delivery office breaks a tie).
    district_of: dict[str, tuple[str, str]] = {}
    for pincode, offices in by_pincode.items():
        votes = Counter(map(district, offices))
        top = max(votes.values())
        district_of[pincode] = district(min((r for r in offices if votes[district(r)] == top), key=_office_rank))

    # A point (as written) that the offices of several pincodes share is a placeholder: a postal
    # division's default for all its offices, or a typo copied around.
    def point(row: dict) -> tuple[str, str]:
        return row["Latitude"].strip(), row["Longitude"].strip()

    pincodes_at: dict[tuple[str, str], set[str]] = defaultdict(set)
    for pincode, offices in by_pincode.items():
        for row in offices:
            pincodes_at[point(row)].add(pincode)
    shared = {p for p, at in pincodes_at.items() if len(at) >= SHARED_POINT_PINCODES}

    # Per pincode: (the readings of an office, whether it is a head or sub office) for each
    # office with a point of its own, and for each office with a shared point.
    def office_readings(offices: list[dict], own: bool) -> list:
        return [
            (r, row["OfficeType"] in ("HO", "PO"))
            for row in offices
            if (point(row) not in shared) == own and (r := _readings(row))
        ]

    readings = {pincode: office_readings(offices, True) for pincode, offices in by_pincode.items()}
    shared_readings = {pincode: office_readings(offices, False) for pincode, offices in by_pincode.items()}
    # The neighbours of a pincode: the offices of the pincodes with the same first 4 (or 3)
    # digits, and of its district, from the unambiguous readings only (values that cannot be DMS).
    group_points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for pincode, office_readings in readings.items():
        clear = [r[0] for r, _ in office_readings if len(r) == 1]
        for key in (pincode[:4], pincode[:3], district_of[pincode]):
            group_points[key].extend(clear)
    groups: dict = {}
    for key, points in group_points.items():
        if points:
            centre = _median(points)
            spread = statistics.median(haversine_km(p, centre) for p in points)
            groups[key] = centre, max(MIN_RADIUS_KM, 2 * spread)

    def neighbours(pincode: str) -> list[tuple[tuple[float, float], float]]:
        """The 4-digit group (when it has enough points), the wider 3-digit group, the district
        (an island district such as Lakshadweep shares its 3 digits with the mainland)."""
        keys = [pincode[:4]] if len(group_points[pincode[:4]]) >= MIN_GROUP_POINTS else []
        return [groups[k] for k in [*keys, pincode[:3], district_of[pincode]] if k in groups]

    def town(points: list[tuple[tuple[float, float], bool]]) -> tuple[float, float]:
        """The median point, moved to the head and sub offices among them when they agree with it
        (the town a rural pincode is named after, where its branches are). When most points are
        further than TOWN_KM from it (two villages far apart), the median is nowhere: the head
        and sub offices' point instead."""
        middle = _median([p for p, _ in points])
        main = [p for p, is_main in points if is_main]
        near_main = [p for p in main if haversine_km(p, middle) <= TOWN_KM]
        if near_main:
            return _median(near_main)
        agree = sum(1 for p, _ in points if haversine_km(p, middle) <= TOWN_KM)
        return _median(main) if main and 2 * agree < len(points) else middle

    def centre_of(pincode: str) -> tuple[float | None, float | None, int]:
        """(lat, lon, approx) of a pincode from its offices' readings."""
        references = neighbours(pincode)

        def placed(office_readings: list) -> list:
            # Each office's reading nearest the neighbours (the decimal one when there are none).
            return [
                (min(r, key=lambda p: haversine_km(p, references[0][0])) if references else r[0], is_main)
                for r, is_main in office_readings
            ]

        def within(points: list, reference: tuple) -> list:
            centre, radius = reference
            return [(p, is_main) for p, is_main in points if haversine_km(p, centre) <= radius]

        points = placed(readings[pincode])
        for reference in references:
            near = within(points, reference)
            if near:
                return *town(near), 0
        # Far from its neighbours but several offices agree (a rural pincode on a group's edge).
        if points:
            middle = _median([p for p, _ in points])
            cluster = [(p, is_main) for p, is_main in points if haversine_km(p, middle) <= CLUSTER_KM]
            if len(cluster) >= 3 and all(haversine_km(middle, c) <= MAX_FROM_GROUP_KM for c, _ in references):
                return *town(cluster), 0
        if references:
            # Only shared points (a town's, or a placeholder): roughly there when they agree with
            # the nearest neighbours, else its district's centre (the last reference).
            coarse = within(placed(shared_readings[pincode]), references[0])
            return *(town(coarse) if coarse else references[-1][0]), 1
        return None, None, 1

    states = sorted({state for state, _ in district_of.values()})
    state_index = {state: i for i, state in enumerate(states)}
    districts = sorted(set(district_of.values()), key=lambda d: (d[0], d[1]))
    district_index = {d: i for i, d in enumerate(districts)}

    pincodes, located, approx, unknown = {}, {}, 0, 0
    for pincode in sorted(by_pincode):
        offices = by_pincode[pincode]
        lat, lon, flag = centre_of(pincode)
        approx += flag and lat is not None
        unknown += lat is None
        office = office_name(min(offices, key=_office_rank)["OfficeName"])
        coords = [round(lat, COORD_DIGITS), round(lon, COORD_DIGITS)] if lat is not None else [None, None]
        pincodes[pincode] = [office, district_index[district_of[pincode]], *coords, flag]
        located[pincode] = (state_key(district_of[pincode][0]), lat, lon, district_of[pincode][1])

    out = {
        "format": 1,
        "dataset": "All India Pincode Directory with latitude and longitude (India Post, data.gov.in)",
        "url": PINCODE_URL,
        "columns": ["office", "district", "lat", "lon", "approx"],
        "states": states,
        "districts": [[name, state_index[state]] for state, name in districts],
        "pincodes": pincodes,
    }
    print(
        f"directory: {len(rows)} post offices, {len(pincodes)} pincodes, {len(shared)} points shared by "
        f"{SHARED_POINT_PINCODES}+ pincodes, {approx} approximate (a shared point or the district's centre), "
        f"{unknown} without coordinates"
    )
    return out, located


def address_pincode(address: str, state: str, located: dict) -> str | None:
    """The pincode in a branch address: the last one the directory knows, one in the branch's state first."""
    found = [a + b for a, b in _PINCODE_IN_TEXT.findall(address)]
    known = [p for p in found if p in located]
    same_state = [p for p in known if located[p][0] == state_key(state)]
    pick = same_state or known
    return pick[-1] if pick else None


def _words(text: str) -> str:
    return " " + " ".join(re.findall(r"[A-Z0-9]+", text.upper())) + " "


def branch_city(row: dict, district: str) -> str:
    """The branch's centre, district or city as published, the first its address or name confirms
    (the list often names a district from before a boundary change); else the pincode's district."""
    text = _words(row["ADDRESS"] + " " + row["BRANCH"])
    for value in (row["CENTRE"], row["DISTRICT"], row["CITY"]):
        if value.strip() and _words(value) in text:
            return display_case(value)
    return district


def build_branches(rows: list[dict], located: dict) -> dict:
    lenders = {}
    for lender_id, (bank, prefix) in BANKS.items():
        own = re.compile(rf"^{prefix}0\d{{6}}$")
        stats = Counter()
        seen, branches = set(), []
        for row in rows:
            if row["BANK"] != bank:
                continue
            if not own.match(row["IFSC"]):
                stats["other_codes"] += 1  # sub-members (co-operative banks) and special codes
                continue
            stats["own"] += 1
            if _NOT_A_BRANCH.search(row["BRANCH"].upper()):
                stats["back_office"] += 1
                continue
            pincode = address_pincode(row["ADDRESS"], row["STATE"], located)
            if pincode is None:
                stats["no_pincode"] += 1
                continue
            if row["IFSC"] in seen:
                continue
            seen.add(row["IFSC"])
            address = " ".join(row["ADDRESS"].split())
            if len(address) > MAX_ADDRESS:
                address = address[: MAX_ADDRESS - 1].rstrip(" ,") + "…"
            branches.append(
                [
                    row["IFSC"],
                    display_case(row["BRANCH"]),
                    display_case(address),
                    branch_city(row, located[pincode][3]),
                    pincode,
                ]
            )
        branches.sort(key=lambda b: (b[4], b[1], b[0]))
        stats["kept"] = len(branches)
        lenders[lender_id] = {"name": bank, "ifsc_prefix": prefix, "branches": branches}
        print(
            f"{bank}: {stats['own']} own branches, {stats['back_office']} back-office units, "
            f"{stats['no_pincode']} without a known pincode in the address, {stats['kept']} kept "
            f"({stats['other_codes']} sub-member or special codes skipped)"
        )
    return {
        "format": 1,
        "dataset": f"RBI bank branch list (IFSC) via razorpay/ifsc {IFSC_RELEASE}",
        "url": f"https://github.com/razorpay/ifsc/releases/tag/{IFSC_RELEASE}",
        "columns": ["ifsc", "name", "address", "city", "pincode"],
        "lenders": lenders,
    }


def _download(url: str, folder: Path) -> Path:
    path = folder / url.rsplit("/", 1)[-1]
    print(f"downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (build_branch_data.py)"})
    with urllib.request.urlopen(request, timeout=600) as response, path.open("wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    return path


def _read_csv(path: Path) -> tuple[list[dict], str]:
    data = path.read_bytes()
    text = data.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text))), hashlib.sha256(data).hexdigest()


def _write(name: str, payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    buffer = io.BytesIO()
    # mtime=0 and no file name: the same data gives the same bytes.
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, compresslevel=9, mtime=0) as gz:
        gz.write(raw)
    (OUT_DIR / name).write_bytes(buffer.getvalue())
    print(f"wrote {name}: {len(raw) / 1e6:.1f} MB JSON, {len(buffer.getvalue()) / 1e6:.2f} MB gzip")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pincode-csv", type=Path, help=f"India Post directory CSV (default: download {PINCODE_URL})")
    parser.add_argument("--ifsc-csv", type=Path, help=f"razorpay/ifsc IFSC.csv (default: download {IFSC_URL})")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        pincode_csv = args.pincode_csv or _download(PINCODE_URL, Path(tmp))
        ifsc_csv = args.ifsc_csv or _download(IFSC_URL, Path(tmp))
        offices, offices_sha = _read_csv(pincode_csv)
        banks, banks_sha = _read_csv(ifsc_csv)
    directory, located = build_directory(offices)
    directory["sha256"] = offices_sha
    branches = build_branches(banks, located)
    branches["sha256"] = banks_sha
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _write(DIRECTORY_FILE, directory)
    _write(BRANCHES_FILE, branches)
    return 0


if __name__ == "__main__":
    sys.exit(main())
