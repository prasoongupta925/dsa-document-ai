"""Nearest lender branches for a pincode: public open data, SAMPLE rows and the DSA's own lists.

Data (app/data/branches, built by scripts/build_branch_data.py; sources and licences in its
README.md):
- pincode_directory.json.gz: India Post's All India Pincode Directory (data.gov.in,
  Government Open Data License - India): every pincode's delivery office, district, state
  and centre;
- bank_branches.json.gz: the branches of HDFC Bank, ICICI Bank and Axis Bank in RBI's IFSC
  list (razorpay/ifsc, public domain), placed by the pincode in their published address;
- sample_nbfc_branches.json: a few SAMPLE rows for Bajaj Finance and Tata Capital near the
  demo pincodes (no open branch list exists for them), labelled as such wherever shown.

A distance is pincode centre to pincode centre (haversine), so it is "about" N km; a branch
in the same pincode is 0 km, and comes first. Branches come nearest first (then by name), at
most MAX_BRANCHES of them and none over MAX_DISTANCE_KM away. Some pincodes have no point of
their own in the directory (approx): they take a point their post offices share with other
pincodes', or their district's centre, so a distance to or from one is marked approximate.

The DSA's own lists (app/reference_data.py) come first: a lender with rows in the uploaded
branch list gets those branches instead of the public or SAMPLE ones, and a lender with rows
in the uploaded serviceability list is serviceable exactly where that list says. Otherwise
serviceability is the SAMPLE pincode list of app/data/pincodes.json (the one the eligibility
calculation uses), and unknown (null) for a lender that list does not have.

Lenders are matched by name with legal words ignored ("HDFC Bank Ltd" = "HDFC", "Tata
Capital Financial Services" = "Tata Capital") or by the policy file's id (hdfc_bank).
"""

import gzip
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from app import eligibility

DATA_DIR = Path(__file__).resolve().parent / "data" / "branches"
DIRECTORY_FILE = "pincode_directory.json.gz"
BANK_BRANCHES_FILE = "bank_branches.json.gz"
SAMPLE_BRANCHES_FILE = "sample_nbfc_branches.json"

MAX_BRANCHES = 3
MAX_DISTANCE_KM = 200
EARTH_RADIUS_KM = 6371.0088
KM_PER_DEGREE_LATITUDE = EARTH_RADIUS_KM * math.pi / 180

Source = Literal["dsa_list", "public_data", "sample"]

SOURCES = {
    "india_post": {
        "name": "Department of Posts, All India Pincode Directory with Latitude and Longitude (data.gov.in): "
        "pincode office, district, state and location, used for every distance",
        "licence": "Government Open Data License - India (GODL-India)",
        "url": "https://data.gov.in/files/ogdpv2dms/s3fs-public/dataurl03122020/pincode.csv",
    },
    "rbi_ifsc": {
        "name": "RBI list of bank branches (IFSC), as published by razorpay/ifsc v2.0.62: HDFC Bank, ICICI Bank "
        "and Axis Bank branches",
        "licence": "Public domain (dataset); razorpay/ifsc code under the MIT License",
        "url": "https://github.com/razorpay/ifsc",
    },
    "sample_branches": {
        "name": "SAMPLE branches for Bajaj Finance and Tata Capital, made up for the demo (no open list exists)",
        "licence": "Synthetic sample data, not real branches",
        "url": None,
    },
    "sample_serviceability": {
        "name": f"SAMPLE serviceable pincode list ({eligibility.SAMPLE_LABEL})",
        "licence": "Synthetic sample data",
        "url": None,
    },
}

_LEGAL_WORDS = frozenset(
    {
        "the",
        "ltd",
        "limited",
        "pvt",
        "private",
        "co",
        "company",
        "corp",
        "corporation",
        "india",
        "bank",
        "finance",
        "financial",
        "services",
        "fin",
    }
)


def lender_key(name: Any) -> str:
    """Comparison key of a lender name: 'HDFC Bank Ltd' -> 'hdfc', 'hdfc_bank' -> 'hdfc'."""
    words = re.findall(r"[a-z0-9]+", str(name or "").casefold().replace("&", " and "))
    kept = [w for w in words if w not in _LEGAL_WORDS]
    return " ".join(kept or words)


# ------------------------------------------------------------------ data
@dataclass(frozen=True)
class Place:
    pincode: str
    office: str
    district: str
    state: str
    lat: float | None
    lon: float | None
    # No point of its own in the directory: a point its offices share, or its district's centre.
    approximate: bool = False


@dataclass(frozen=True)
class Branch:
    name: str
    pincode: str
    address: str | None = None
    city: str | None = None
    ifsc: str | None = None
    # As given in a DSA list; the directory's district and state are used when it knows the pincode.
    district: str | None = None
    state: str | None = None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, h)))


class Directory:
    """India Post's pincodes: office, district, state and centre."""

    def __init__(self, data: Mapping[str, Any]):
        self.dataset = data.get("dataset")
        states = data["states"]
        self._districts = [(name, states[state]) for name, state in data["districts"]]
        self._pincodes: dict[str, list] = data["pincodes"]

    def __len__(self) -> int:
        return len(self._pincodes)

    def __contains__(self, pincode: object) -> bool:
        return pincode in self._pincodes

    def place(self, pincode: str | None) -> Place | None:
        entry = self._pincodes.get(str(pincode or ""))
        if entry is None:
            return None
        office, district, lat, lon, approx = entry
        name, state = self._districts[district]
        return Place(str(pincode), office, name, state, lat, lon, bool(approx))

    def distance_km(self, origin: Place, pincode: str, within: float | None = None) -> float | None:
        """About how far `pincode`'s centre is from `origin` (0 for the same pincode); None when
        either has no location or, given `within`, it is further than that."""
        if pincode == origin.pincode:
            return 0.0
        entry = self._pincodes.get(pincode)
        if entry is None or entry[2] is None or origin.lat is None or origin.lon is None:
            return None
        # A great circle is never shorter than its latitude difference: skip the far ones cheaply.
        if within is not None and abs(entry[2] - origin.lat) * KM_PER_DEGREE_LATITUDE > within:
            return None
        distance = haversine_km(origin.lat, origin.lon, entry[2], entry[3])
        return distance if within is None or distance <= within else None


def _gzip_json(name: str) -> Any:
    with gzip.open(DATA_DIR / name, "rt", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_directory() -> Directory:
    return Directory(_gzip_json(DIRECTORY_FILE))


@dataclass(frozen=True)
class LenderData:
    """One lender's shipped branches (public or SAMPLE), grouped by pincode."""

    id: str
    name: str
    source: Source
    by_pincode: Mapping[str, tuple[Branch, ...]] = field(repr=False)
    label: str | None = None

    @property
    def count(self) -> int:
        return sum(len(rows) for rows in self.by_pincode.values())


def _group(branches: Iterable[Branch]) -> dict[str, tuple[Branch, ...]]:
    grouped: dict[str, list[Branch]] = {}
    for branch in branches:
        grouped.setdefault(branch.pincode, []).append(branch)
    return {pincode: tuple(rows) for pincode, rows in grouped.items()}


@lru_cache(maxsize=1)
def load_shipped_branches() -> dict[str, LenderData]:
    """lender id -> its public branches (banks) or SAMPLE branches (NBFCs)."""
    out: dict[str, LenderData] = {}
    public = _gzip_json(BANK_BRANCHES_FILE)
    columns = public["columns"]
    for lender_id, entry in public["lenders"].items():
        rows = (dict(zip(columns, row, strict=True)) for row in entry["branches"])
        branches = (Branch(r["name"], r["pincode"], r["address"], r["city"], r["ifsc"]) for r in rows)
        out[lender_id] = LenderData(lender_id, entry["name"], "public_data", _group(branches))
    sample = json.loads((DATA_DIR / SAMPLE_BRANCHES_FILE).read_text(encoding="utf-8"))
    for lender_id, entry in sample["lenders"].items():
        if lender_id in out:
            raise ValueError(f"{SAMPLE_BRANCHES_FILE}: {lender_id} also has public branches")
        branches = (Branch(b["name"], b["pincode"], b.get("address"), b.get("city")) for b in entry["branches"])
        out[lender_id] = LenderData(lender_id, entry["name"], "sample", _group(branches), sample.get("label"))
    return out


# ------------------------------------------------------------------ lenders
@dataclass(frozen=True)
class LenderRef:
    name: str
    id: str | None

    @property
    def match_key(self) -> str:
        """What a DSA list row must match: the lender id when known, else the name key."""
        return self.id or f"name:{lender_key(self.name)}"


@lru_cache(maxsize=1)
def _known_lenders() -> dict[str, str]:
    """name key -> lender id, for the policy file's lenders and those with shipped branches."""
    known: dict[str, str] = {}
    for lender_id, data in load_shipped_branches().items():
        known.update({lender_key(lender_id): lender_id, lender_key(data.name): lender_id})
    for lender in eligibility.load_policy_book().lenders:
        known.update({lender_key(lender.id): lender.id, lender_key(lender.name): lender.id})
    return known


@lru_cache(maxsize=4096)
def resolve_lender(name: str) -> LenderRef:
    """The lender `name` means: a lender of the policy file or of the shipped branches, else an unknown one."""
    name = " ".join(str(name or "").split())
    return LenderRef(name, _known_lenders().get(lender_key(name)))


def match_key(name: str) -> str:
    return resolve_lender(name).match_key


# ------------------------------------------------------------------ nearest
def nearest(
    origin: Place,
    by_pincode: Mapping[str, Iterable[Branch]],
    directory: Directory,
    limit: int = MAX_BRANCHES,
    max_km: float = MAX_DISTANCE_KM,
) -> list[tuple[Branch, float]]:
    """The `limit` branches nearest `origin` (by distance, the same pincode first at a tie, then
    by name): another pincode can sit at the same point (a shared or district point)."""
    found: list[tuple[float, bool, str, Branch]] = []
    for pincode, rows in by_pincode.items():
        distance = directory.distance_km(origin, pincode, within=max_km)
        if distance is not None:
            other = pincode != origin.pincode
            found.extend((distance, other, branch.name.casefold(), branch) for branch in rows)
    found.sort(key=lambda f: (*f[:3], f[3].ifsc or ""))
    return [(branch, distance) for distance, _, _, branch in found[:limit]]


@dataclass
class OwnLists:
    """The DSA's uploaded lists, as find() uses them (app/reference_data.py builds this)."""

    # match key -> pincode -> serviceable, for each lender the serviceability list has
    serviceable: Mapping[str, Mapping[str, bool]] = field(default_factory=dict)
    # match key -> pincode -> branches, for each lender the branch list has
    branches: Mapping[str, Mapping[str, tuple[Branch, ...]]] = field(default_factory=dict)
    # sources entries for the lists that were uploaded
    serviceable_source: dict | None = None
    branches_source: dict | None = None


def _branch_out(branch: Branch, distance: float, directory: Directory, origin: Place) -> dict[str, Any]:
    place = directory.place(branch.pincode)
    return {
        "name": branch.name,
        "address": branch.address,
        "city": branch.city,
        "district": place.district if place else branch.district,
        "state": place.state if place else branch.state,
        "pincode": branch.pincode,
        "ifsc": branch.ifsc,
        "distance_km": round(distance, 1),
        # One of the two pincodes has no point of its own: the distance is rougher.
        "approximate": branch.pincode != origin.pincode
        and (origin.approximate or (place is not None and place.approximate)),
    }


def _place_out(place: Place | None) -> dict[str, Any] | None:
    if place is None:
        return None
    return {
        "office": place.office,
        "district": place.district,
        "state": place.state,
        "lat": place.lat,
        "lon": place.lon,
        "approximate": place.approximate,
    }


def find(pincode: str, lenders: Iterable[str], own: OwnLists | None = None) -> dict[str, Any]:
    """Serviceability and the nearest branches of each lender at `pincode`, with the sources used."""
    own = own or OwnLists()
    directory = load_directory()
    shipped = load_shipped_branches()
    book = eligibility.load_policy_book()
    origin = directory.place(pincode)
    used: dict[str, dict] = {}
    notes: list[str] = []
    if origin is None:
        notes.append(f"Pincode {pincode} is not in the India Post directory: branches cannot be placed")
    else:
        used["india_post"] = SOURCES["india_post"]
        if origin.approximate:
            notes.append(
                f"The directory has no exact location for pincode {pincode}: distances from it are approximate "
                "(from a point its post offices share, or its district's centre)"
            )

    rows = []
    for ref in map(resolve_lender, lenders):
        lender_notes: list[str] = []
        label = None
        # Serviceability: the DSA's list, else the SAMPLE pincode list of the policy file.
        serviceable_source: Source | None = None
        serviceable: bool | None = None
        if ref.match_key in own.serviceable:
            serviceable_source, serviceable = "dsa_list", own.serviceable[ref.match_key].get(pincode, False)
            used["dsa_serviceable"] = own.serviceable_source or {}
            if pincode not in own.serviceable[ref.match_key]:
                lender_notes.append(f"Pincode {pincode} is not on your serviceability list for {ref.name}")
        elif ref.id and book.lender(ref.id):
            serviceable = book.serviceable(ref.id, pincode)
            serviceable_source = "sample" if book.pincodes.sample else "dsa_list"
            if book.pincodes.sample:
                used["sample_serviceability"] = SOURCES["sample_serviceability"]

        # Branches: the DSA's list, else the public data (banks), else the SAMPLE rows (NBFCs);
        # a branch list is a source of the answer only when the pincode could be placed.
        branches_source: Source | None = None
        by_pincode: Mapping[str, Iterable[Branch]] = {}
        if ref.match_key in own.branches:
            branches_source, by_pincode = "dsa_list", own.branches[ref.match_key]
            if origin:
                used["dsa_branches"] = own.branches_source or {}
        elif ref.id in shipped:
            data = shipped[ref.id]
            branches_source, by_pincode, label = data.source, data.by_pincode, data.label
            key = "rbi_ifsc" if data.source == "public_data" else "sample_branches"
            if origin:
                used[key] = SOURCES[key]
        else:
            lender_notes.append(
                f"No branch data for {ref.name}: the public data covers HDFC Bank, ICICI Bank and Axis Bank; "
                "upload your branch list"
            )

        found = nearest(origin, by_pincode, directory) if origin and by_pincode else []
        if origin and by_pincode and not found:
            lender_notes.append(f"No {ref.name} branch within {MAX_DISTANCE_KM} km")
        if "dsa_list" in (serviceable_source, branches_source):
            source: Source = "dsa_list"
        else:
            source = branches_source or serviceable_source or "public_data"
        rows.append(
            {
                "lender": ref.name,
                "lender_id": ref.id,
                "serviceable": serviceable,
                "source": source,
                "serviceable_source": serviceable_source,
                "branches_source": branches_source,
                "branches": [_branch_out(b, d, directory, origin) for b, d in found] if origin else [],
                "label": label,
                "notes": lender_notes,
            }
        )
    return {
        "pincode": pincode,
        "place": _place_out(origin),
        "lenders": rows,
        "sources": [s for s in used.values() if s],
        "notes": notes,
    }
