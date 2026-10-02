"""Tests for the file-check integration API (no AWS calls).

The boto3 Lambda client is stubbed. HandlerLambda runs the real file-check
Lambda handler and engine (packages/lambda/file-check-mcp) in-process on a fake
DynamoDB table, so these tests also pin the contract between the backend and
the Lambda: the event, the Gateway-style tool name in ClientContext and the
response shape.
"""

import base64
import importlib.util
import io
import json
import os
import sys
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from fastapi.testclient import TestClient

from app.config import get_config
from app.ddb.models import Project, ProjectData
from app.main import app

client = TestClient(app)

HEADERS = {"x-user-id": "callvarta-crm"}
PROJECT_ID = "proj_demo"
FUNCTION_NAME = "idp-v2-file-check-mcp"

FILE_CHECK_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "lambda", "file-check-mcp"))


def _load_handler():
    """Import the Lambda's index.py (and its engine) under a private name.

    index.py reads BACKEND_TABLE_NAME at import; the variable is restored so
    the backend's own settings never see it.
    """
    if FILE_CHECK_DIR not in sys.path:
        sys.path.insert(0, FILE_CHECK_DIR)
    previous = os.environ.get("BACKEND_TABLE_NAME")
    os.environ["BACKEND_TABLE_NAME"] = "test-table"
    try:
        spec = importlib.util.spec_from_file_location("file_check_mcp_index", os.path.join(FILE_CHECK_DIR, "index.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            os.environ.pop("BACKEND_TABLE_NAME", None)
        else:
            os.environ["BACKEND_TABLE_NAME"] = previous
    return module


fc_index = _load_handler()


# ------------------------------------------------------------------ synthetic facts
def _facts(prefix, name, doc_type, **fields):
    return {
        "schema_version": 1,
        "document_id": f"{prefix}-{name[:2]}",
        "project_id": PROJECT_ID,
        "workflow_id": f"wf-{prefix}-{name[:2]}",
        "document_name": name,
        "doc_type": doc_type,
        "fields": fields,
        "grounding": {"grounded": True, "text_chars": 1200, "notes": [], "unverified_fields": [], "truncated": False},
        "status": "completed",
    }


def sneha():
    n, pan, emp = "Sneha Anil Kulkarni", "CKRPK7314M", "Deccan Retail Pvt Ltd"
    return [
        _facts(
            "s",
            "01_loan_application_form.pdf",
            "loan_application",
            applicant_name=n,
            pan=pan,
            masked_aadhaar_last4="5518",
            employer=emp,
            declared_net_salary=65000.0,
            loan_amount=400000.0,
        ),
        _facts(
            "s",
            "02_identity_details_self_declaration.pdf",
            "identity_details",
            applicant_name=n,
            pan=pan,
            masked_aadhaar_last4="5518",
        ),
        _facts(
            "s",
            "03_salary_slip_2026-07_jul.pdf",
            "salary_slip",
            applicant_name=n,
            pan=pan,
            employer=emp,
            month="2026-07",
            gross_salary=65000.0,
            net_salary=58000.0,
        ),
        _facts(
            "s",
            "04_salary_slip_2026-08_aug.pdf",
            "salary_slip",
            applicant_name=n,
            pan=pan,
            employer=emp,
            month="2026-08",
            gross_salary=65000.0,
            net_salary=58000.0,
        ),
        _facts(
            "s",
            "05_bank_statement_2026-06_to_2026-08.pdf",
            "bank_statement",
            applicant_name="SNEHA ANIL KULKARNI",
            statement_from="2026-06-01",
            statement_to="2026-08-31",
            salary_credits=[
                {"date": f"2026-0{m}-01", "amount": 58000.0, "narration": f"NEFT CR/SAL DECCAN RETAIL PVT LTD/{t}26"}
                for m, t in ((6, "JUN"), (7, "JUL"), (8, "AUG"))
            ],
        ),
    ]


def rahul():
    n, pan, emp = "Rahul Vijay Deshmukh", "BQXPD4821K", "Konkan Softworks Pvt Ltd"
    docs = [
        _facts(
            "r",
            "01_loan_application_form.pdf",
            "loan_application",
            applicant_name=n,
            pan=pan,
            masked_aadhaar_last4="7304",
            employer=emp,
            declared_net_salary=82500.0,
        ),
        _facts(
            "r",
            "02_identity_details_self_declaration.pdf",
            "identity_details",
            applicant_name=n,
            pan=pan,
            masked_aadhaar_last4="7304",
        ),
    ]
    for i, m in enumerate(("06", "07", "08")):
        docs.append(
            _facts(
                "r",
                f"0{i + 3}_salary_slip_2026-{m}.pdf",
                "salary_slip",
                applicant_name=n,
                pan=pan,
                employer=emp,
                month=f"2026-{m}",
                gross_salary=95000.0,
                net_salary=82500.0,
            )
        )
    docs.append(
        _facts(
            "r",
            "06_bank_statement_2026-03_to_2026-08.pdf",
            "bank_statement",
            applicant_name="RAHUL VIJAY DESHMUKH",
            statement_from="2026-03-01",
            statement_to="2026-08-31",
            salary_credits=[
                {"date": f"2026-0{m}-01", "amount": 82500.0, "narration": "NEFT CR/SAL KONKAN SOFTWORKS"}
                for m in range(3, 9)
            ],
        )
    )
    docs.append(
        _facts(
            "r",
            "07_form16_itr_summary_FY2025-26.pdf",
            "form16_itr",
            applicant_name=n,
            pan=pan,
            employer=emp,
            gross_salary=1140000.0,
            financial_year="2025-26",
        )
    )
    return docs


def rahul_with_obligations():
    """Rahul analysed after the obligations extraction: declared EMI plus the bank debits."""
    docs = rahul()
    for d in docs:
        f = d["fields"]
        if d["doc_type"] == "loan_application":
            f["declared_existing_emis"] = [
                {"lender": "Mulshi Auto Finance Ltd (sample)", "loan_type": "Car loan", "amount": 8200.0}
            ]
            f["declared_total_existing_emi"] = 8200.0
            f["loan_tenure_months"] = 48
        elif d["doc_type"] == "bank_statement":
            f["recurring_debits"] = [
                {
                    "date": f"2026-0{m}-05",
                    "amount": 8200.0,
                    "narration": "ACH DR/MULSHI AUTO FINANCE/CAR LOAN EMI",
                    "channel": "ACH",
                    "category": "loan_emi",
                }
                for m in range(3, 9)
            ] + [
                {
                    "date": f"2026-0{m}-03",
                    "amount": 18000.0,
                    "narration": "NEFT DR/RENT/VASANT JOSHI",
                    "channel": "other",
                    "category": "rent",
                }
                for m in range(3, 9)
            ]
    return docs


def _to_ddb(value):
    """Numbers come back from the boto3 resource layer as Decimal."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_ddb(v) for v in value]
    return value


def project_items(facts):
    items = [{"PK": f"PROJ#{PROJECT_ID}", "SK": "META", "data": {"name": "Demo"}}]
    for f in facts:
        doc = {
            "document_id": f["document_id"],
            "project_id": PROJECT_ID,
            "name": f["document_name"],
            "file_type": "application/pdf",
            "status": "completed",
        }
        items.append({"PK": f"PROJ#{PROJECT_ID}", "SK": f"DOC#{f['document_id']}", "data": doc})
        items.append({"PK": f"PROJ#{PROJECT_ID}", "SK": f"FACTS#{f['document_id']}", "data": _to_ddb(f)})
    return items


class FakeTable:
    def __init__(self, items):
        self.items = items

    def query(self, **kwargs):
        return {"Items": self.items}


# ------------------------------------------------------------------ Lambda stubs
class HandlerLambda:
    """boto3 Lambda client stub that runs the real file-check handler."""

    def __init__(self):
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        custom = json.loads(base64.b64decode(kwargs["ClientContext"]))["custom"]
        context = SimpleNamespace(client_context=SimpleNamespace(custom=custom))
        result = fc_index.handler(json.loads(kwargs["Payload"]), context)
        return {"StatusCode": 200, "Payload": io.BytesIO(json.dumps(result).encode("utf-8"))}

    @property
    def tool_names(self):
        return [
            json.loads(base64.b64decode(c["ClientContext"]))["custom"]["bedrockAgentCoreToolName"] for c in self.calls
        ]


def canned_lambda(payload=None, function_error=None, raw=None):
    stub = MagicMock()
    body = raw if raw is not None else json.dumps(payload).encode("utf-8")
    response = {"StatusCode": 200, "Payload": io.BytesIO(body)}
    if function_error:
        response["FunctionError"] = function_error
    stub.invoke.return_value = response
    return stub


def _project(project_id=PROJECT_ID):
    return Project(
        data=ProjectData(project_id=project_id, name="Demo", description="", status="active"),
        created_at="2026-09-28T00:00:00+00:00",
        updated_at="2026-09-28T00:00:00+00:00",
    )


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(get_config(), "file_check_function_name", FUNCTION_NAME)


@pytest.fixture
def project():
    with patch("app.routers.file_check.get_project_item", return_value=_project()) as get_item:
        yield get_item


@pytest.fixture
def no_project():
    with patch("app.routers.file_check.get_project_item", return_value=None) as get_item:
        yield get_item


def use_lambda(stub):
    return patch("app.file_check.get_file_check_lambda_client", return_value=stub)


@pytest.fixture
def handler_lambda(monkeypatch, configured, project):
    monkeypatch.setattr(fc_index, "_table", FakeTable(project_items(sneha() + rahul())))
    stub = HandlerLambda()
    with use_lambda(stub):
        yield stub


# ------------------------------------------------------------------ happy paths
class TestListChecklists:
    def test_lists_catalog_from_lambda(self, handler_lambda):
        response = client.get(f"/projects/{PROJECT_ID}/checklists", headers=HEADERS)

        assert response.status_code == 200
        data = response.json()
        assert data["default_checklist"] == "salaried_personal_loan"
        assert len(data["checklists"]) == 23
        pl = next(c for c in data["checklists"] if c["id"] == "salaried_personal_loan")
        items = {i["id"]: i for i in pl["items"]}
        assert items["salary_slips"]["rule"] == {"kind": "monthly", "months": 3, "field": "month"}
        assert items["loan_application"]["rule"] == {"kind": "present"}
        assert "declared_vs_bank_credits" in pl["consistency_checks"]

        (call,) = handler_lambda.calls
        assert call["FunctionName"] == FUNCTION_NAME
        assert call["InvocationType"] == "RequestResponse"
        assert handler_lambda.tool_names == ["filecheck___list_checklists"]
        assert json.loads(call["Payload"]) == {}


class TestRunFileCheck:
    def test_sneha_not_ready_with_exact_findings(self, handler_lambda):
        response = client.post(
            f"/projects/{PROJECT_ID}/file-check",
            headers=HEADERS,
            json={"checklist_id": "salaried_personal_loan", "applicant": "Sneha Kulkarni"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == PROJECT_ID
        assert data["checklist"] == {"id": "salaried_personal_loan", "name": "Personal Loan - Salaried"}
        assert data["overall_verdict"] == "NOT READY"
        # Her facts predate the obligations extraction: EMIs and FOIR are "to review", never a pass.
        assert data["summary"] == "1 applicant: Sneha Anil Kulkarni NOT READY (5 issues, 2 to review)"
        assert "assistant_instructions" not in data  # chat-only guidance is not part of the API

        (sneha_result,) = data["applicants"]
        assert sneha_result["applicant"] == "Sneha Anil Kulkarni"
        assert sneha_result["pan"] == "CKRPK7314M"
        assert sneha_result["verdict"] == "NOT READY"
        assert sneha_result["reference_month"] == "2026-08"
        assert sneha_result["missing_items"] == [
            "Salary slip: Jun 2026",
            "Bank statement: Mar 2026, Apr 2026, May 2026",
            "Form-16 / ITR (latest FY)",
        ]
        rows = {r["item_id"]: r for r in sneha_result["checklist"]}
        assert rows["salary_slips"]["missing_months"] == ["2026-06"]
        assert rows["bank_statement"]["missing_months"] == ["2026-03", "2026-04", "2026-05"]
        assert rows["bank_statement"]["documents"] == ["05_bank_statement_2026-06_to_2026-08.pdf"]
        assert rows["form16_itr"]["status"] == "MISSING"
        checks = {c["check_id"]: c for c in sneha_result["consistency"]}
        assert checks["declared_vs_slip_net"]["status"] == "MISMATCH"
        assert checks["declared_vs_bank_credits"]["status"] == "MISMATCH"
        assert "₹65,000" in checks["declared_vs_bank_credits"]["detail"]
        assert "₹58,000" in checks["declared_vs_bank_credits"]["detail"]
        assert checks["slip_net_vs_bank_credits"]["status"] == "OK"
        assert sneha_result["income"]["declared_net"] == 65000
        assert sneha_result["income"]["bank_salary_credit"] == 58000
        assert len(sneha_result["mismatches"]) == 2

        (call,) = handler_lambda.calls
        assert handler_lambda.tool_names == ["filecheck___run_file_check"]
        assert json.loads(call["Payload"]) == {
            "project_id": PROJECT_ID,
            "checklist_id": "salaried_personal_loan",
            "applicant": "Sneha Kulkarni",
        }

    def test_all_applicants_default_checklist_without_body(self, handler_lambda):
        response = client.post(f"/projects/{PROJECT_ID}/file-check", headers=HEADERS)

        assert response.status_code == 200
        data = response.json()
        verdicts = {a["applicant"]: a["verdict"] for a in data["applicants"]}
        assert verdicts == {"Rahul Vijay Deshmukh": "READY", "Sneha Anil Kulkarni": "NOT READY"}
        assert data["overall_verdict"] == "NOT READY"
        assert data["checklist"]["id"] == "salaried_personal_loan"
        assert json.loads(handler_lambda.calls[0]["Payload"]) == {"project_id": PROJECT_ID}

    def test_applicant_by_pan_and_reference_month(self, handler_lambda):
        response = client.post(
            f"/projects/{PROJECT_ID}/file-check",
            headers=HEADERS,
            json={"applicant": "BQXPD4821K", "reference_month": "2026-08"},
        )

        assert response.status_code == 200
        data = response.json()
        assert [a["applicant"] for a in data["applicants"]] == ["Rahul Vijay Deshmukh"]
        assert data["overall_verdict"] == "READY"
        assert data["applicants"][0]["missing_items"] == []

    def test_review_findings_and_additive_engine_fields_pass_through(self, monkeypatch, configured, project):
        """A REVIEW consistency finding and the obligations / FOIR blocks reach the caller (no 502)."""
        monkeypatch.setattr(fc_index, "_table", FakeTable(project_items(sneha())))
        context = SimpleNamespace(
            client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": "x___run_file_check"})
        )
        payload = fc_index.handler({"project_id": PROJECT_ID}, context)
        (applicant,) = payload["applicants"]
        foir = {"status": "REVIEW", "value": 0.5, "detail": "indicative"}
        applicant["consistency"].append(
            {
                "check_id": "foir",
                "check": "FOIR (indicative)",
                "status": "REVIEW",
                "detail": "indicative",
                "documents": [],
            }
        )
        applicant["needs_review"] = ["FOIR (indicative): indicative"]
        applicant["obligations"] = {"declared": [], "undeclared": [], "documents": []}
        applicant["foir"] = foir
        with use_lambda(canned_lambda(payload)):
            response = client.post(f"/projects/{PROJECT_ID}/file-check", headers=HEADERS, json={})

        assert response.status_code == 200
        (result,) = response.json()["applicants"]
        assert result["verdict"] == "NOT READY"
        assert result["consistency"][-1]["status"] == "REVIEW"
        assert result["needs_review"] == ["FOIR (indicative): indicative"]
        assert result["obligations"] == {"declared": [], "undeclared": [], "documents": []}
        assert result["foir"] == foir

    def test_real_engine_review_rows_obligations_and_foir_pass_through(self, monkeypatch, configured, project):
        """The real engine's REVIEW rows, needs_review, obligations and FOIR reach the caller unchanged."""
        monkeypatch.setattr(fc_index, "_table", FakeTable(project_items(sneha() + rahul_with_obligations())))
        with use_lambda(HandlerLambda()):
            response = client.post(f"/projects/{PROJECT_ID}/file-check", headers=HEADERS, json={})

        assert response.status_code == 200
        data = response.json()
        assert data["summary"] == (
            "2 applicants: Rahul Vijay Deshmukh READY; Sneha Anil Kulkarni NOT READY (5 issues, 2 to review)"
        )
        by_name = {a["applicant"]: a for a in data["applicants"]}

        # Analysed with obligations: EMI matched in the bank, FOIR computed (indicative).
        rahul_result = by_name["Rahul Vijay Deshmukh"]
        assert rahul_result["verdict"] == "READY"
        assert rahul_result["needs_review"] == []
        checks = {c["check_id"]: c for c in rahul_result["consistency"]}
        assert checks["declared_emis_vs_bank_debits"]["status"] == "OK"
        assert checks["foir"]["status"] == "OK"
        assert "max new EMI is ₹49,550" in checks["foir"]["detail"]
        foir = rahul_result["foir"]
        assert (foir["existing_emis"], foir["max_new_emi"], foir["existing_emi_ratio_pct"]) == (8200, 49550, 9.9)
        assert foir["indicative"] is True and foir["hard_limit"] is False
        (emi,) = rahul_result["obligations"]["fixed_loan_emis"]
        assert (emi["amount"], emi["day_of_month"], emi["months_seen"]) == (8200, 5, 6)
        assert rahul_result["obligations"]["declared_emis"][0]["status"] == "matched"

        # Analysed before the obligations extraction: REVIEW (never a silent pass), verdict unchanged.
        sneha_result = by_name["Sneha Anil Kulkarni"]
        checks = {c["check_id"]: c for c in sneha_result["consistency"]}
        assert checks["declared_emis_vs_bank_debits"]["status"] == "REVIEW"
        assert checks["foir"]["status"] == "REVIEW"
        assert [r.split(":")[0] for r in sneha_result["needs_review"]] == [
            "Declared EMIs vs bank debits",
            "FOIR (indicative)",
        ]
        assert "re-run the analysis" in sneha_result["needs_review"][0]
        assert sneha_result["obligations"]["available"] is False
        assert sneha_result["foir"]["status"] == "REVIEW"
        assert len(sneha_result["reasons"]) == 5

    def test_checklists_carry_foir_policy(self, handler_lambda):
        response = client.get(f"/projects/{PROJECT_ID}/checklists", headers=HEADERS)

        assert response.status_code == 200
        pl = next(c for c in response.json()["checklists"] if c["id"] == "salaried_personal_loan")
        assert (pl["foir"]["value"], pl["foir"]["hard_limit"]) == (0.7, False)
        assert {"declared_emis_vs_bank_debits", "foir"} <= set(pl["consistency_checks"])

    def test_unknown_checklist_is_400_with_valid_ids(self, handler_lambda):
        response = client.post(
            f"/projects/{PROJECT_ID}/file-check",
            headers=HEADERS,
            json={"checklist_id": "no_such_checklist"},
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["message"] == "Unknown checklist_id: no_such_checklist"
        assert "salaried_personal_loan" in detail["available"]


# ------------------------------------------------------------------ auth / validation
class TestAuthAndValidation:
    @pytest.mark.parametrize(
        ("method", "path"),
        [("get", "/checklists"), ("post", "/file-check")],
    )
    def test_missing_user_header_is_rejected(self, method, path, configured, project):
        stub = canned_lambda({})
        with use_lambda(stub):
            response = getattr(client, method)(f"/projects/{PROJECT_ID}{path}")

        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["header", "x-user-id"]
        stub.invoke.assert_not_called()
        project.assert_not_called()

    @pytest.mark.parametrize(
        ("method", "path"),
        [("get", "/checklists"), ("post", "/file-check")],
    )
    def test_unknown_project_is_404(self, method, path, configured, no_project):
        stub = canned_lambda({})
        with use_lambda(stub):
            response = getattr(client, method)(f"/projects/proj_missing{path}", headers=HEADERS)

        assert response.status_code == 404
        assert response.json() == {"detail": "Project not found"}
        no_project.assert_called_once_with("proj_missing")
        stub.invoke.assert_not_called()

    def test_invalid_project_id_is_422(self, configured, project):
        stub = canned_lambda({})
        with use_lambda(stub):
            response = client.post("/projects/proj%0Abad/file-check", headers=HEADERS, json={})

        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["path", "project_id"]
        stub.invoke.assert_not_called()

    @pytest.mark.parametrize(
        "body",
        [
            {"reference_month": "2026-13"},
            {"reference_month": "Aug 2026"},
            {"checklist_id": "Salaried PL"},
            {"applicant": ""},
            {"applicant": "   "},  # blank must not widen the check to every applicant
            {"checklistId": "salaried_personal_loan"},  # unknown field
        ],
    )
    def test_invalid_body_is_422(self, body, configured, project):
        stub = canned_lambda({})
        with use_lambda(stub):
            response = client.post(f"/projects/{PROJECT_ID}/file-check", headers=HEADERS, json=body)

        assert response.status_code == 422
        stub.invoke.assert_not_called()


# ------------------------------------------------------------------ Lambda failures
class TestLambdaErrors:
    @pytest.mark.parametrize(
        ("method", "path"),
        [("get", "/checklists"), ("post", "/file-check")],
    )
    def test_function_error_is_502(self, method, path, configured, project):
        stub = canned_lambda({"errorMessage": "boom", "errorType": "RuntimeError"}, function_error="Unhandled")
        with use_lambda(stub):
            response = getattr(client, method)(f"/projects/{PROJECT_ID}{path}", headers=HEADERS)

        assert response.status_code == 502
        assert response.json() == {"detail": "File check failed: function error (Unhandled)"}

    def test_handler_error_payload_is_502(self, configured, project):
        stub = canned_lambda({"error": "file check failed: KeyError"})
        with use_lambda(stub):
            response = client.post(f"/projects/{PROJECT_ID}/file-check", headers=HEADERS, json={})

        assert response.status_code == 502
        assert response.json() == {"detail": "File check failed: file check failed: KeyError"}

    def test_invoke_client_error_is_502(self, configured, project):
        stub = MagicMock()
        stub.invoke.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "not allowed"}}, "Invoke"
        )
        with use_lambda(stub):
            response = client.post(f"/projects/{PROJECT_ID}/file-check", headers=HEADERS, json={})

        assert response.status_code == 502
        assert response.json() == {"detail": "File check failed: invoke failed (AccessDeniedException)"}

    def test_invoke_timeout_is_502(self, configured, project):
        stub = MagicMock()
        stub.invoke.side_effect = ReadTimeoutError(endpoint_url="https://lambda.ap-south-1.amazonaws.com")
        with use_lambda(stub):
            response = client.get(f"/projects/{PROJECT_ID}/checklists", headers=HEADERS)

        assert response.status_code == 502
        assert response.json() == {"detail": "File check failed: invoke failed (ReadTimeoutError)"}

    def test_non_json_payload_is_502(self, configured, project):
        with use_lambda(canned_lambda(raw=b"<html>")):
            response = client.post(f"/projects/{PROJECT_ID}/file-check", headers=HEADERS, json={})

        assert response.status_code == 502
        assert response.json() == {"detail": "File check failed: non-JSON response"}

    def test_unexpected_payload_shape_is_502(self, configured, project):
        with use_lambda(canned_lambda({"overall_verdict": "MAYBE"})):
            response = client.post(f"/projects/{PROJECT_ID}/file-check", headers=HEADERS, json={})

        assert response.status_code == 502
        assert response.json() == {"detail": "File check returned an unexpected response"}

    def test_not_configured_is_503(self, monkeypatch, project):
        monkeypatch.setattr(get_config(), "file_check_function_name", "")
        stub = canned_lambda({})
        with use_lambda(stub):
            response = client.post(f"/projects/{PROJECT_ID}/file-check", headers=HEADERS, json={})

        assert response.status_code == 503
        assert response.json() == {"detail": "File check is not configured"}
        stub.invoke.assert_not_called()


def test_openapi_documents_the_contract():
    spec = app.openapi()
    post = spec["paths"]["/projects/{project_id}/file-check"]["post"]
    assert post["tags"] == ["file-check"]
    assert post["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/FileCheckResponse")
    assert {"400", "404", "422", "502", "503"} <= set(post["responses"])
    schemas = spec["components"]["schemas"]
    assert schemas["FileCheckResponse"]["properties"]["overall_verdict"]["enum"] == ["READY", "NOT READY"]
    assert set(schemas["FileCheckRequest"]["properties"]) == {"checklist_id", "applicant", "reference_month"}
    get = spec["paths"]["/projects/{project_id}/checklists"]["get"]
    assert get["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/ChecklistCatalog")


# ------------------------------------------------------------------ cost per document (usage)
FACTS_MODEL = "global.amazon.nova-2-lite-v1:0"


def rahul_with_usage():
    """Rahul's documents as the facts step now records them, one analysed before usage."""
    docs = rahul()
    for i, d in enumerate(docs):
        if d["doc_type"] == "form16_itr":
            continue  # an old record: no usage
        d["usage"] = {"model_id": FACTS_MODEL, "input_tokens": 1000 * (i + 1), "output_tokens": 100, "cost_usd": 0.0}
        d["usage"]["cost_usd"] = round(d["usage"]["input_tokens"] * 0.35e-6 + 100 * 2.95e-6, 8)
    return docs


class TestUsage:
    def test_documents_carry_usage_and_applicants_their_total(self, monkeypatch, configured, project):
        monkeypatch.setattr(fc_index, "_table", FakeTable(project_items(rahul_with_usage() + sneha())))
        with use_lambda(HandlerLambda()):
            response = client.post(f"/projects/{PROJECT_ID}/file-check", headers=HEADERS, json={})

        assert response.status_code == 200
        by_name = {a["applicant"]: a for a in response.json()["applicants"]}
        rahul_result = by_name["Rahul Vijay Deshmukh"]
        usage = {d["document_name"]: d["usage"] for d in rahul_result["documents"]}
        assert usage["01_loan_application_form.pdf"] == {
            "model_id": FACTS_MODEL,
            "input_tokens": 1000,
            "output_tokens": 100,
            "cost_usd": 0.000645,  # 1,000 x $0.35/M + 100 x $2.95/M
        }
        assert usage["07_form16_itr_summary_FY2025-26.pdf"] is None  # old record: null, still valid
        assert rahul_result["usage_total"] == {
            "input_tokens": 1000 + 2000 + 3000 + 4000 + 5000 + 6000,
            "output_tokens": 600,
            "cost_usd": 0.009120,  # 21,000 x 0.35/M + 600 x 2.95/M
            "documents_with_usage": 6,
            "documents_total": 7,
        }
        # every document entry has the key (object or null), not only those with usage
        sneha_result = by_name["Sneha Anil Kulkarni"]
        assert all("usage" in d and d["usage"] is None for d in sneha_result["documents"])
        assert sneha_result["usage_total"] == {
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "documents_with_usage": 0,
            "documents_total": 5,
        }

    def test_engine_without_usage_still_validates(self, monkeypatch, configured, project):
        """A file-check Lambda deployed before usage answers without the fields: null, not a 502."""
        monkeypatch.setattr(fc_index, "_table", FakeTable(project_items(sneha())))
        context = SimpleNamespace(
            client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": "x___run_file_check"})
        )
        payload = fc_index.handler({"project_id": PROJECT_ID}, context)
        for applicant in payload["applicants"]:
            applicant.pop("usage_total")
            for doc in applicant["documents"]:
                doc.pop("usage")
        with use_lambda(canned_lambda(payload)):
            response = client.post(f"/projects/{PROJECT_ID}/file-check", headers=HEADERS, json={})

        assert response.status_code == 200
        (result,) = response.json()["applicants"]
        assert result["usage_total"] is None
        assert all(d["usage"] is None for d in result["documents"])

    def test_openapi_documents_the_usage_fields(self):
        schemas = app.openapi()["components"]["schemas"]
        assert "usage" in schemas["ApplicantDocument"]["properties"]
        assert "usage_total" in schemas["ApplicantResult"]["properties"]
        assert set(schemas["DocumentUsage"]["properties"]) == {"model_id", "input_tokens", "output_tokens", "cost_usd"}
        assert set(schemas["ApplicantUsageTotal"]["properties"]) == {
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "documents_with_usage",
            "documents_total",
        }
