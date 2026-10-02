"""Tests for POST /projects/{project_id}/applicants/erase (DPDP right to erasure; no AWS calls).

The router is mounted on a test app (the integrator adds it to app.main).
One stateful fake DynamoDB table backs both the backend and the real
file-check Lambda handler, which runs in-process (as in test_file_check.py), so
the applicant is resolved by the real engine and the documents are deleted by
the real DELETE /documents/{id} code. S3, the LanceDB Lambda and SQS are fakes
that record their calls.
"""

import copy
import datetime as dt
import io
import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.ddb.client as ddb_client
import app.routers.applicants as applicants
import app.routers.documents as documents
from app.config import get_config
from app.ddb.file_check_confirmations import CONFIRMATION_SK_PREFIX, confirmation_key
from app.routers.eligibility import INPUTS_SK_PREFIX, applicant_key
from tests.test_file_check import (
    FUNCTION_NAME,
    HEADERS,
    PROJECT_ID,
    HandlerLambda,
    _to_ddb,
    canned_lambda,
    fc_index,
    rahul,
    sneha,
    use_lambda,
)

erase_app = FastAPI()
erase_app.include_router(applicants.router)
client = TestClient(erase_app)

ERASE = f"/projects/{PROJECT_ID}/applicants/erase"
BUCKET = "doc-bucket"
QUEUE_URL = "https://sqs.ap-south-1.amazonaws.com/000000000000/graph-delete"
NOW = "2026-09-28T00:00:00+00:00"
RAHUL, RAHUL_PAN = "Rahul Vijay Deshmukh", "BQXPD4821K"
SNEHA_NAME = "Sneha Anil Kulkarni"


# ------------------------------------------------------------------ fakes
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
    """Base table keyed on (PK, SK): get / put / delete, key-condition queries, batch writer."""

    def __init__(self, items=()):
        self.items = {(i["PK"], i["SK"]): i for i in items}
        self.deletes = []
        self.updates = []
        self.fail_delete = set()  # (PK, SK) keys whose delete raises

    def get_item(self, Key):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def put_item(self, Item):
        self.items[(Item["PK"], Item["SK"])] = Item

    def update_item(self, Key, UpdateExpression, ConditionExpression=None, ExpressionAttributeValues=None):
        """The subset the erase uses on delivery log items: REMOVE applicant / SET applicant = :applicant."""
        key = (Key["PK"], Key["SK"])
        self.updates.append((key, UpdateExpression))
        assert ConditionExpression == "attribute_exists(PK)"
        if key not in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if UpdateExpression == "REMOVE applicant":
            self.items[key].pop("applicant", None)
        else:
            assert UpdateExpression == "SET applicant = :applicant"
            self.items[key]["applicant"] = ExpressionAttributeValues[":applicant"]

    def delete_item(self, Key):
        key = (Key["PK"], Key["SK"])
        if key in self.fail_delete:
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "DeleteItem")
        self.deletes.append(key)
        self.items.pop(key, None)

    def query(self, **kwargs):
        condition = kwargs["KeyConditionExpression"]
        return {"Items": [i for _, i in sorted(self.items.items()) if _matches(condition, i)]}

    def batch_writer(self):
        table = self

        class _Batch:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def delete_item(self, Key):
                table.delete_item(Key=Key)

        return _Batch()

    def has(self, pk, sk):
        return (pk, sk) in self.items

    def erase_items(self):
        return [i for (_, sk), i in self.items.items() if sk.startswith("ERASE#")]


class FakeResource:
    def __init__(self, table):
        self.table = table

    def Table(self, name):  # noqa: N802 - boto3's name
        return self.table


class FakeS3:
    def __init__(self, keys):
        self.keys = set(keys)

    def delete_object(self, Bucket, Key):
        assert Bucket == BUCKET
        self.keys.discard(Key)

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        s3 = self

        class _Paginator:
            def paginate(self, Bucket, Prefix):
                assert Bucket == BUCKET
                return [{"Contents": [{"Key": k} for k in sorted(s3.keys) if k.startswith(Prefix)]}]

        return _Paginator()

    def delete_objects(self, Bucket, Delete):
        assert Bucket == BUCKET
        for obj in Delete["Objects"]:
            self.keys.discard(obj["Key"])


class FakeLanceDb:
    """The LanceDB service Lambda: delete_by_workflow answers 200 unless the workflow is in `fail`."""

    def __init__(self):
        self.calls = []
        self.fail = set()

    def invoke(self, FunctionName, InvocationType, Payload):
        body = json.loads(Payload)
        self.calls.append(body)
        workflow_id = body["params"]["workflow_id"]
        ok = workflow_id not in self.fail
        answer = {"statusCode": 200, "success": True} if ok else {"statusCode": 500, "error": "boom"}
        return {"Payload": io.BytesIO(json.dumps(answer).encode("utf-8"))}


# ------------------------------------------------------------------ project data
def _doc_id(fact):
    return fact["document_id"]


def _workflow_id(fact):
    return f"wf-{_doc_id(fact)}"


def _s3_key(fact):
    return f"projects/{PROJECT_ID}/documents/{_doc_id(fact)}/{_doc_id(fact)}.pdf"


def project_items(facts):
    """META, then per document: DOC#, FACTS#, its workflow (DOC#/WF#) and the workflow's STEP."""
    meta = {"project_id": PROJECT_ID, "name": "Demo", "description": "", "status": "active"}
    items = [{"PK": f"PROJ#{PROJECT_ID}", "SK": "META", "data": meta, "created_at": NOW, "updated_at": NOW}]
    for f in facts:
        did, wid = _doc_id(f), _workflow_id(f)
        doc = {
            "document_id": did,
            "project_id": PROJECT_ID,
            "name": f["document_name"],
            "file_type": "application/pdf",
            "file_size": 1024,
            "status": "completed",
            "s3_key": _s3_key(f),
        }
        workflow = {
            "execution_arn": f"arn:aws:states:ap-south-1:000000000000:execution:idp:{wid}",
            "file_name": f["document_name"],
            "file_type": "application/pdf",
            "file_uri": f"s3://{BUCKET}/{_s3_key(f)}",
            "project_id": PROJECT_ID,
            "status": "completed",
        }
        stamps = {"created_at": NOW, "updated_at": NOW}
        items += [
            {"PK": f"PROJ#{PROJECT_ID}", "SK": f"DOC#{did}", "data": _to_ddb(doc), **stamps},
            {"PK": f"PROJ#{PROJECT_ID}", "SK": f"FACTS#{did}", "data": _to_ddb(f), **stamps},
            {"PK": f"DOC#{did}", "SK": f"WF#{wid}", "data": workflow, **stamps},
            {"PK": f"WF#{wid}", "SK": "STEP", "data": {"current_step": "done"}},
        ]
    return items


def s3_keys(facts):
    keys = []
    for f in facts:
        prefix = f"projects/{PROJECT_ID}/documents/{_doc_id(f)}/"
        keys += [_s3_key(f), f"{prefix}analysis/segment_0000.json", f"{prefix}analysis/facts.json"]
    return keys


def by_name(facts):
    return sorted(facts, key=lambda f: f["document_name"])


class World:
    def __init__(self, facts):
        self.facts = facts
        self.table = FakeTable(project_items(facts))
        self.s3 = FakeS3(s3_keys(facts))
        self.lancedb = FakeLanceDb()
        self.sqs = MagicMock()
        self.file_check = HandlerLambda()

    def document_items(self, fact):
        did, wid = _doc_id(fact), _workflow_id(fact)
        keys = [
            (f"PROJ#{PROJECT_ID}", f"DOC#{did}"),
            (f"PROJ#{PROJECT_ID}", f"FACTS#{did}"),
            (f"DOC#{did}", f"WF#{wid}"),
            (f"WF#{wid}", "STEP"),
        ]
        return [k for k in keys if self.table.has(*k)]

    def document_objects(self, fact):
        return [k for k in self.s3.keys if k.startswith(f"projects/{PROJECT_ID}/documents/{_doc_id(fact)}/")]


@pytest.fixture
def world(monkeypatch):
    """Rahul (7 documents) and Sneha (5) in one project; every store faked and shared."""
    config = get_config()
    monkeypatch.setattr(config, "file_check_function_name", FUNCTION_NAME)
    monkeypatch.setattr(config, "document_storage_bucket_name", BUCKET)
    monkeypatch.setattr(config, "lancedb_function_name", "idp-v2-lancedb-service")
    monkeypatch.setattr(config, "graph_delete_queue_url", QUEUE_URL)
    monkeypatch.setattr(config, "retention_days", 7)

    w = World(rahul() + sneha())
    monkeypatch.setattr(ddb_client, "_ddb_resource", FakeResource(w.table))
    monkeypatch.setattr(fc_index, "_table", w.table)  # the Lambda reads the same table

    def boto3_client(name, **kwargs):
        assert name == "sqs", f"unexpected boto3 client {name}"
        return w.sqs

    with (
        use_lambda(w.file_check),
        patch("app.routers.documents.get_s3_client", return_value=w.s3),
        patch("app.s3.get_s3_client", return_value=w.s3),
        patch("app.lancedb.get_lambda_client", return_value=w.lancedb),
        patch("boto3.client", side_effect=boto3_client),
    ):
        yield w


def ids(facts):
    return [_doc_id(f) for f in facts]


def _erase(applicant=RAHUL_PAN, confirm=RAHUL, headers=HEADERS, document_ids=None):
    """POST the erase with the documents the verdict shows under Rahul, unless told otherwise."""
    body = {
        "applicant": applicant,
        "confirm": confirm,
        "document_ids": ids(rahul()) if document_ids is None else document_ids,
    }
    return client.post(ERASE, headers=headers, json=body)


def delivery(n, applicant=None, project_id=PROJECT_ID):
    item = {
        "PK": f"PROJ#{project_id}",
        "SK": f"WHDLV#2026-09-30T10:00:0{n}.000000+00:00#d-{n}",
        "delivery_id": f"d-{n}",
        "event": "file_check.completed",
        "status": "delivered",
    }
    if applicant is not None:
        item["applicant"] = applicant
    return item


# ------------------------------------------------------------------ happy path
class TestErase:
    def test_erases_every_document_of_the_applicant_and_nothing_else(self, world):
        before = dt.datetime.now(dt.UTC)
        response = _erase(RAHUL_PAN, confirm="  rahul   VIJAY deshmukh ")

        assert response.status_code == 200
        data = response.json()
        assert set(data) == {
            "applicant",
            "documents_deleted",
            "failed",
            "delivery_log_redacted",
            "eligibility_inputs_deleted",
            "not_erased",
            "erased_at",
        }
        assert data["applicant"] == RAHUL
        assert data["documents_deleted"] == [
            {"document_id": _doc_id(f), "name": f["document_name"]} for f in by_name(rahul())
        ]
        assert data["failed"] == []
        erased_at = dt.datetime.fromisoformat(data["erased_at"])
        assert before <= erased_at <= dt.datetime.now(dt.UTC)

        # The applicant was resolved by the file-check Lambda's read-only tool.
        assert world.file_check.tool_names == ["filecheck___applicant_documents"]
        assert json.loads(world.file_check.calls[0]["Payload"]) == {"project_id": PROJECT_ID, "applicant": RAHUL_PAN}

        # Every store of each of Rahul's documents is empty; Sneha's are untouched.
        for f in rahul():
            assert world.document_items(f) == [], f["document_name"]
            assert world.document_objects(f) == [], f["document_name"]
        for f in sneha():
            assert len(world.document_items(f)) == 4
            assert len(world.document_objects(f)) == 3
        assert world.table.has(f"PROJ#{PROJECT_ID}", "META")

        # LanceDB vectors and the graph of each workflow (the DELETE /documents/{id} cleanup).
        rahul_workflows = sorted(_workflow_id(f) for f in rahul())
        assert sorted(c["params"]["workflow_id"] for c in world.lancedb.calls) == rahul_workflows
        assert {c["action"] for c in world.lancedb.calls} == {"delete_by_workflow"}
        bodies = [json.loads(c.kwargs["MessageBody"]) for c in world.sqs.send_message.call_args_list]
        assert sorted(b["workflow_id"] for b in bodies) == rahul_workflows
        assert {(b["project_id"], b["phase"]) for b in bodies} == {(PROJECT_ID, "clusters")}
        assert {c.kwargs["QueueUrl"] for c in world.sqs.send_message.call_args_list} == {QUEUE_URL}

    def test_what_is_not_erased_is_stated(self, world):
        data = _erase().json()

        assert data["delivery_log_redacted"] == 0
        assert data["not_erased"] == [
            "Chat conversations and artifacts that mention the applicant: delete them in the chat and artifact "
            "lists, or the retention sweep deletes them after 7 days",
            "Verdicts and login requests the CRM webhook already delivered: erase them in the CRM",
        ]

    def test_saved_eligibility_inputs_are_erased_too(self, world):
        """The CIBIL page's inputs (PAN, mobile, DOB, income, loans) go with the documents, by PAN or name."""

        def saved(identifier, name, pan=None):
            return {
                "PK": f"PROJ#{PROJECT_ID}",
                "SK": f"{INPUTS_SK_PREFIX}{applicant_key(identifier)}",
                "applicant": identifier,
                "inputs": {"profile": {"name": name, "pan": pan, "mobile": "9820012345"}},
                "expires_at": 2_000_000_000,
            }

        for item in (saved(RAHUL_PAN, RAHUL, RAHUL_PAN), saved(RAHUL, RAHUL), saved(SNEHA_NAME, SNEHA_NAME)):
            world.table.put_item(Item=item)

        data = _erase().json()

        assert data["eligibility_inputs_deleted"] == 2
        left = [i["applicant"] for (_, sk), i in world.table.items.items() if sk.startswith(INPUTS_SK_PREFIX)]
        assert left == [SNEHA_NAME]
        assert not any("eligibility" in line for line in data["not_erased"])
        (audit,) = world.table.erase_items()
        assert audit["eligibility_inputs_deleted"] == 2

    def test_a_failed_eligibility_erase_is_stated(self, world):
        failure = ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "Query")
        with patch("app.routers.applicants.erase_applicant_eligibility", side_effect=failure):
            response = _erase()

        assert response.status_code == 200  # the documents are still erased
        data = response.json()
        assert data["eligibility_inputs_deleted"] is None
        assert data["not_erased"][-1] == (
            "The applicant's saved eligibility inputs (CIBIL page): they are deleted automatically "
            "7 days after they were first saved"
        )
        assert len(data["documents_deleted"]) == 7

    def test_confirmed_review_items_are_erased_too(self, world, capsys):
        """Who confirmed which needs-review item goes with the documents: under the PAN or the name,
        or made on one of the applicant's documents (under a PAN the erase is not given)."""

        def confirmation(identifier, item_id, document_ids):
            key = confirmation_key(PROJECT_ID, identifier, item_id)
            data = {"item_id": item_id, "document_ids": document_ids, "confirmed_by": "asha.verma"}
            return {**key, "data": data, "expires_at": 2_000_000_000}

        rahul_doc, sneha_doc = ids(rahul())[0], ids(sneha())[0]
        items = [
            confirmation(RAHUL_PAN, "address_proof", [rahul_doc]),
            confirmation(RAHUL, "age", [rahul_doc]),
            confirmation("ZZZZZ9999Z", "pan_format", [rahul_doc]),
            confirmation(SNEHA_NAME, "address_proof", [sneha_doc]),
        ]
        for item in items:
            world.table.put_item(Item=item)

        data = _erase().json()

        left = [sk for (_, sk) in world.table.items if sk.startswith(CONFIRMATION_SK_PREFIX)]
        assert left == [confirmation_key(PROJECT_ID, SNEHA_NAME, "address_proof")["SK"]]
        assert not any("needs-review" in line for line in data["not_erased"])
        (audit,) = world.table.erase_items()
        assert audit["review_confirmations_deleted"] == 3
        out = capsys.readouterr().out
        assert "review_confirmations_deleted=3" in out
        for secret in (RAHUL, RAHUL_PAN, "asha.verma"):
            assert secret not in out, secret

    def test_a_failed_confirmation_erase_is_stated(self, world):
        failure = ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "Query")
        with patch("app.routers.applicants.delete_applicant_confirmations", side_effect=failure):
            response = _erase()

        assert response.status_code == 200  # the documents are still erased
        data = response.json()
        assert data["not_erased"][-1] == (
            "Who confirmed the applicant's needs-review items in the file check: they are deleted automatically "
            "7 days after they were confirmed"
        )
        assert len(data["documents_deleted"]) == 7
        (audit,) = world.table.erase_items()
        assert audit["review_confirmations_deleted"] == 0

    def test_erase_by_name_and_the_second_erase_finds_nobody(self, world):
        first = _erase("Rahul V. Deshmukh", confirm=RAHUL)
        assert first.status_code == 200
        assert len(first.json()["documents_deleted"]) == 7

        second = _erase(RAHUL_PAN, confirm=RAHUL)
        assert second.status_code == 404
        assert second.json() == {"detail": "Applicant not found in this project"}
        assert len(world.table.erase_items()) == 1  # only the erasure that happened

    def test_every_document_goes_through_the_documents_delete_endpoint_code(self, world):
        assert applicants.delete_document is documents.delete_document
        with patch("app.routers.applicants.delete_document", wraps=documents.delete_document) as shared:
            response = _erase()

        assert response.status_code == 200
        assert [c.args for c in shared.call_args_list] == [(PROJECT_ID, _doc_id(f)) for f in by_name(rahul())]

    def test_audit_item_holds_counts_only(self, world, capsys):
        response = _erase()
        assert response.status_code == 200

        (item,) = world.table.erase_items()
        assert set(item) == {
            "PK",
            "SK",
            "documents_matched",
            "documents_deleted",
            "documents_failed",
            "delivery_log_redacted",
            "eligibility_inputs_deleted",
            "review_confirmations_deleted",
            "expires_at",
        }
        assert item["PK"] == f"PROJ#{PROJECT_ID}"
        prefix, stamp, token = item["SK"].split("#")
        assert prefix == "ERASE" and len(token) == 32
        erased_at = dt.datetime.fromisoformat(stamp)
        assert erased_at == dt.datetime.fromisoformat(response.json()["erased_at"])
        assert (item["documents_matched"], item["documents_deleted"], item["documents_failed"]) == (7, 7, 0)
        assert item["expires_at"] == int(erased_at.timestamp()) + 7 * 86400  # DynamoDB TTL, 7 days

        # No personal data: no name, PAN, document id or name, and not the caller either.
        text = json.dumps(item, default=str)
        secrets = [RAHUL, "Rahul", "Deshmukh", RAHUL_PAN, HEADERS["x-user-id"]]
        secrets += [_doc_id(f) for f in rahul()] + [f["document_name"] for f in rahul()]
        for secret in secrets:
            assert secret not in text, secret

        out = capsys.readouterr().out
        assert "deleted=7 failed=0" in out
        for secret in (RAHUL, "Rahul", "Deshmukh", RAHUL_PAN, "821K"):
            assert secret not in out, secret

    def test_audit_expiry_follows_the_retention_setting(self, world, monkeypatch):
        monkeypatch.setattr(get_config(), "retention_days", 3)
        assert _erase().status_code == 200
        (item,) = world.table.erase_items()
        erased_at = dt.datetime.fromisoformat(item["SK"].split("#")[1])
        assert item["expires_at"] == int(erased_at.timestamp()) + 3 * 86400

    def test_audit_write_failure_still_answers(self, world, capsys):
        world.table.put_item = MagicMock(side_effect=ClientError({"Error": {"Code": "AccessDenied"}}, "PutItem"))
        response = _erase()
        assert response.status_code == 200
        assert len(response.json()["documents_deleted"]) == 7
        assert "audit write failed" in capsys.readouterr().out


# ------------------------------------------------------------------ what the user confirmed
class TestConfirmedDocuments:
    """The grouping runs again at request time: nothing is deleted unless it is what the user confirmed."""

    def test_a_document_that_joined_the_applicant_since_the_check_is_409(self, world):
        late = copy.deepcopy(rahul()[2])
        late.update(document_id="r-late", document_name="08_salary_slip_2026-05_may.pdf")
        late["fields"]["month"] = "2026-05"
        for item in project_items([late]):
            world.table.put_item(item)

        response = _erase()  # confirms the 7 documents the verdict showed

        assert response.status_code == 409
        assert response.json() == {
            "detail": (
                "The applicant's documents changed since the check (8 now, 7 confirmed): run the file check "
                "again and review them before erasing"
            )
        }
        _nothing_deleted(world)

    @pytest.mark.parametrize(
        "document_ids",
        [
            ids(rahul())[:-1],  # one left out (it left the group, or the user never saw it)
            ids(rahul()) + [ids(sneha())[0]],  # someone else's document
            ids(sneha()),
            ["no-such-document"],
        ],
    )
    def test_any_other_set_is_409_and_nothing_is_deleted(self, world, document_ids):
        response = _erase(document_ids=document_ids)

        assert response.status_code == 409
        _nothing_deleted(world)

    def test_order_and_repeats_do_not_matter(self, world):
        document_ids = list(reversed(ids(rahul()))) + ids(rahul())[:2]
        assert _erase(document_ids=document_ids).status_code == 200

    @pytest.mark.parametrize(
        "document_ids",
        [[], [""], ["  "], ["x" * 257], [f"d{n}" for n in range(501)], "r-01", None],
    )
    def test_invalid_document_ids_are_422(self, world, document_ids):
        body = {"applicant": RAHUL_PAN, "confirm": RAHUL, "document_ids": document_ids}
        response = client.post(ERASE, headers=HEADERS, json=body)

        assert response.status_code == 422
        assert world.file_check.calls == []
        _nothing_deleted(world)


# ------------------------------------------------------------------ webhook delivery log
class TestDeliveryLog:
    def test_the_name_is_removed_from_the_delivery_log(self, world, capsys):
        for item in (
            delivery(1, RAHUL),
            delivery(2, f"Sneha Anil Kulkarni, {RAHUL}"),
            delivery(3, "rahul  vijay DESHMUKH"),  # the same name, other spacing and case
            delivery(4, "Sneha Anil Kulkarni"),
            delivery(5),  # a test event: no applicant
            delivery(6, RAHUL, project_id="proj_other"),
        ):
            world.table.put_item(item)

        data = _erase().json()

        assert data["delivery_log_redacted"] == 3
        log = {sk: item for (pk, sk), item in world.table.items.items() if sk.startswith("WHDLV#")}
        by_id = {item["delivery_id"]: item for item in log.values() if item["PK"] == f"PROJ#{PROJECT_ID}"}
        assert "applicant" not in by_id["d-1"]
        assert by_id["d-2"]["applicant"] == "Sneha Anil Kulkarni"
        assert "applicant" not in by_id["d-3"]
        assert by_id["d-4"]["applicant"] == "Sneha Anil Kulkarni"
        assert "applicant" not in by_id["d-5"]
        # The entries themselves stay (status, time); another project's log is not touched.
        assert set(by_id) == {"d-1", "d-2", "d-3", "d-4", "d-5"}
        assert world.table.items[("PROJ#proj_other", delivery(6)["SK"])]["applicant"] == RAHUL
        (audit,) = world.table.erase_items()
        assert audit["delivery_log_redacted"] == 3
        assert RAHUL not in capsys.readouterr().out

    def test_an_entry_deleted_by_ttl_meanwhile_is_not_recreated(self, world):
        world.table.put_item(delivery(1, RAHUL))
        real_query = world.table.query

        def query_then_ttl(**kwargs):
            page = real_query(**kwargs)
            if kwargs.get("ProjectionExpression") == "PK, SK, applicant":  # the delivery log query
                world.table.items.pop((f"PROJ#{PROJECT_ID}", delivery(1)["SK"]), None)
            return page

        world.table.query = query_then_ttl
        data = _erase().json()

        assert data["delivery_log_redacted"] == 0
        assert (f"PROJ#{PROJECT_ID}", delivery(1)["SK"]) not in world.table.items

    def test_a_failed_redaction_does_not_fail_the_erase_and_is_stated(self, world, capsys):
        world.table.put_item(delivery(1, RAHUL))
        world.table.update_item = MagicMock(
            side_effect=ClientError({"Error": {"Code": "AccessDeniedException"}}, "UpdateItem")
        )

        response = _erase()

        assert response.status_code == 200
        data = response.json()
        assert len(data["documents_deleted"]) == 7
        assert data["delivery_log_redacted"] is None
        assert data["not_erased"][-1] == "The applicant's name in the webhook delivery log: it expires after 7 days"
        assert "delivery log not redacted" in capsys.readouterr().out


# ------------------------------------------------------------------ partial failure
class TestPartialFailure:
    def test_a_failing_document_is_reported_and_the_others_are_deleted(self, world):
        slip = by_name(rahul())[2]
        world.table.fail_delete.add((f"PROJ#{PROJECT_ID}", f"DOC#{_doc_id(slip)}"))

        response = _erase()

        assert response.status_code == 200
        data = response.json()
        assert data["failed"] == [
            {
                "document_id": _doc_id(slip),
                "name": slip["document_name"],
                "error": "delete failed (ProvisionedThroughputExceededException)",
            }
        ]
        assert [d["document_id"] for d in data["documents_deleted"]] == [
            _doc_id(f) for f in by_name(rahul()) if _doc_id(f) != _doc_id(slip)
        ]
        for f in rahul():
            if _doc_id(f) != _doc_id(slip):
                assert world.document_items(f) == []
        assert world.table.has(f"PROJ#{PROJECT_ID}", f"DOC#{_doc_id(slip)}")  # can be erased again
        (item,) = world.table.erase_items()
        assert (item["documents_matched"], item["documents_deleted"], item["documents_failed"]) == (7, 6, 1)

    def test_search_index_failure_makes_the_erasure_incomplete(self, world):
        statement = next(f for f in rahul() if f["doc_type"] == "bank_statement")
        world.lancedb.fail.add(_workflow_id(statement))

        data = _erase().json()

        (failed,) = data["failed"]
        assert failed["document_id"] == _doc_id(statement)
        assert failed["error"] == "document deleted, but its search-index (LanceDB) entries were not deleted"
        assert len(data["documents_deleted"]) == 6

    def test_unexpected_errors_do_not_stop_the_erase(self, world):
        first = by_name(rahul())[0]

        def flaky(project_id, document_id):
            if document_id == _doc_id(first):
                raise RuntimeError("secret BQXPD4821K")
            return documents.delete_document(project_id, document_id)

        with patch("app.routers.applicants.delete_document", side_effect=flaky):
            data = _erase().json()

        assert data["failed"] == [
            {"document_id": _doc_id(first), "name": first["document_name"], "error": "delete failed (RuntimeError)"}
        ]
        assert len(data["documents_deleted"]) == 6

    def test_unsafe_document_id_from_the_lookup_is_never_deleted(self, world):
        payload = {
            "project_id": PROJECT_ID,
            "applicant_name": RAHUL,
            "pan_masked": "XXXXXX821K",
            "documents": [{"document_id": "../other-project", "name": "x.pdf"}],
            "matches": 1,
        }
        with (
            use_lambda(canned_lambda(payload)),
            patch("app.routers.applicants.delete_document") as shared,
        ):
            data = _erase(document_ids=["../other-project"]).json()

        shared.assert_not_called()
        assert data["failed"] == [{"document_id": "../other-project", "name": "x.pdf", "error": "invalid document id"}]
        assert data["documents_deleted"] == []


# ------------------------------------------------------------------ refusals (nothing deleted)
def _nothing_deleted(world):
    assert world.table.deletes == []
    assert world.table.erase_items() == []
    assert world.lancedb.calls == []


class TestRefusals:
    def test_unknown_project_is_404(self, world):
        del world.table.items[(f"PROJ#{PROJECT_ID}", "META")]
        response = _erase()
        assert response.status_code == 404
        assert response.json() == {"detail": "Project not found"}
        assert world.file_check.calls == []
        _nothing_deleted(world)

    @pytest.mark.parametrize("applicant", ["Priya Sharma", "ZZZZZ9999Z"])
    def test_unknown_applicant_is_404(self, world, applicant):
        response = _erase(applicant, confirm=applicant)
        assert response.status_code == 404
        assert response.json() == {"detail": "Applicant not found in this project"}
        _nothing_deleted(world)

    @pytest.mark.parametrize("confirm", ["Sneha Anil Kulkarni", "Rahul Deshmukh", RAHUL_PAN, "yes"])
    def test_confirm_must_be_the_resolved_name(self, world, confirm):
        response = _erase(RAHUL_PAN, confirm=confirm)
        assert response.status_code == 400
        assert response.json() == {"detail": "confirm does not match the applicant's name"}
        _nothing_deleted(world)

    def test_a_name_matching_two_applicants_is_409(self, world, monkeypatch):
        namesakes = []
        for prefix, name, pan in (("o", "Amit Patil", "AAAPZ1111Q"), ("t", "Amit S. Patil", "CCCPD2222R")):
            for doc_type in ("identity_details", "salary_slip"):
                fact = rahul()[1]
                fact.update(document_id=f"{prefix}-{doc_type}", document_name=f"{prefix}_{doc_type}.pdf")
                fact.update(doc_type=doc_type, fields={"applicant_name": name, "pan": pan})
                namesakes.append(fact)
        for item in project_items(namesakes):
            world.table.put_item(item)

        response = _erase("Amit Patil", confirm="Amit Patil", document_ids=["o-identity_details", "o-salary_slip"])

        assert response.status_code == 409
        assert "2 applicants match this name" in response.json()["detail"]
        _nothing_deleted(world)
        # the PAN still picks one of them
        response = _erase("CCCPD2222R", confirm="amit s. patil", document_ids=["t-identity_details", "t-salary_slip"])
        assert response.status_code == 200
        deleted = [d["document_id"] for d in response.json()["documents_deleted"]]
        assert deleted == ["t-identity_details", "t-salary_slip"]

    @pytest.mark.parametrize(
        "body",
        [
            {"applicant": RAHUL_PAN},
            {"confirm": RAHUL},
            {"applicant": "", "confirm": RAHUL},
            {"applicant": "   ", "confirm": RAHUL},
            {"applicant": RAHUL_PAN, "confirm": "  "},
            {"applicant": RAHUL_PAN, "confirm": RAHUL, "document_ids": ["r-01"], "force": True},
            {"applicant": RAHUL_PAN, "confirm": RAHUL},  # no document_ids
            {"applicant": "x" * 201, "confirm": RAHUL},
        ],
    )
    def test_invalid_body_is_422(self, world, body):
        response = client.post(ERASE, headers=HEADERS, json=body)
        assert response.status_code == 422
        assert world.file_check.calls == []
        _nothing_deleted(world)

    def test_missing_user_header_is_422(self, world):
        response = _erase(headers={})
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["header", "x-user-id"]
        _nothing_deleted(world)

    def test_invalid_project_id_is_422(self, world):
        response = client.post(
            "/projects/proj%0Abad/applicants/erase",
            headers=HEADERS,
            json={"applicant": "x", "confirm": "x", "document_ids": ["d"]},
        )
        assert response.status_code == 422
        _nothing_deleted(world)


class TestLookupErrors:
    def test_not_configured_is_503(self, world, monkeypatch):
        monkeypatch.setattr(get_config(), "file_check_function_name", "")
        response = _erase()
        assert response.status_code == 503
        assert response.json() == {"detail": "File check is not configured"}
        _nothing_deleted(world)

    def test_lambda_without_the_tool_is_502(self, world):
        """A file-check Lambda deployed before the erase feature does not know the tool."""
        with use_lambda(canned_lambda({"error": "Unknown tool: filecheck___applicant_documents"})):
            response = _erase()
        assert response.status_code == 502
        assert response.json() == {"detail": "Applicant lookup failed: Unknown tool: filecheck___applicant_documents"}
        _nothing_deleted(world)

    def test_malformed_lookup_is_502(self, world):
        payload = {"applicant_name": RAHUL, "documents": [{"document_id": "", "name": "x.pdf"}], "matches": 1}
        with use_lambda(canned_lambda(payload)):
            response = _erase()
        assert response.status_code == 502
        assert response.json() == {"detail": "Applicant lookup returned an unexpected response"}
        _nothing_deleted(world)


def test_openapi_documents_the_contract():
    spec = erase_app.openapi()
    post = spec["paths"]["/projects/{project_id}/applicants/erase"]["post"]
    assert post["tags"] == ["applicants"]
    assert {"200", "400", "404", "409", "422", "502", "503"} <= set(post["responses"])
    schemas = spec["components"]["schemas"]
    assert set(schemas["EraseRequest"]["properties"]) == {"applicant", "confirm", "document_ids"}
    assert set(schemas["EraseRequest"]["required"]) == {"applicant", "confirm", "document_ids"}
    assert set(schemas["EraseResponse"]["properties"]) == {
        "applicant",
        "documents_deleted",
        "failed",
        "delivery_log_redacted",
        "eligibility_inputs_deleted",
        "not_erased",
        "erased_at",
    }
    assert set(schemas["ErasedDocument"]["properties"]) == {"document_id", "name"}
    assert set(schemas["EraseFailedDocument"]["properties"]) == {"document_id", "name", "error"}
