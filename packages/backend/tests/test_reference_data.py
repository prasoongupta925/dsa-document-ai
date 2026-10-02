"""Tests for the DSA's own reference lists (app/reference_data.py): CSV parsing and storage.

No AWS calls: a small in-memory table stands in for DynamoDB (pages of `page_size` items, so
the paging loops run). Synthetic company names only; the lenders are the policy file's.
"""

import copy
import datetime as dt
import json

import pytest
from botocore.exceptions import ClientError

from app import branches, eligibility, reference_data
from app.config import get_config
from app.reference_data import CsvError, parse_csv

NOW = dt.datetime(2026, 10, 2, 10, 0, tzinfo=dt.UTC)
WEEK = 7 * 86400
PROJECT_ID = "proj_demo"
PK = f"PROJ#{PROJECT_ID}"


def csv_bytes(*lines: str, encoding: str = "utf-8") -> bytes:
    return ("\r\n".join(lines) + "\r\n").encode(encoding)


def refused(kind: str, data: bytes) -> CsvError:
    with pytest.raises(CsvError) as info:
        parse_csv(kind, data)
    return info.value


# ------------------------------------------------------------------ fake table
def _matches(condition, item) -> bool:
    expr = condition.get_expression()
    op, values = expr["operator"], expr["values"]
    if op == "AND":
        return _matches(values[0], item) and _matches(values[1], item)
    value = item.get(values[0].name)
    if op == "=":
        return value == values[1]
    if op == "begins_with":
        return isinstance(value, str) and value.startswith(values[1])
    raise AssertionError(f"unsupported key condition {op}")


class FakeTable:
    """(PK, SK) items: get / put / delete and key-condition queries returned `page_size` at a time."""

    def __init__(self, items=(), page_size: int = 1000):
        self.items = {(i["PK"], i["SK"]): copy.deepcopy(i) for i in items}
        self.page_size = page_size
        self.queries = 0
        self.fail = False

    def _check(self, operation: str) -> None:
        if self.fail:
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, operation)

    def get_item(self, Key, ConsistentRead=None):
        self._check("GetItem")
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": copy.deepcopy(item)} if item else {}

    def put_item(self, Item):
        self._check("PutItem")
        self.items[(Item["PK"], Item["SK"])] = copy.deepcopy(Item)

    def delete_item(self, Key):
        self._check("DeleteItem")
        self.items.pop((Key["PK"], Key["SK"]), None)

    def query(self, KeyConditionExpression, ProjectionExpression=None, ConsistentRead=None, ExclusiveStartKey=None):
        self._check("Query")
        self.queries += 1
        rows = sorted((i for i in self.items.values() if _matches(KeyConditionExpression, i)), key=lambda i: i["SK"])
        if ExclusiveStartKey:
            rows = [r for r in rows if r["SK"] > ExclusiveStartKey["SK"]]
        page = rows[: self.page_size]
        if ProjectionExpression:
            names = [n.strip() for n in ProjectionExpression.split(",")]
            page = [{n: r[n] for n in names} for r in page]
        out = {"Items": copy.deepcopy(page)}
        if len(rows) > self.page_size:
            out["LastEvaluatedKey"] = {"PK": page[-1]["PK"], "SK": page[-1]["SK"]}
        return out

    def of(self, prefix: str) -> list[dict]:
        return [i for (_, sk), i in sorted(self.items.items()) if sk.startswith(prefix)]


@pytest.fixture
def table(monkeypatch):
    fake = FakeTable()
    monkeypatch.setattr(reference_data, "get_table", lambda: fake)
    monkeypatch.setattr(get_config(), "retention_days", 7)
    reference_data.reset_cache()
    yield fake
    reference_data.reset_cache()


# ------------------------------------------------------------------ pincode_serviceability
class TestServiceabilityCsv:
    def test_reads_lender_pincode_and_serviceable(self):
        parsed = parse_csv(
            "pincode_serviceability",
            csv_bytes(
                "lender,pincode,serviceable", "HDFC Bank,400606,yes", "HDFC Bank,400603,No", "Bajaj Finance,400615,"
            ),
        )
        assert parsed.kind == "pincode_serviceability"
        assert parsed.rows == [["HDFC Bank", "400606", 1], ["HDFC Bank", "400603", 0], ["Bajaj Finance", "400615", 1]]
        assert parsed.lenders == ["HDFC Bank", "Bajaj Finance"]
        assert parsed.duplicates == 0
        assert parsed.notes == []

    def test_serviceable_column_is_optional_and_listed_means_yes(self):
        parsed = parse_csv("pincode_serviceability", csv_bytes("Lender,Pincode", "ICICI Bank,400606"))
        assert parsed.rows == [["ICICI Bank", "400606", 1]]

    def test_crm_style_headers_in_any_order_case_and_delimiter(self):
        """A CRM export: other column names, other order, semicolons, a BOM, Excel's sep= line."""
        data = "﻿sep=;\r\nPIN CODE;Status;Bank Name;Remarks\r\n400 606;Active;Axis Bank;new\r\n".encode()
        parsed = parse_csv("pincode_serviceability", data)
        assert parsed.rows == [["Axis Bank", "400606", 1]]

    def test_tabs_utf16_and_spreadsheet_numbers(self):
        data = "lender\tpincode\tserviceable\nTata Capital\t400603.0\tY\n".encode("utf-16")
        parsed = parse_csv("pincode_serviceability", data)
        assert parsed.rows == [["Tata Capital", "400603", 1]]

    def test_windows_1252_text_is_read(self):
        data = csv_bytes("lender,pincode", "Crédit Exemple,400606", encoding="cp1252")
        assert parse_csv("pincode_serviceability", data).lenders == ["Crédit Exemple"]

    def test_identical_rows_are_dropped_and_counted(self):
        parsed = parse_csv(
            "pincode_serviceability",
            csv_bytes(
                "lender,pincode,serviceable", "HDFC Bank,400606,yes", "HDFC Bank,400606,y", ",,", "HDFC,400606,1"
            ),
        )
        assert parsed.rows == [["HDFC Bank", "400606", 1]]
        assert parsed.duplicates == 2

    def test_a_contradiction_refuses_the_file_even_under_another_name(self):
        error = refused(
            "pincode_serviceability",
            csv_bytes("lender,pincode,serviceable", "HDFC Bank,400606,yes", "HDFC Bank Ltd,400606,no"),
        )
        assert error.message == "The file has 1 problem"
        assert error.errors == ["Row 3: it contradicts row 2 (HDFC Bank Ltd, 400606)"]

    def test_every_bad_row_is_listed_and_nothing_is_kept(self):
        error = refused(
            "pincode_serviceability",
            csv_bytes(
                "lender,pincode,serviceable",
                "HDFC Bank,400606,yes",
                "HDFC Bank,40060,yes",
                "ICICI Bank,400606,maybe",
                ",400606,yes",
            ),
        )
        assert error.message == "The file has 3 problems"
        assert error.errors == [
            "Row 3: the pincode '40060' is not 6 digits",
            "Row 4: serviceable is 'maybe': use yes or no",
            "Row 5: the lender is empty",
        ]

    def test_at_most_twenty_problems_are_listed(self):
        rows = [f"HDFC Bank,{n},yes" for n in range(30)]
        error = refused("pincode_serviceability", csv_bytes("lender,pincode,serviceable", *rows))
        assert error.message == "The file has 30 problems (the first 20 are listed)"
        assert len(error.errors) == reference_data.MAX_ERRORS == 20

    def test_a_missing_column_names_the_columns_needed_and_found(self):
        error = refused("pincode_serviceability", csv_bytes("bank,city", "HDFC Bank,Thane"))
        assert error.message == (
            "The file has no pincode column: a pincode serviceability list needs lender, pincode "
            "(optional: serviceable)"
        )
        assert error.errors == ["Columns found: bank, city"]

    def test_unknown_pincodes_and_lenders_are_accepted_with_notes(self):
        assert "999999" not in branches.load_directory()
        parsed = parse_csv(
            "pincode_serviceability",
            csv_bytes("lender,pincode", "HDFC Bank,400606", "HDFC Bank,999999", "Example Credit Co-op,400606"),
        )
        assert len(parsed.rows) == 3
        assert parsed.notes == [
            "1 pincode is not in the India Post directory (999999): check for typos",
            "Not lenders of the eligibility policies, so used only when asked for by name: Example Credit Co-op",
        ]


# ------------------------------------------------------------------ lender_branches
class TestBranchesCsv:
    def test_reads_every_column_and_cleans_the_ifsc(self):
        parsed = parse_csv(
            "lender_branches",
            csv_bytes(
                "Lender,Branch Name,Pin Code,Address,City,District,State,IFSC Code",
                'HDFC Bank,Thane West,400606,"Shop 4, Station Road",Thane,Thane,Maharashtra,hdfc 0003959',
                "Bajaj Finance,Kasarvadavali,400615,,,,,",
            ),
        )
        assert parsed.rows == [
            [
                "HDFC Bank",
                "Thane West",
                "400606",
                "Shop 4, Station Road",
                "Thane",
                "Thane",
                "Maharashtra",
                "HDFC0003959",
            ],
            ["Bajaj Finance", "Kasarvadavali", "400615", None, None, None, None, None],
        ]
        assert parsed.notes == []

    def test_bad_branch_rows_are_listed(self):
        error = refused(
            "lender_branches",
            csv_bytes("lender,branch,pincode,ifsc", "HDFC Bank,,400606,", "HDFC Bank,Thane,400606,HDFC123"),
        )
        assert error.errors == [
            "Row 2: the branch name is empty",
            "Row 3: the IFSC 'HDFC123' is not 11 characters like HDFC0001234",
        ]

    def test_text_is_trimmed_and_control_characters_dropped(self):
        parsed = parse_csv("lender_branches", csv_bytes("lender,branch,pincode", "  Axis Bank ,Thane\x07  East,400603"))
        assert parsed.rows[0][:3] == ["Axis Bank", "Thane East", "400603"]

    def test_a_branch_the_directory_cannot_place_gets_a_note(self):
        parsed = parse_csv("lender_branches", csv_bytes("lender,branch,pincode", "Axis Bank,Nowhere,999999"))
        assert parsed.notes == [
            "1 pincode is not in the India Post directory (999999): those branches cannot be placed, so they are "
            "never shown as nearest"
        ]


# ------------------------------------------------------------------ company_categories
class TestCompanyCategoriesCsv:
    def test_reads_lender_company_and_category(self):
        book = eligibility.load_policy_book()
        icici = book.lender("icici_bank")
        category = next(iter(icici.company_categories))
        parsed = parse_csv(
            "company_categories",
            csv_bytes("lender,company,category", f"ICICI,Example Tech Private Limited,{category.lower()}"),
        )
        assert parsed.rows == [["icici_bank", "Example Tech Private Limited", category]]
        assert parsed.lenders == ["ICICI Bank"]
        assert parsed.notes == []

    def test_the_lender_and_category_must_be_in_the_policy_file(self):
        book = eligibility.load_policy_book()
        hdfc = book.lender("hdfc_bank")
        error = refused(
            "company_categories",
            csv_bytes(
                "employer name,bank,cat",
                "Example Tech Pvt Ltd,Example Credit Co-op,CAT A",
                "Example Tech Pvt Ltd,HDFC Bank,CAT Z",
                ",HDFC Bank,CAT A",
            ),
        )
        lenders = ", ".join(lender.name for lender in book.lenders)
        assert error.errors == [
            f"Row 2: 'Example Credit Co-op' is not a lender of the policy file ({lenders})",
            f"Row 3: HDFC Bank has no category 'CAT Z' (its categories: {', '.join(hdfc.company_categories)})",
            "Row 4: the company is empty",
        ]

    def test_the_same_company_twice_must_agree(self):
        categories = list(eligibility.load_policy_book().lender("icici_bank").company_categories)
        error = refused(
            "company_categories",
            csv_bytes(
                "lender,company,category",
                f"ICICI Bank,Example Tech Private Limited,{categories[0]}",
                f"ICICI Bank,EXAMPLE TECH PVT. LTD.,{categories[1]}",
            ),
        )
        assert error.errors == ["Row 3: it contradicts row 2 (ICICI Bank)"]


# ------------------------------------------------------------------ files that are not lists
class TestNotAList:
    def test_an_excel_workbook_is_refused_with_how_to_save_as_csv(self):
        error = refused("lender_branches", b"PK\x03\x04" + b"\x00" * 40)
        assert "save it as CSV" in error.message

    @pytest.mark.parametrize("data", [b"", b"\r\n\r\n", b" , ,\r\n"])
    def test_an_empty_file(self, data):
        assert refused("lender_branches", data).message == "The file is empty"

    def test_a_header_without_rows(self):
        assert refused("lender_branches", csv_bytes("lender,branch,pincode", ",,")).message == (
            "The file has a header but no rows"
        )

    def test_too_large_or_too_many_rows(self, monkeypatch):
        monkeypatch.setattr(reference_data, "MAX_UPLOAD_BYTES", 40)
        assert refused("pincode_serviceability", b"lender,pincode\r\n" + b"x" * 40).message.startswith(
            "The file is over"
        )
        monkeypatch.setattr(reference_data, "MAX_UPLOAD_BYTES", 4 * 1024 * 1024)
        monkeypatch.setattr(reference_data, "MAX_ROWS", 2)
        rows = ["HDFC Bank,400606", "HDFC Bank,400608", "HDFC Bank,400602"]
        assert refused("pincode_serviceability", csv_bytes("lender,pincode", *rows)).message == (
            "The file has over 2 rows"
        )


# ------------------------------------------------------------------ storage
SERVICEABILITY = csv_bytes(
    "lender,pincode,serviceable", "HDFC Bank,400606,no", "HDFC Bank,400603,yes", "Bajaj Finance,400606,yes"
)
BRANCH_LIST = csv_bytes(
    "lender,branch,pincode,city",
    "Bajaj Finance,Thane Station Road,400606,Thane",
    "Bajaj Finance,Hiranandani Estate,400615,Kasarvadavali",
    "Example Credit Co-op,Thane Market,400608,Thane",
)


class TestStorage:
    def test_save_writes_chunks_then_a_header_that_expires_in_seven_days(self, table):
        parsed = parse_csv("pincode_serviceability", SERVICEABILITY)
        stored = reference_data.save(PROJECT_ID, parsed, "serviceability.csv", NOW)

        header = table.items[(PK, "REFDATA#pincode_serviceability")]
        assert header == {
            "PK": PK,
            "SK": "REFDATA#pincode_serviceability",
            "kind": "pincode_serviceability",
            "upload_id": stored.upload_id,
            "filename": "serviceability.csv",
            "row_count": 3,
            "chunks": 1,
            "lenders": ["HDFC Bank", "Bajaj Finance"],
            "uploaded_at": "2026-10-02T10:00:00+00:00",
            "expires_at": int(NOW.timestamp()) + WEEK,
        }
        chunks = table.of(f"REFDATA#pincode_serviceability#{stored.upload_id}#")
        assert [c["SK"][-4:] for c in chunks] == ["0000"]
        assert json.loads(chunks[0]["rows"]) == parsed.rows
        assert chunks[0]["expires_at"] == int(NOW.timestamp()) + WEEK

    def test_the_retention_period_comes_from_the_config(self, table, monkeypatch):
        monkeypatch.setattr(get_config(), "retention_days", 3)
        stored = reference_data.save(PROJECT_ID, parse_csv("lender_branches", BRANCH_LIST), None, NOW)
        assert stored.expires_at == int(NOW.timestamp()) + 3 * 86400
        assert all(item["expires_at"] == stored.expires_at for item in table.items.values())

    def test_load_reads_the_rows_back_in_order_across_chunks_and_pages(self, table, monkeypatch):
        monkeypatch.setattr(reference_data, "CHUNK_BYTES", 60)
        table.page_size = 2
        parsed = parse_csv("lender_branches", BRANCH_LIST)
        stored = reference_data.save(PROJECT_ID, parsed, "branches.csv", NOW)
        assert table.items[(PK, "REFDATA#lender_branches")]["chunks"] == 3
        reference_data.reset_cache()

        loaded = reference_data.load(PROJECT_ID, "lender_branches", NOW)
        assert loaded.upload_id == stored.upload_id
        assert loaded.rows == parsed.rows
        assert (loaded.filename, loaded.lenders) == ("branches.csv", ["Bajaj Finance", "Example Credit Co-op"])

    def test_a_loaded_list_is_cached_until_the_next_upload(self, table):
        reference_data.save(PROJECT_ID, parse_csv("pincode_serviceability", SERVICEABILITY), None, NOW)
        reference_data.reset_cache()
        first = reference_data.load(PROJECT_ID, "pincode_serviceability", NOW)
        queries = table.queries
        assert reference_data.load(PROJECT_ID, "pincode_serviceability", NOW) is first
        assert table.queries == queries

        replacement = csv_bytes("lender,pincode", "Axis Bank,400601")
        reference_data.save(PROJECT_ID, parse_csv("pincode_serviceability", replacement), None, NOW)
        assert reference_data.load(PROJECT_ID, "pincode_serviceability", NOW).rows == [["Axis Bank", "400601", 1]]

    def test_a_new_upload_replaces_the_list_and_removes_the_old_rows(self, table, monkeypatch):
        # One row per chunk and one item per page: the old chunks are removed page by page.
        monkeypatch.setattr(reference_data, "CHUNK_BYTES", 60)
        table.page_size = 1
        first = reference_data.save(PROJECT_ID, parse_csv("lender_branches", BRANCH_LIST), None, NOW)
        assert len(table.of(f"REFDATA#lender_branches#{first.upload_id}#")) == 3
        replacement = csv_bytes("lender,branch,pincode", "Tata Capital,Thane Station,400601")
        second = reference_data.save(PROJECT_ID, parse_csv("lender_branches", replacement), None, NOW)

        assert table.of(f"REFDATA#lender_branches#{first.upload_id}#") == []
        assert len(table.of(f"REFDATA#lender_branches#{second.upload_id}#")) == 1
        assert table.items[(PK, "REFDATA#lender_branches")]["upload_id"] == second.upload_id
        reference_data.reset_cache()
        assert reference_data.load(PROJECT_ID, "lender_branches", NOW).lenders == ["Tata Capital"]

    def test_the_cache_keeps_the_latest_lists_only(self, table):
        parsed = parse_csv("pincode_serviceability", SERVICEABILITY)
        for n in range(20):
            reference_data.save(f"proj_{n}", parsed, None, NOW)
        cached = [key[0] for key in reference_data._cache]
        assert cached == [f"proj_{n}" for n in range(20 - reference_data._CACHE_SIZE, 20)]
        for n in (0, 19):
            reference_data.load(f"proj_{n}", "pincode_serviceability", NOW)
        assert [key[0] for key in reference_data._cache][-2:] == ["proj_0", "proj_19"]
        assert len(reference_data._cache) == reference_data._CACHE_SIZE

    def test_each_kind_is_its_own_list(self, table):
        reference_data.save(PROJECT_ID, parse_csv("pincode_serviceability", SERVICEABILITY), None, NOW)
        assert reference_data.load(PROJECT_ID, "lender_branches", NOW) is None
        status = reference_data.status(PROJECT_ID, NOW)
        assert list(status) == list(reference_data.KINDS)
        assert status["pincode_serviceability"]["row_count"] == 3
        assert status["lender_branches"] is None and status["company_categories"] is None

    def test_an_expired_list_is_gone_even_before_dynamodb_removes_it(self, table):
        reference_data.save(PROJECT_ID, parse_csv("pincode_serviceability", SERVICEABILITY), None, NOW)
        later = NOW + dt.timedelta(days=7)
        assert reference_data.load(PROJECT_ID, "pincode_serviceability", later) is None
        assert reference_data.status(PROJECT_ID, later)["pincode_serviceability"] is None
        assert reference_data.serviceable(PROJECT_ID, "HDFC Bank", "400606", later) is None

    def test_delete_removes_the_header_and_the_rows(self, table):
        reference_data.save(PROJECT_ID, parse_csv("lender_branches", BRANCH_LIST), None, NOW)
        assert reference_data.delete(PROJECT_ID, "lender_branches", NOW) is True
        assert table.of("REFDATA#") == []
        assert reference_data.load(PROJECT_ID, "lender_branches", NOW) is None
        assert reference_data.delete(PROJECT_ID, "lender_branches", NOW) is False

    def test_storage_errors_reach_the_caller(self, table):
        table.fail = True
        with pytest.raises(ClientError):
            reference_data.save(PROJECT_ID, parse_csv("lender_branches", BRANCH_LIST), None, NOW)
        with pytest.raises(ClientError):
            reference_data.own_lists(PROJECT_ID, NOW)


class TestLookups:
    def test_serviceability_by_lender_name_or_id(self, table):
        reference_data.save(PROJECT_ID, parse_csv("pincode_serviceability", SERVICEABILITY), None, NOW)
        assert reference_data.serviceable(PROJECT_ID, "HDFC Bank", "400603", NOW) is True
        assert reference_data.serviceable(PROJECT_ID, "hdfc_bank", "400606", NOW) is False
        # Listed lender, unlisted pincode: not serviceable; unlisted lender: the list says nothing.
        assert reference_data.serviceable(PROJECT_ID, "HDFC", "400001", NOW) is False
        assert reference_data.serviceable(PROJECT_ID, "ICICI Bank", "400606", NOW) is None

    def test_company_category_by_company_name_variant(self, table):
        category = next(iter(eligibility.load_policy_book().lender("axis_bank").company_categories))
        data = csv_bytes("lender,company,category", f"Axis Bank,Example Tech Private Limited,{category}")
        reference_data.save(PROJECT_ID, parse_csv("company_categories", data), None, NOW)
        assert reference_data.company_category(PROJECT_ID, "axis_bank", "Example Tech Pvt. Ltd.", NOW) == category
        assert reference_data.company_category(PROJECT_ID, "icici_bank", "Example Tech Pvt. Ltd.", NOW) is None
        assert reference_data.company_category(PROJECT_ID, "axis_bank", "Other Example Co", NOW) is None
        assert reference_data.company_category(PROJECT_ID, "axis_bank", None, NOW) is None

    def test_no_list_means_no_answer(self, table):
        assert reference_data.company_category(PROJECT_ID, "axis_bank", "Example Tech", NOW) is None
        own = reference_data.own_lists(PROJECT_ID, NOW)
        assert (own.serviceable, own.branches, own.serviceable_source, own.branches_source) == ({}, {}, None, None)

    def test_own_lists_feed_the_branch_finder_with_their_sources(self, table):
        reference_data.save(PROJECT_ID, parse_csv("pincode_serviceability", SERVICEABILITY), "service.csv", NOW)
        reference_data.save(PROJECT_ID, parse_csv("lender_branches", BRANCH_LIST), "branches.csv", NOW)
        own = reference_data.own_lists(PROJECT_ID, NOW)

        assert own.serviceable["hdfc_bank"] == {"400606": False, "400603": True}
        assert sorted(own.branches["bajaj_finance"]) == ["400606", "400615"]
        # A lender the app does not know is matched by its name.
        assert branches.match_key("Example Credit Co-op").startswith("name:")
        assert own.branches[branches.match_key("Example Credit Co-op")]["400608"][0].name == "Thane Market"
        assert own.serviceable_source == {
            "name": "Your pincode serviceability list (service.csv, uploaded 02 Oct 2026)",
            "licence": "Your own data, deleted on 09 Oct 2026",
            "url": None,
        }
        assert own.branches_source["name"] == "Your lender branches list (branches.csv, uploaded 02 Oct 2026)"

    def test_a_stored_list_answers_only_for_its_kind(self, table):
        stored = reference_data.save(PROJECT_ID, parse_csv("lender_branches", BRANCH_LIST), None, NOW)
        with pytest.raises(ValueError):
            stored.serviceable("HDFC Bank", "400606")
        with pytest.raises(ValueError):
            stored.company_category("hdfc_bank", "Example Tech")
        assert stored.source()["name"] == "Your lender branches list (uploaded 02 Oct 2026)"
