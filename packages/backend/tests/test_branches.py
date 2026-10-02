"""Tests for the nearest lender branches (app/branches.py) and the data it ships (app/data/branches).

The answers are worked out from the shipped data itself: the public India Post pincode directory
and RBI bank branch list, and the SAMPLE NBFC rows. The DSA's own lists are made up here (made-up
branch names only). Facts that a data refresh would change (branch names, counts) are not
asserted; the demo pincodes' places and the data's shape are.
"""

import gzip
import json
import re
from pathlib import Path

import pytest

from app import branches, eligibility
from app.branches import Branch, OwnLists

DATA_DIR = Path(branches.__file__).resolve().parent / "data" / "branches"
BANKS = {"hdfc_bank": "HDFC", "icici_bank": "ICIC", "axis_bank": "UTIB"}
NBFCS = ("bajaj_finance", "tata_capital")
DSA_SERVICEABLE = {"name": "Your pincode serviceability list (service.csv)", "licence": "Your own data", "url": None}
DSA_BRANCHES = {"name": "Your lender branches list (branches.csv)", "licence": "Your own data", "url": None}


def lender_row(answer: dict, lender: str) -> dict:
    return next(row for row in answer["lenders"] if row["lender"] == lender)


def source_names(answer: dict) -> list[str]:
    return [source["name"] for source in answer["sources"]]


# ------------------------------------------------------------------ places and distances
class TestDirectory:
    def test_the_demo_pincodes_are_placed(self):
        directory = branches.load_directory()
        thane = directory.place("400606")
        assert (thane.office, thane.district, thane.state) == ("Jekegram", "Thane", "Maharashtra")
        assert thane.approximate is False
        assert 19.1 < thane.lat < 19.3 and 72.9 < thane.lon < 73.05
        assert directory.place("400601").district == "Thane"
        assert directory.place("400001").office == "Mumbai GPO"
        assert directory.place("999999") is None
        assert directory.place(None) is None

    def test_distances_are_pincode_centre_to_pincode_centre(self):
        directory = branches.load_directory()
        thane = directory.place("400606")
        assert directory.distance_km(thane, "400606") == 0.0
        # Thane (Jekegram) to Mumbai GPO: about 33 km as the crow flies.
        assert 28 < directory.distance_km(thane, "400001") < 38
        assert directory.distance_km(thane, "400001", within=20) is None
        assert directory.distance_km(thane, "110001", within=200) is None
        assert directory.distance_km(thane, "999999") is None

    def test_haversine(self):
        assert branches.haversine_km(19.0, 72.8, 19.0, 72.8) == 0.0
        # One degree of latitude is about 111 km.
        assert 110.5 < branches.haversine_km(19.0, 72.8, 20.0, 72.8) < 111.8


# ------------------------------------------------------------------ the answer
class TestFind:
    def test_the_contract_shape(self):
        answer = branches.find("400606", ["HDFC Bank", "Bajaj Finance"])
        assert set(answer) >= {"pincode", "place", "lenders", "sources"}
        assert answer["pincode"] == "400606"
        assert set(answer["place"]) >= {"office", "district", "state", "lat", "lon"}
        assert [row["lender"] for row in answer["lenders"]] == ["HDFC Bank", "Bajaj Finance"]
        for row in answer["lenders"]:
            assert set(row) >= {"lender", "serviceable", "source", "branches"}
            assert row["source"] in ("dsa_list", "public_data", "sample")
            for branch in row["branches"]:
                assert set(branch) == {
                    "name",
                    "address",
                    "city",
                    "district",
                    "state",
                    "pincode",
                    "ifsc",
                    "distance_km",
                    "approximate",
                }
        for source in answer["sources"]:
            assert set(source) == {"name", "licence", "url"}

    @pytest.mark.parametrize("pincode", ["400606", "400603", "400615", "400601", "400053", "411014"])
    def test_every_bank_has_up_to_three_branches_nearest_first(self, pincode):
        answer = branches.find(pincode, ["HDFC Bank", "ICICI Bank", "Axis Bank"])
        for lender_id, prefix in BANKS.items():
            row = next(r for r in answer["lenders"] if r["lender_id"] == lender_id)
            found = row["branches"]
            assert 1 <= len(found) <= branches.MAX_BRANCHES
            distances = [b["distance_km"] for b in found]
            assert distances == sorted(distances)
            assert all(0 <= d <= branches.MAX_DISTANCE_KM for d in distances)
            assert all(re.fullmatch(rf"{prefix}0\d{{6}}", b["ifsc"]) for b in found)
            assert (row["source"], row["branches_source"]) == ("public_data", "public_data")
            # Mumbai, Thane and Pune have branches of all three banks within 15 km.
            assert distances[0] < 15

    def test_a_branch_in_the_same_pincode_is_0_km_and_first(self):
        answer = branches.find("400606", ["HDFC Bank"])
        first = lender_row(answer, "HDFC Bank")["branches"][0]
        assert (first["pincode"], first["distance_km"]) == ("400606", 0.0)
        assert (first["district"], first["state"]) == ("Thane", "Maharashtra")
        assert first["address"]

    def test_serviceability_is_the_sample_pincode_list_of_the_calculation(self):
        book = eligibility.load_policy_book()
        answer = branches.find("400606", [lender.name for lender in book.lenders])
        for lender in book.lenders:
            row = lender_row(answer, lender.name)
            assert row["serviceable"] is book.serviceable(lender.id, "400606")
            assert row["serviceable_source"] == "sample"
        assert f"SAMPLE serviceable pincode list ({eligibility.SAMPLE_LABEL})" in source_names(answer)

    def test_nbfcs_get_their_sample_rows_labelled_as_such(self):
        answer = branches.find("400606", ["Bajaj Finance", "Tata Capital"])
        for name in ("Bajaj Finance", "Tata Capital"):
            row = lender_row(answer, name)
            assert row["source"] == row["branches_source"] == "sample"
            assert "SAMPLE" in row["label"]
            assert row["branches"]
            for branch in row["branches"]:
                assert branch["name"].endswith("(sample)")
                assert branch["ifsc"] is None
        sample = next(s for s in answer["sources"] if s["name"].startswith("SAMPLE branches"))
        assert "not real" in sample["licence"]

    def test_every_source_used_is_listed_with_its_licence(self):
        answer = branches.find("400606", ["HDFC Bank", "Tata Capital"])
        licences = {s["licence"] for s in answer["sources"]}
        assert "Government Open Data License - India (GODL-India)" in licences
        assert any(s["url"] == "https://github.com/razorpay/ifsc" for s in answer["sources"])
        assert any("Public domain" in licence for licence in licences)
        # Only what was used: no DSA list here.
        assert not any(name.startswith("Your") for name in source_names(answer))

    @pytest.mark.parametrize(
        ("asked", "lender_id"),
        [
            ("HDFC", "hdfc_bank"),
            ("hdfc bank ltd", "hdfc_bank"),
            ("hdfc_bank", "hdfc_bank"),
            ("HDFC Bank Limited", "hdfc_bank"),
            ("ICICI  Bank", "icici_bank"),
            ("Axis Bank Ltd.", "axis_bank"),
            ("Bajaj Finance Limited", "bajaj_finance"),
            ("Tata Capital Financial Services Ltd", "tata_capital"),
            ("ICICI Home Finance", None),
            ("Kotak Mahindra Bank", None),
        ],
    )
    def test_lender_names_are_matched_without_their_legal_words(self, asked, lender_id):
        assert branches.resolve_lender(asked).id == lender_id

    def test_a_lender_without_any_data(self):
        row = branches.find("400606", ["Kotak Mahindra Bank"])["lenders"][0]
        assert row["lender"] == "Kotak Mahindra Bank"
        assert (row["lender_id"], row["serviceable"], row["branches"], row["branches_source"]) == (None, None, [], None)
        assert row["notes"] == [
            "No branch data for Kotak Mahindra Bank: the public data covers HDFC Bank, ICICI Bank and Axis Bank; "
            "upload your branch list"
        ]

    def test_a_pincode_the_directory_does_not_know(self):
        answer = branches.find("999999", ["HDFC Bank", "Bajaj Finance"])
        assert answer["place"] is None
        assert answer["notes"] == ["Pincode 999999 is not in the India Post directory: branches cannot be placed"]
        assert all(row["branches"] == [] for row in answer["lenders"])
        # The branch lists were not used; the serviceability list was.
        assert source_names(answer) == [f"SAMPLE serviceable pincode list ({eligibility.SAMPLE_LABEL})"]

    def test_no_branch_is_shown_beyond_200_km(self):
        row = lender_row(branches.find("110001", ["Bajaj Finance"]), "Bajaj Finance")
        assert row["branches"] == []
        assert row["notes"] == ["No Bajaj Finance branch within 200 km"]
        assert row["serviceable"] is False

    def test_an_approximate_pincode_says_so(self):
        # Chinchwad: its only point is one that 12 Pune offices share, 55 km west of Pune.
        answer = branches.find("411033", ["HDFC Bank"])
        assert answer["place"]["approximate"] is True
        assert answer["notes"] == [
            "The directory has no exact location for pincode 411033: distances from it are approximate (from a "
            "point its post offices share, or its district's centre)"
        ]
        found = lender_row(answer, "HDFC Bank")["branches"]
        # Its own branches first, exact; the others are marked approximate.
        assert found[0]["pincode"] == "411033"
        assert found[0]["approximate"] is False
        assert all(b["approximate"] for b in found if b["pincode"] != "411033")

    def test_a_branch_in_an_approximate_pincode_is_marked(self):
        directory = branches.load_directory()
        thane = directory.place("400606")
        exact = directory.place("400608")
        assert not thane.approximate and not exact.approximate
        approximate = next(p for p in sorted(directory._pincodes) if directory.place(p).approximate)
        out = branches._branch_out(Branch("Example", approximate), 120.0, directory, thane)
        assert out["approximate"] is True
        assert branches._branch_out(Branch("Example", "400608"), 3.8, directory, thane)["approximate"] is False


# ------------------------------------------------------------------ the DSA's own lists
class TestOwnLists:
    def own(self) -> OwnLists:
        return OwnLists(
            serviceable={"hdfc_bank": {"400603": True, "400615": False}},
            branches={
                "bajaj_finance": {
                    "400615": (Branch("Kasarvadavali Naka", "400615", "Shop 2, Station Road", "Kasarvadavali"),),
                    # Not in the directory: cannot be placed, never shown.
                    "999999": (Branch("Nowhere", "999999"),),
                }
            },
            serviceable_source=DSA_SERVICEABLE,
            branches_source=DSA_BRANCHES,
        )

    def test_the_serviceability_list_decides_for_the_lenders_it_has(self):
        answer = branches.find("400606", ["HDFC Bank", "ICICI Bank"], self.own())
        hdfc = lender_row(answer, "HDFC Bank")
        assert (hdfc["serviceable"], hdfc["serviceable_source"], hdfc["source"]) == (False, "dsa_list", "dsa_list")
        assert hdfc["notes"] == ["Pincode 400606 is not on your serviceability list for HDFC Bank"]
        # Its branches are still the public ones.
        assert hdfc["branches_source"] == "public_data" and hdfc["branches"]
        assert lender_row(branches.find("400603", ["HDFC"], self.own()), "HDFC")["serviceable"] is True
        # A lender the list does not have keeps the SAMPLE list.
        icici = lender_row(answer, "ICICI Bank")
        assert (icici["serviceable"], icici["serviceable_source"], icici["source"]) == (True, "sample", "public_data")
        assert DSA_SERVICEABLE["name"] in source_names(answer)

    def test_the_branch_list_replaces_the_shipped_branches(self):
        answer = branches.find("400606", ["Bajaj Finance"], self.own())
        row = lender_row(answer, "Bajaj Finance")
        assert (row["source"], row["branches_source"], row["label"]) == ("dsa_list", "dsa_list", None)
        assert [b["name"] for b in row["branches"]] == ["Kasarvadavali Naka"]
        branch = row["branches"][0]
        # District and state come from the directory when the list has none.
        assert (branch["city"], branch["district"], branch["state"], branch["ifsc"]) == (
            "Kasarvadavali",
            "Thane",
            "Maharashtra",
            None,
        )
        assert 5 < branch["distance_km"] < 12
        assert DSA_BRANCHES["name"] in source_names(answer)
        assert not any(name.startswith("SAMPLE branches") for name in source_names(answer))

    def test_a_list_district_is_kept_when_the_directory_cannot_place_the_branch(self):
        directory = branches.load_directory()
        branch = Branch("Nowhere", "999999", district="Example District", state="Example State")
        out = branches._branch_out(branch, 0.0, directory, directory.place("400606"))
        assert (out["district"], out["state"]) == ("Example District", "Example State")

    def test_unknown_lenders_are_matched_by_name(self):
        own = OwnLists(
            branches={branches.match_key("Example Credit Co-op Bank"): {"400608": (Branch("Thane Market", "400608"),)}}
        )
        row = branches.find("400606", ["example credit co-op bank ltd"], own)["lenders"][0]
        assert row["lender_id"] is None
        assert [b["name"] for b in row["branches"]] == ["Thane Market"]
        assert row["source"] == "dsa_list"


class TestNearest:
    def test_ties_go_by_name_and_at_most_the_limit_is_returned(self):
        directory = branches.load_directory()
        origin = directory.place("400606")
        by_pincode = {
            "400606": (Branch("Zeta", "400606"), Branch("alpha", "400606")),
            "400615": (Branch("Beta", "400615"),),
            "400001": (Branch("Gamma", "400001"),),
        }
        found = branches.nearest(origin, by_pincode, directory, limit=3)
        assert [(b.name, round(d)) for b, d in found] == [("alpha", 0), ("Zeta", 0), ("Beta", 7)]
        assert branches.nearest(origin, by_pincode, directory, max_km=1) == [
            (by_pincode["400606"][1], 0.0),
            (by_pincode["400606"][0], 0.0),
        ]

    def test_the_same_pincode_wins_a_tie_with_a_pincode_at_the_same_point(self):
        directory = branches.load_directory()
        # Two Pune pincodes without a point of their own sit at the same place.
        assert directory.distance_km(directory.place("411033"), "411032") == 0.0
        origin = directory.place("411033")
        by_pincode = {
            "411032": (Branch("Airport Road", "411032"),),
            "411033": (Branch("Thergaon", "411033"),),
        }
        found = branches.nearest(origin, by_pincode, directory)
        assert [b.name for b, _ in found] == ["Thergaon", "Airport Road"]


# ------------------------------------------------------------------ the shipped data
class TestShippedData:
    @pytest.mark.parametrize(
        ("pincode", "town", "within_km"),
        [
            ("400001", (18.938, 72.835), 3),  # Mumbai GPO
            ("400053", (19.128, 72.834), 3),  # Andheri West (9 Mumbai offices share a Malabar Hill point)
            ("400601", (19.196, 72.970), 3),  # Thane
            ("400606", (19.209, 72.963), 3),  # Jekegram, Pokhran Road, Thane West
            ("400603", (19.185, 72.981), 3),  # Thane East (Kopri)
            ("400615", (19.268, 72.966), 3),  # Kasarvadavali, Ghodbunder Road
            ("411038", (18.507, 73.807), 3),  # Kothrud, Pune
            ("411045", (18.560, 73.780), 3),  # Baner, Pune
            ("411033", (18.630, 73.800), 25),  # Chinchwad, Pune: approximate (no point of its own)
        ],
    )
    def test_the_demo_pincodes_sit_near_their_towns(self, pincode, town, within_km):
        place = branches.load_directory().place(pincode)
        assert branches.haversine_km(place.lat, place.lon, *town) < within_km

    def test_it_stays_small_enough_for_the_backend_image(self):
        sizes = {path.name: path.stat().st_size for path in DATA_DIR.iterdir()}
        assert set(sizes) == {
            "README.md",
            "bank_branches.json.gz",
            "pincode_directory.json.gz",
            "sample_nbfc_branches.json",
        }
        assert sum(sizes.values()) < 2_000_000

    def test_every_bank_branch_is_placed_and_its_own(self):
        directory = branches.load_directory()
        shipped = branches.load_shipped_branches()
        for lender_id, prefix in BANKS.items():
            data = shipped[lender_id]
            assert data.source == "public_data"
            ifscs = [b.ifsc for rows in data.by_pincode.values() for b in rows]
            assert len(ifscs) == len(set(ifscs)) == data.count > 1000
            assert all(re.fullmatch(rf"{prefix}0\d{{6}}", ifsc) for ifsc in ifscs)
            assert all(pincode in directory for pincode in data.by_pincode)

    def test_the_sample_rows_are_marked_and_only_for_nbfcs(self):
        sample = json.loads((DATA_DIR / "sample_nbfc_branches.json").read_text(encoding="utf-8"))
        assert sample["sample"] is True and "SAMPLE" in sample["label"]
        assert sorted(sample["lenders"]) == sorted(NBFCS)
        directory = branches.load_directory()
        for entry in sample["lenders"].values():
            for branch in entry["branches"]:
                assert branch["name"].endswith("(sample)")
                assert "SAMPLE" in branch["address"]
                assert branch["pincode"] in directory

    def test_every_policy_lender_has_branch_data(self):
        shipped = branches.load_shipped_branches()
        assert {lender.id for lender in eligibility.load_policy_book().lenders} <= set(shipped)

    def test_the_readme_names_each_source_its_licence_and_the_input_files(self):
        readme = (DATA_DIR / "README.md").read_text(encoding="utf-8")
        assert "Government Open Data License - India" in readme
        assert "public domain" in readme
        assert "SAMPLE" in readme
        for name in ("pincode_directory.json.gz", "bank_branches.json.gz"):
            with gzip.open(DATA_DIR / name, "rt", encoding="utf-8") as f:
                assert json.load(f)["sha256"] in readme
