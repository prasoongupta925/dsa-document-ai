"""Tests for the branch finder and reference-list API (app/routers/branches.py), no AWS calls.

The router runs on a test app; in-memory tables stand in for DynamoDB (the project's META item
in one, the uploaded lists in the other, so the lists can fail on their own). The branches come
from the shipped public data and SAMPLE rows; the uploaded lists are made up.
"""

import datetime as dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routers.branches as branches_router
from app import eligibility, reference_data
from app.config import get_config
from tests.test_reference_data import FakeTable, csv_bytes

branches_app = FastAPI()
branches_app.include_router(branches_router.router)
client = TestClient(branches_app)

HEADERS = {"x-user-id": "dsa-user"}
PROJECT_ID = "proj_demo"
BASE = f"/projects/{PROJECT_ID}/eligibility"
NOW = dt.datetime(2026, 10, 2, 10, 0, tzinfo=dt.UTC)
META = {
    "PK": f"PROJ#{PROJECT_ID}",
    "SK": "META",
    "data": {"project_id": PROJECT_ID, "name": "Demo", "description": "", "status": "active", "language": "en"},
    "created_at": "2026-10-01T00:00:00+00:00",
    "updated_at": "2026-10-01T00:00:00+00:00",
}

SERVICEABILITY = csv_bytes("Bank,Pin Code,Status", "HDFC Bank,400603,yes", "Bajaj Finance,400606,no")
BRANCH_LIST = csv_bytes("lender,branch,pincode,city", "Bajaj Finance,Thane Station Road,400608,Thane")


@pytest.fixture
def tables(monkeypatch):
    projects, lists = FakeTable([META]), FakeTable()
    monkeypatch.setattr("app.ddb.projects.get_table", lambda: projects)
    monkeypatch.setattr(reference_data, "get_table", lambda: lists)
    monkeypatch.setattr(branches_router, "_now", lambda: NOW)
    monkeypatch.setattr(get_config(), "retention_days", 7)
    reference_data.reset_cache()
    yield projects, lists
    reference_data.reset_cache()


def upload(kind: str, data: bytes, filename: str = "list.csv", content_type: str = "text/csv"):
    return client.post(
        f"{BASE}/reference-data?kind={kind}&filename={filename}",
        content=data,
        headers={**HEADERS, "Content-Type": content_type},
    )


def find(pincode: str = "400606", lenders: str | None = "HDFC Bank,Bajaj Finance"):
    query = f"pincode={pincode}" + (f"&lenders={lenders}" if lenders is not None else "")
    return client.get(f"{BASE}/branches?{query}", headers=HEADERS)


def lender_row(body: dict, lender: str) -> dict:
    return next(row for row in body["lenders"] if row["lender"] == lender)


def test_the_routes_are_registered_on_the_app():
    from app.main import app

    paths = app.openapi()["paths"]
    assert set(paths["/projects/{project_id}/eligibility/branches"]) == {"get"}
    assert set(paths["/projects/{project_id}/eligibility/reference-data"]) == {"get", "post"}
    assert set(paths["/projects/{project_id}/eligibility/reference-data/{kind}"]) == {"delete"}
    # The upload documents both bodies it takes.
    upload_body = paths["/projects/{project_id}/eligibility/reference-data"]["post"]["requestBody"]
    assert set(upload_body["content"]) == {"text/csv", "multipart/form-data"}


# ------------------------------------------------------------------ GET .../branches
class TestFindBranches:
    def test_answers_per_lender_with_the_nearest_branches_and_the_sources(self, tables):
        response = find()
        assert response.status_code == 200
        body = response.json()
        assert body["pincode"] == "400606"
        assert body["place"]["office"] == "Jekegram"
        assert [row["lender"] for row in body["lenders"]] == ["HDFC Bank", "Bajaj Finance"]
        hdfc = lender_row(body, "HDFC Bank")
        assert (hdfc["serviceable"], hdfc["source"]) == (True, "public_data")
        assert hdfc["branches"][0]["distance_km"] == 0.0
        assert hdfc["branches"][0]["ifsc"].startswith("HDFC0")
        bajaj = lender_row(body, "Bajaj Finance")
        assert bajaj["source"] == "sample"
        assert all(b["name"].endswith("(sample)") for b in bajaj["branches"])
        assert {"Government Open Data License - India (GODL-India)"} <= {s["licence"] for s in body["sources"]}
        assert body["notes"] == []

    def test_every_policy_lender_when_none_is_asked(self, tables):
        body = find(lenders=None).json()
        assert [row["lender"] for row in body["lenders"]] == [
            lender.name for lender in eligibility.load_policy_book().lenders
        ]

    def test_lenders_are_asked_once_each_in_order_and_at_most_twenty(self, tables):
        body = find(lenders="ICICI Bank, hdfc bank ,HDFC BANK,,ICICI Bank").json()
        assert [row["lender"] for row in body["lenders"]] == ["ICICI Bank", "hdfc bank"]
        names = ",".join(f"Example Lender {n}" for n in range(21))
        response = find(lenders=names)
        assert response.status_code == 400
        assert response.json() == {"detail": "At most 20 lenders"}

    @pytest.mark.parametrize("pincode", ["40060", "012345", "4006061", "40060a"])
    def test_a_pincode_must_be_six_digits(self, tables, pincode):
        assert find(pincode=pincode).status_code == 422

    def test_an_unknown_project(self, tables):
        response = client.get("/projects/proj_other/eligibility/branches?pincode=400606", headers=HEADERS)
        assert response.status_code == 404

    def test_the_caller_header_is_required(self, tables):
        assert client.get(f"{BASE}/branches?pincode=400606").status_code == 422

    def test_uploaded_lists_come_first(self, tables):
        assert upload("pincode_serviceability", SERVICEABILITY, "service.csv").status_code == 200
        assert upload("lender_branches", BRANCH_LIST, "branches.csv").status_code == 200
        body = find().json()

        hdfc = lender_row(body, "HDFC Bank")
        assert (hdfc["serviceable"], hdfc["source"], hdfc["serviceable_source"]) == (False, "dsa_list", "dsa_list")
        assert hdfc["branches_source"] == "public_data"
        bajaj = lender_row(body, "Bajaj Finance")
        assert (bajaj["serviceable"], bajaj["source"]) == (False, "dsa_list")
        assert [(b["name"], b["city"]) for b in bajaj["branches"]] == [("Thane Station Road", "Thane")]
        names = [s["name"] for s in body["sources"]]
        assert "Your pincode serviceability list (service.csv, uploaded 02 Oct 2026)" in names
        assert "Your lender branches list (branches.csv, uploaded 02 Oct 2026)" in names

    def test_the_public_data_answers_when_the_lists_cannot_be_read(self, tables):
        _, lists = tables
        lists.fail = True
        response = find()
        assert response.status_code == 200
        body = response.json()
        assert body["notes"][0] == (
            "Your uploaded lists could not be read just now: this answer uses the public and sample data"
        )
        assert lender_row(body, "HDFC Bank")["source"] == "public_data"

    def test_the_applicants_pincode_is_not_logged(self, tables, capsys):
        find(pincode="400603")
        out = capsys.readouterr().out
        assert "branches user=dsa-user project=proj_demo lenders=2" in out
        assert "400603" not in out


# ------------------------------------------------------------------ POST .../reference-data
class TestUpload:
    def test_a_csv_body_replaces_the_list_and_says_when_it_is_deleted(self, tables):
        response = upload("pincode_serviceability", SERVICEABILITY, "service.csv")
        assert response.status_code == 200
        assert response.json() == {
            "kind": "pincode_serviceability",
            "label": "Pincode serviceability",
            "description": reference_data.KIND_DESCRIPTIONS["pincode_serviceability"],
            "columns": ["lender", "pincode"],
            "optional_columns": ["serviceable"],
            "uploaded": True,
            "filename": "service.csv",
            "rows": 2,
            "lenders": ["HDFC Bank", "Bajaj Finance"],
            "uploaded_at": "2026-10-02T10:00:00+00:00",
            "expires_at": "2026-10-09T10:00:00+00:00",
            "duplicates": 0,
            "notes": [],
        }

    def test_the_web_apps_octet_stream_upload(self, tables):
        response = upload("lender_branches", BRANCH_LIST, content_type="application/octet-stream")
        assert response.status_code == 200
        assert response.json()["rows"] == 1

    def test_a_multipart_form_with_the_kind_in_it(self, tables):
        response = client.post(
            f"{BASE}/reference-data",
            files={"file": ("C:\\fakepath\\my branches.csv", BRANCH_LIST, "text/csv")},
            data={"kind": "lender_branches"},
            headers=HEADERS,
        )
        assert response.status_code == 200
        assert (response.json()["kind"], response.json()["filename"]) == ("lender_branches", "my branches.csv")

    def test_the_answer_lists_what_to_check(self, tables):
        data = csv_bytes("lender,branch,pincode", "Axis Bank,Nowhere,999999")
        notes = upload("lender_branches", data).json()["notes"]
        assert notes == [
            "1 pincode is not in the India Post directory (999999): those branches cannot be placed, so they are "
            "never shown as nearest"
        ]

    def test_the_kind_is_required(self, tables):
        response = client.post(
            f"{BASE}/reference-data", content=BRANCH_LIST, headers={**HEADERS, "Content-Type": "text/csv"}
        )
        assert response.status_code == 400
        assert response.json()["detail"] == (
            "kind must be one of: pincode_serviceability, lender_branches, company_categories"
        )
        assert upload("branch_list", BRANCH_LIST).status_code == 422

    def test_a_bad_file_is_refused_with_every_problem(self, tables):
        data = csv_bytes("lender,pincode,serviceable", "HDFC Bank,4006,yes", "HDFC Bank,400606,perhaps")
        response = upload("pincode_serviceability", data)
        assert response.status_code == 400
        assert response.json() == {
            "detail": {
                "message": "The file has 2 problems",
                "errors": [
                    "Row 2: the pincode '4006' is not 6 digits",
                    "Row 3: serviceable is 'perhaps': use yes or no",
                ],
            }
        }
        _, lists = tables
        assert lists.items == {}

    def test_not_a_csv(self, tables):
        response = upload("lender_branches", b'{"rows": []}', content_type="application/json")
        assert response.status_code == 415
        response = client.post(
            f"{BASE}/reference-data?kind=lender_branches",
            files={"other": ("list.csv", BRANCH_LIST, "text/csv")},
            headers=HEADERS,
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Send the CSV file as the 'file' part of the form"

    def test_too_large(self, tables, monkeypatch):
        monkeypatch.setattr(reference_data, "MAX_UPLOAD_BYTES", 30)
        assert upload("lender_branches", BRANCH_LIST).status_code == 413
        response = client.post(
            f"{BASE}/reference-data?kind=lender_branches",
            files={"file": ("list.csv", BRANCH_LIST, "text/csv")},
            headers=HEADERS,
        )
        assert response.status_code == 413

    def test_an_unknown_project(self, tables):
        response = client.post(
            "/projects/proj_other/eligibility/reference-data?kind=lender_branches",
            content=BRANCH_LIST,
            headers={**HEADERS, "Content-Type": "text/csv"},
        )
        assert response.status_code == 404

    def test_a_storage_failure(self, tables):
        _, lists = tables
        lists.fail = True
        response = upload("lender_branches", BRANCH_LIST)
        assert response.status_code == 502
        assert response.json() == {"detail": "The list could not be saved"}


# ------------------------------------------------------------------ GET / DELETE .../reference-data
class TestListsAndDelete:
    def test_lists_every_kind_with_its_columns_and_upload(self, tables):
        upload("lender_branches", BRANCH_LIST, "branches.csv")
        response = client.get(f"{BASE}/reference-data", headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert (body["retention_days"], body["max_upload_bytes"], body["max_rows"]) == (7, 4 * 1024 * 1024, 100_000)
        lists = {item["kind"]: item for item in body["lists"]}
        assert list(lists) == list(reference_data.KINDS)
        assert lists["lender_branches"]["uploaded"] is True
        assert lists["lender_branches"]["filename"] == "branches.csv"
        assert lists["lender_branches"]["rows"] == 1
        assert lists["lender_branches"]["expires_at"] == "2026-10-09T10:00:00+00:00"
        assert lists["lender_branches"]["optional_columns"] == ["address", "city", "district", "state", "ifsc"]
        assert lists["company_categories"] == {
            "kind": "company_categories",
            "label": "Company categories",
            "description": reference_data.KIND_DESCRIPTIONS["company_categories"],
            "columns": ["lender", "company", "category"],
            "optional_columns": [],
            "uploaded": False,
            "filename": None,
            "rows": 0,
            "lenders": [],
            "uploaded_at": None,
            "expires_at": None,
            "duplicates": None,
            "notes": [],
        }

    def test_delete_brings_back_the_public_and_sample_data(self, tables):
        upload("lender_branches", BRANCH_LIST)
        response = client.delete(f"{BASE}/reference-data/lender_branches", headers=HEADERS)
        assert response.status_code == 200
        assert response.json() == {"kind": "lender_branches", "deleted": True}
        assert lender_row(find().json(), "Bajaj Finance")["source"] == "sample"
        again = client.delete(f"{BASE}/reference-data/lender_branches", headers=HEADERS)
        assert again.json() == {"kind": "lender_branches", "deleted": False}
        assert client.delete(f"{BASE}/reference-data/branches", headers=HEADERS).status_code == 422

    def test_storage_failures(self, tables):
        _, lists = tables
        lists.fail = True
        assert client.get(f"{BASE}/reference-data", headers=HEADERS).status_code == 502
        assert client.delete(f"{BASE}/reference-data/lender_branches", headers=HEADERS).status_code == 502

    def test_an_unknown_project(self, tables):
        assert client.get("/projects/proj_other/eligibility/reference-data", headers=HEADERS).status_code == 404
