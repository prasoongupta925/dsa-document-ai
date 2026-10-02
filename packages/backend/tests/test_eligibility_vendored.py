"""The chat's loan_eligibility tool (packages/lambda/file-check-mcp) against the eligibility API.

The file-check Lambda ships a byte-identical copy of app/eligibility.py and its data (the
first tests keep them in step) and reads the inputs the API saved: on the same saved inputs
and documents its answer must be the API's, number for number. No AWS calls; the Lambda runs
in-process (tests/test_file_check.py) on the API tests' fake table. Synthetic data only.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routers.eligibility as elig
from app import eligibility
from app.config import get_config
from tests.test_eligibility_api import META, FakeTable, example
from tests.test_file_check import FILE_CHECK_DIR, HandlerLambda, fc_index, project_items, rahul_with_obligations, sneha

loan_tools = sys.modules["loan_tools"]  # imported by the Lambda's index.py

eligibility_app = FastAPI()
eligibility_app.include_router(elig.router)
client = TestClient(eligibility_app)

HEADERS = {"x-user-id": "dsa-user"}
PROJECT_ID = "proj_demo"
RAHUL, RAHUL_PAN = "Rahul Vijay Deshmukh", "BQXPD4821K"
SNEHA = "Sneha Anil Kulkarni"
BACKEND_APP = Path(eligibility.__file__).resolve().parent
LAMBDA_DIR = Path(FILE_CHECK_DIR)
LOAN_ELIGIBILITY = SimpleNamespace(
    client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": "filecheck___loan_eligibility"})
)


@pytest.mark.parametrize(
    "name", ["eligibility.py", "data/lender_policies.json", "data/pincodes.json", "data/companies.json"]
)
def test_lambda_copy_is_identical(name):
    assert (LAMBDA_DIR / name).read_bytes() == (BACKEND_APP / name).read_bytes(), (
        f"packages/lambda/file-check-mcp/{name} must be a copy of app/{name}: "
        f"cp packages/backend/app/{name} packages/lambda/file-check-mcp/{name}"
    )


@pytest.fixture
def table(monkeypatch):
    """One fake table for the API and the Lambda: the project, Rahul's and Sneha's documents."""
    facts = rahul_with_obligations() + sneha()
    fake = FakeTable([META, *[i for i in project_items(facts) if i["SK"] != "META"]])
    for target in (
        "app.routers.eligibility.get_table",
        "app.ddb.projects.get_table",
        "app.ddb.webhooks.get_table",
        "app.ddb.facts.get_table",
        "app.reference_data.get_table",
    ):
        monkeypatch.setattr(target, lambda: fake)
    monkeypatch.setattr(fc_index, "_table", fake)
    monkeypatch.setattr(get_config(), "retention_days", 7)
    monkeypatch.setattr(get_config(), "file_check_function_name", "idp-v2-file-check-mcp")
    monkeypatch.setattr(get_config(), "webhook_function_name", "")
    with patch("app.file_check.get_file_check_lambda_client", return_value=HandlerLambda()):
        yield fake


def tool(applicant):
    return fc_index.handler({"project_id": PROJECT_ID, "applicant": applicant}, LOAN_ELIGIBILITY)


def known(value):
    """Without the null fields (the API's response models add them where the engine has no value)."""
    if isinstance(value, dict):
        return {k: known(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [known(v) for v in value]
    return value


SAME = (
    "per_lender",
    "best_lender",
    "best_lender_id",
    "best_lender_reason",
    "income_considered",
    "income",
    "obligations",
    "obligation_details",
    "bt_amount",
    "requested",
    "notes",
    "disclaimers",
    "sample",
    "policy_label",
    "label",
)


@pytest.mark.parametrize(
    ("applicant", "changes"),
    [
        (RAHUL, {"profile__net_income": 82500}),  # the documents' figures: bank EMI 8,200 unmatched
        (RAHUL, {}),  # the sheet's example with Rahul's documents
        (SNEHA, {"profile__name": SNEHA, "profile__company": "Deccan Retail Pvt Ltd", "loan__tenure_months": 84}),
        ("Konkan Applicant", {"profile__name": "Konkan Applicant", "loan__tenure_months": None}),  # no documents
    ],
)
def test_the_tool_answers_as_the_api(table, applicant, changes):
    saved = client.put(
        f"/projects/{PROJECT_ID}/eligibility/inputs",
        headers=HEADERS,
        json={"applicant": applicant, **example(**changes)},
    )
    assert saved.status_code == 200
    api = client.post(f"/projects/{PROJECT_ID}/eligibility/calculate", headers=HEADERS, json={"applicant": applicant})
    assert api.status_code == 200

    answer = json.loads(json.dumps(tool(applicant)))

    assert "error" not in answer, answer
    body = api.json()
    assert [key for key in SAME if known(answer[key]) != known(body[key])] == []
    assert answer["file_check"]["used"] == body["file_check"]["used"]
    assert answer["file_check"]["detail"] == body["file_check"]["detail"]


def test_the_tool_finds_inputs_the_api_saved_under_the_pan(table):
    client.put(
        f"/projects/{PROJECT_ID}/eligibility/inputs",
        headers=HEADERS,
        json={"applicant": RAHUL_PAN, **example(profile__pan=RAHUL_PAN)},
    )

    answer = tool("Rahul Deshmukh")

    assert answer["best_lender"] == "ICICI Bank"
    assert answer["pan_masked"] == "XXXXXX821K"
    assert RAHUL_PAN not in json.dumps(answer)


@pytest.mark.parametrize("applicant", ["BQXPD4821K", " bqxpd 4821k ", RAHUL, "  rahul   vijay DESHMUKH ", "Sneha"])
def test_the_saved_key_is_the_apis(applicant):
    assert loan_tools.applicant_key(applicant) == elig.applicant_key(applicant)


def test_the_file_check_figures_are_the_apis(table):
    check = fc_index.handler(
        {"project_id": PROJECT_ID},
        SimpleNamespace(
            client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": "filecheck___run_file_check"})
        ),
    )

    assert len(check["applicants"]) == 2
    for found in check["applicants"]:
        assert loan_tools.verified_income(found) == elig.verified_income(found)
        assert loan_tools.bank_statement_emis(found) == elig.bank_statement_emis(found)
    assert any(loan_tools.bank_statement_emis(found) for found in check["applicants"])
