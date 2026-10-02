"""Tests for POST / DELETE /projects/{id}/file-check/confirmations (no AWS calls).

The DynamoDB table is a fake. The stored data is handed to the real
file-check engine (packages/lambda/file-check-mcp/engine.py, loaded with the
Lambda by test_file_check) to pin the contract: what the route stores is what
the engine counts as a confirmed item.
"""

import copy
import datetime as dt
import inspect
import json
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

import app.ddb.file_check_confirmations as confirmations
from app.config import get_config
from app.main import app
from tests.test_file_check import (
    PROJECT_ID,
    FakeTable,
    HandlerLambda,
    _facts,
    _project,
    _to_ddb,
    canned_lambda,
    fc_index,
    project_items,
    rahul,
    use_lambda,
)

client = TestClient(app)
engine = fc_index.engine

HEADERS = {"x-user-id": "asha.verma"}
URL = f"/projects/{PROJECT_ID}/file-check/confirmations"
RAHUL_PAN = "BQXPD4821K"
RAHUL_IDS = [f["document_id"] for f in rahul()]
NOW = dt.datetime(2026, 10, 1, 8, 35, tzinfo=dt.UTC)


class ConfirmationTable:
    """(PK, SK) items: put, delete with ReturnValues=ALL_OLD."""

    def __init__(self):
        self.items = {}
        self.fail = None

    def put_item(self, Item):
        if self.fail:
            raise self.fail
        self.items[(Item["PK"], Item["SK"])] = copy.deepcopy(Item)

    def delete_item(self, Key, ReturnValues=None):
        if self.fail:
            raise self.fail
        old = self.items.pop((Key["PK"], Key["SK"]), None)
        assert ReturnValues == "ALL_OLD"
        return {"Attributes": old} if old else {}


@pytest.fixture
def table(monkeypatch):
    fake = ConfirmationTable()
    monkeypatch.setattr(confirmations, "get_table", lambda: fake)
    with patch("app.routers.file_check.get_project_item", return_value=_project()):
        yield fake


def _body(**overrides):
    return {
        "applicant": RAHUL_PAN,
        "item_id": "vlp_pl_02",
        "checklist_id": "vlp_pl_sal",
        "document_ids": RAHUL_IDS,
        **overrides,
    }


def _confirm(**overrides):
    return client.post(URL, headers=HEADERS, json=_body(**overrides))


def _undo(**body):
    return client.request("DELETE", URL, headers=HEADERS, json=body)


# ------------------------------------------------------------------ storage
class TestConfirm:
    def test_stores_who_and_when_with_the_retention_ttl_and_no_applicant_data(self, table):
        before = int(dt.datetime.now(dt.UTC).timestamp())
        response = _confirm()

        assert response.status_code == 200
        body = response.json()
        assert body["item_id"] == "vlp_pl_02" and body["checklist_id"] == "vlp_pl_sal"
        assert body["confirmed_by"] == "asha.verma"
        (item,) = table.items.values()
        key = engine.applicant_key(RAHUL_PAN)
        assert item["PK"] == f"PROJ#{PROJECT_ID}"
        assert item["SK"] == f"FCCONF#{key}#vlp_pl_02"
        data = item["data"]
        assert data == {
            "applicant_key": key,
            "item_id": "vlp_pl_02",
            "checklist_id": "vlp_pl_sal",
            "document_ids": sorted(RAHUL_IDS),
            "confirmed_by": "asha.verma",
            "confirmed_at": body["confirmed_at"],
            "expires_at": item["expires_at"],
        }
        retention = get_config().retention_days * 86400
        assert before + retention <= item["expires_at"] <= before + retention + 5
        assert dt.datetime.fromisoformat(body["expires_at"]).timestamp() == item["expires_at"]
        assert dt.datetime.fromisoformat(body["confirmed_at"]).tzinfo is not None
        # Neither the PAN nor the name is stored.
        text = json.dumps(item)
        assert RAHUL_PAN not in text and RAHUL_PAN.lower() not in text and "Rahul" not in text

    def test_confirming_again_replaces_and_the_pan_spelling_does_not_matter(self, table):
        assert _confirm().status_code == 200
        assert _confirm(applicant=" bqxpd 4821k ", checklist_id=None).status_code == 200
        (item,) = table.items.values()
        assert item["data"]["checklist_id"] is None
        # By name it is another key: the UI sends the verdict's PAN when it has one.
        assert _confirm(applicant="Rahul Vijay Deshmukh").status_code == 200
        assert len(table.items) == 2

    def test_logs_the_item_never_the_applicant(self, table, capsys):
        assert _confirm(applicant="Rahul Vijay Deshmukh").status_code == 200
        assert _undo(applicant="Rahul Vijay Deshmukh", item_id="vlp_pl_02").status_code == 200
        out = capsys.readouterr().out
        assert "item=vlp_pl_02" in out and "user=asha.verma" in out
        assert "Rahul" not in out and RAHUL_PAN not in out

    @pytest.mark.parametrize(
        "overrides",
        [
            {"document_ids": []},
            {"document_ids": ["  "]},
            {"document_ids": [f"d{i}" for i in range(501)]},
            {"item_id": "ss pl 02"},
            {"item_id": ""},
            {"applicant": "   "},
            {"applicant": "Rahul\nVijay"},
            {"applicant": "x" * 201},
            {"checklist_id": "VLP-PL"},
            {"confirmed_by": "someone.else"},  # who confirmed comes from x-user-id only
        ],
    )
    def test_rejects_invalid_requests(self, table, overrides):
        assert _confirm(**overrides).status_code == 422
        assert table.items == {}

    def test_requires_the_caller_id_and_the_project(self, table):
        assert client.post(URL, json=_body()).status_code == 422
        with patch("app.routers.file_check.get_project_item", return_value=None):
            assert _confirm().status_code == 404
            assert _undo(applicant=RAHUL_PAN, item_id="vlp_pl_02").status_code == 404
        assert table.items == {}

    def test_storage_failure_is_502(self, table):
        table.fail = ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "PutItem")
        response = _confirm()
        assert response.status_code == 502
        assert response.json() == {"detail": "The confirmation could not be stored"}
        assert _undo(applicant=RAHUL_PAN, item_id="vlp_pl_02").status_code == 502


class TestUndo:
    def test_deletes_the_confirmation_once(self, table):
        _confirm()
        _confirm(item_id="x02")
        response = _undo(applicant=" BQXPD4821K", item_id="vlp_pl_02")
        assert response.status_code == 200
        assert response.json() == {"item_id": "vlp_pl_02", "deleted": True}
        assert [k[1].rsplit("#", 1)[1] for k in table.items] == ["x02"]
        assert _undo(applicant=RAHUL_PAN, item_id="vlp_pl_02").json() == {"item_id": "vlp_pl_02", "deleted": False}

    def test_also_removes_a_confirmation_saved_under_the_name(self, table):
        """Saved by name before a PAN was read: the engine still applies it, so undo by PAN + name removes it."""
        assert _confirm(applicant="Rahul Vijay Deshmukh").status_code == 200
        assert _undo(applicant=RAHUL_PAN, item_id="vlp_pl_02").json()["deleted"] is False
        assert len(table.items) == 1
        response = _undo(applicant=RAHUL_PAN, applicant_name="rahul  vijay deshmukh", item_id="vlp_pl_02")
        assert response.json() == {"item_id": "vlp_pl_02", "deleted": True}
        assert table.items == {}
        # Under both keys: both go.
        _confirm()
        _confirm(applicant="Rahul Vijay Deshmukh")
        assert len(table.items) == 2
        response = _undo(applicant=RAHUL_PAN, applicant_name="Rahul Vijay Deshmukh", item_id="vlp_pl_02")
        assert response.json()["deleted"] is True
        assert table.items == {}

    def test_rejects_extra_fields_and_missing_ones(self, table):
        assert _undo(applicant=RAHUL_PAN).status_code == 422
        assert _undo(applicant=RAHUL_PAN, item_id="x02", document_ids=[]).status_code == 422
        assert _undo(applicant=RAHUL_PAN, applicant_name="Rahul\nVijay", item_id="x02").status_code == 422


# ------------------------------------------------------------------ contract with the engine
def test_backend_and_engine_compute_the_same_applicant_key():
    for applicant in (RAHUL_PAN, " bqxpd 4821k", "Rahul Vijay Deshmukh", "  RAHUL  vijay deshmukh ", "BQXPD48Z1K"):
        assert confirmations.applicant_key(applicant) == engine.applicant_key(applicant)
    assert confirmations.CONFIRMATION_SK_PREFIX == engine.CONFIRMATION_SK_PREFIX


def _stored_data(table):
    """The items' data as the Lambda reads it back (numbers as Decimal -> int)."""
    return [fc_index._from_decimal(json.loads(json.dumps(i["data"]))) for i in table.items.values()]


def test_stored_confirmations_make_a_brand_checklist_ready(table):
    """Rahul under Varunika Loan Partners PL (salaried) needs a person for 5 items; confirming them makes it READY."""
    catalog = engine.load_catalog()
    checklist = engine.get_checklist(catalog, "vlp_pl_sal")
    required = [i["id"] for i in checklist["items"] if i["rule"]["kind"] == "manual" and i["required"]]
    assert required == ["vlp_pl_02", "vlp_pl_e01", "vlp_pl_e04", "x02", "x05"]
    facts = rahul()

    before = engine.run_file_check(facts, checklist)["applicants"][0]
    assert before["verdict"] == "NOT READY"
    for item_id in required:
        assert _confirm(item_id=item_id).status_code == 200
    result = engine.run_file_check(facts, checklist, confirmations=_stored_data(table))
    (applicant,) = result["applicants"]
    assert applicant["verdict"] == "READY"
    rows = {r["item_id"]: r for r in applicant["checklist"]}
    assert {rows[i]["status"] for i in required} == {"CONFIRMED"}
    assert rows["vlp_pl_02"]["confirmation"]["confirmed_by"] == "asha.verma"
    assert [c["item_id"] for c in applicant["confirmed_items"]] == required

    # Undo one: back to NOT READY on that item only.
    assert _undo(applicant=RAHUL_PAN, item_id="x05").json()["deleted"] is True
    applicant = engine.run_file_check(facts, checklist, confirmations=_stored_data(table))["applicants"][0]
    assert applicant["verdict"] == "NOT READY"
    assert applicant["reasons"] == [
        "REVIEW – Cross-check: DOB match and age: cannot be verified automatically; review manually"
    ]


class ProjectPartition(FakeTable):
    """What the Lambda's Query on PROJ#{id} returns: the file's items plus the confirmations the route stored."""

    def __init__(self, items, stored: ConfirmationTable):
        super().__init__(items)
        self.stored = stored

    def query(self, **kwargs):
        return {"Items": self.items + [_to_ddb(i) for i in self.stored.items.values()]}


# The Lambda handler (index.py, outside this part) must hand the FCCONF# items
# to engine.run_file_check(confirmations=...); until it does, this end-to-end
# check is expected to fail (strict: it reports when the wiring lands).
_LAMBDA_LOADS_CONFIRMATIONS = "confirmations" in inspect.signature(fc_index.load_project_items).parameters


@pytest.mark.xfail(
    not _LAMBDA_LOADS_CONFIRMATIONS,
    strict=True,
    reason="index.py does not pass the project's FCCONF# items to the engine yet",
)
def test_confirm_and_undo_reach_the_lambdas_next_check(monkeypatch, table):
    """Web app flow: confirm the 5 review items, check again (READY, who and when), undo one (NOT READY)."""
    monkeypatch.setattr(get_config(), "file_check_function_name", "idp-v2-file-check-mcp")
    monkeypatch.setattr(fc_index, "_table", ProjectPartition(project_items(rahul()), table))

    def check():
        response = client.post(
            f"/projects/{PROJECT_ID}/file-check", headers=HEADERS, json={"checklist_id": "vlp_pl_sal"}
        )
        assert response.status_code == 200
        (applicant,) = response.json()["applicants"]
        return applicant

    with use_lambda(HandlerLambda()):
        assert check()["verdict"] == "NOT READY"
        for item_id in ("vlp_pl_02", "vlp_pl_e01", "vlp_pl_e04", "x02", "x05"):
            assert _confirm(item_id=item_id).status_code == 200
        applicant = check()
        assert applicant["verdict"] == "READY"
        row = next(r for r in applicant["checklist"] if r["item_id"] == "x02")
        assert row["status"] == "CONFIRMED"
        assert row["confirmation"]["confirmed_by"] == "asha.verma"
        assert _undo(applicant=RAHUL_PAN, item_id="x02").json()["deleted"] is True
        assert check()["verdict"] == "NOT READY"


def test_run_file_check_returns_confirmations_and_recordings(monkeypatch, table):
    """The response model passes the engine's CONFIRMED rows, confirmed_items and recording_documents."""
    monkeypatch.setattr(get_config(), "file_check_function_name", "idp-v2-file-check-mcp")
    checklist = engine.get_checklist(engine.load_catalog(), "vlp_pl_sal")
    _confirm(item_id="vlp_pl_02")
    pdf = {"file_type": "application/pdf", "status": "completed"}
    documents = [{"document_id": f["document_id"], "name": f["document_name"], **pdf} for f in rahul()]
    documents.append({"document_id": "c-01", "name": "call_sneha.wav", "file_type": "audio/wav", "status": "completed"})
    payload = engine.run_file_check(
        rahul(), checklist, documents=documents, confirmations=_stored_data(table), project_id=PROJECT_ID
    )
    with use_lambda(canned_lambda(payload)):
        response = client.post(
            f"/projects/{PROJECT_ID}/file-check", headers=HEADERS, json={"checklist_id": "vlp_pl_sal"}
        )

    assert response.status_code == 200
    data = response.json()
    assert data["recording_documents"] == [
        {"document_id": "c-01", "document_name": "call_sneha.wav", "file_type": "audio/wav"}
    ]
    (applicant,) = data["applicants"]
    row = next(r for r in applicant["checklist"] if r["item_id"] == "vlp_pl_02")
    assert row["status"] == "CONFIRMED" and row["ok"] is True
    assert row["confirmation"]["confirmed_by"] == "asha.verma"
    assert applicant["confirmed_items"][0]["item"] == "Address Proof (Passport/Utility Bill)"
    other = next(r for r in applicant["checklist"] if r["item_id"] == "x02")
    assert other["status"] == "REVIEW" and other.get("confirmation") is None


def test_call_project_through_the_lambda_lists_recordings(monkeypatch):
    """A project of call recordings: no applicants, no 'no facts' rows, a summary that points to Call QA."""
    monkeypatch.setattr(get_config(), "file_check_function_name", "idp-v2-file-check-mcp")
    skipped = {**_facts("c", "call_sneha_2026-09-29.wav", "other"), "status": "skipped", "reason": "media"}
    items = [i for i in project_items([skipped]) if not i["SK"].startswith("DOC#")]
    items.append(
        {
            "PK": f"PROJ#{PROJECT_ID}",
            "SK": f"DOC#{skipped['document_id']}",
            "data": {
                "document_id": skipped["document_id"],
                "project_id": PROJECT_ID,
                "name": "call_sneha_2026-09-29.wav",
                "file_type": "audio/wav",
                "status": "completed",
            },
        }
    )
    monkeypatch.setattr(fc_index, "_table", FakeTable(items))
    with (
        patch("app.routers.file_check.get_project_item", return_value=_project()),
        use_lambda(HandlerLambda()),
    ):
        response = client.post(f"/projects/{PROJECT_ID}/file-check", headers=HEADERS, json={})

    assert response.status_code == 200
    data = response.json()
    assert data["applicants"] == [] and data["no_facts_documents"] == []
    assert [d["document_name"] for d in data["recording_documents"]] == ["call_sneha_2026-09-29.wav"]
    assert data["summary"] == (
        "No loan documents in this project, only 1 call recording: the file check applies to loan files; "
        "review calls with Call QA"
    )


def test_put_confirmation_uses_the_given_clock(table):
    data = confirmations.put_confirmation(
        PROJECT_ID,
        applicant=RAHUL_PAN,
        item_id="x02",
        checklist_id=None,
        document_ids=["b", "a", "a"],
        confirmed_by="rohan.iyer",
        retention_days=7,
        now=NOW,
    )
    assert data["confirmed_at"] == "2026-10-01T08:35:00.000000+00:00"
    assert data["expires_at"] == int(NOW.timestamp()) + 7 * 86400
    assert data["document_ids"] == ["a", "b"]
    (item,) = table.items.values()
    assert item["expires_at"] == data["expires_at"]
