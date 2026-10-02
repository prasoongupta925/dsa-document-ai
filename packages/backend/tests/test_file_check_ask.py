"""Tests for POST .../file-check/ask and GET .../file-check/usage (no AWS calls).

The file-check Lambda runs in-process (the real handler and engine, as in
test_file_check.py). DynamoDB is a fake table that evaluates the key
conditions, S3 is a fake client holding segment JSON files, and Bedrock is a
stub that records the Converse request.
"""

import io
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from app.config import get_config
from app.ddb.ask_usage import put_ask_usage, sum_ask_usage
from app.file_check_ask import (
    CHARS_PER_TOKEN,
    CONTEXT_ENVELOPE_CHARS,
    SYSTEM_PROMPT,
    SourceDocument,
    build_context,
    build_messages,
    compact_verdict,
    cost_usd,
    facts_view,
    fit_history,
    page_text,
)
from app.main import app
from tests.test_file_check import (
    FUNCTION_NAME,
    HEADERS,
    PROJECT_ID,
    HandlerLambda,
    _project,
    _to_ddb,
    canned_lambda,
    client,
    fc_index,
    rahul_with_obligations,
    sneha,
    use_lambda,
)

BUCKET = "doc-bucket"
MODEL_ID = "global.amazon.nova-2-lite-v1:0"
ASK = f"/projects/{PROJECT_ID}/file-check/ask"
USAGE = f"/projects/{PROJECT_ID}/file-check/usage"


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
    if op == "BETWEEN":
        return value is not None and values[1] <= value <= values[2]
    raise AssertionError(f"unsupported key condition {op}")


class FakeDdbTable:
    """Base table with PK/SK key conditions, pagination and put/get."""

    def __init__(self, items=(), page_size=None):
        self.items = list(items)
        self.page_size = page_size
        self.queries = []
        self.puts = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        condition = kwargs["KeyConditionExpression"]
        rows = sorted(
            (i for i in self.items if _matches(condition, i)),
            key=lambda i: (i["PK"], i["SK"]),
        )
        start = kwargs.get("ExclusiveStartKey", {}).get("offset", 0)
        size = self.page_size or len(rows) or 1
        page = rows[start : start + size]
        response = {"Items": page}
        if start + size < len(rows):
            response["LastEvaluatedKey"] = {"offset": start + size}
        return response

    def get_item(self, Key):
        for item in self.items:
            if item["PK"] == Key["PK"] and item["SK"] == Key["SK"]:
                return {"Item": item}
        return {}

    def put_item(self, Item):
        self.puts.append(Item)
        self.items.append(Item)

    def ledger(self):
        return [i for i in self.items if i["SK"].startswith("FCASK#")]


class FakeS3:
    def __init__(self, objects):
        self.objects = objects
        self.gets = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        s3 = self

        class _Paginator:
            def paginate(self, Bucket, Prefix):
                assert Bucket == BUCKET
                keys = sorted(k for k in s3.objects if k.startswith(Prefix))
                return [{"Contents": [{"Key": k} for k in keys]}]

        return _Paginator()

    def get_object(self, Bucket, Key):
        assert Bucket == BUCKET
        self.gets.append(Key)
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
        return {"Body": io.BytesIO(json.dumps(self.objects[Key]).encode("utf-8"))}


class FakeBedrock:
    def __init__(self, text="The car loan EMI is ₹8,200 [06_bank_statement].", usage=(10000, 500), error=None):
        self.text = text
        self.usage = usage
        self.error = error
        self.stop_reason = "end_turn"
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": self.text}]}},
            "stopReason": self.stop_reason,
            "usage": {
                "inputTokens": self.usage[0],
                "outputTokens": self.usage[1],
                "totalTokens": sum(self.usage),
            },
        }

    @property
    def prompt(self):
        return self.calls[-1]["messages"][-1]["content"][0]["text"]


# ------------------------------------------------------------------ project data
def _segment_prefix(document_id):
    return f"projects/{PROJECT_ID}/documents/{document_id}/analysis/segment_"


def ask_items(facts):
    """META, complete DOC# and FACTS# items (as the boto3 resource returns them)."""
    now = "2026-09-28T00:00:00+00:00"
    items = [{"PK": f"PROJ#{PROJECT_ID}", "SK": "META", "data": {"name": "Demo"}}]
    for f in facts:
        did = f["document_id"]
        doc = {
            "document_id": did,
            "project_id": PROJECT_ID,
            "name": f["document_name"],
            "file_type": "application/pdf",
            "file_size": 1024,
            "status": "completed",
            "s3_key": f"projects/{PROJECT_ID}/documents/{did}/{did}.pdf",
        }
        items.append(
            {"PK": f"PROJ#{PROJECT_ID}", "SK": f"DOC#{did}", "data": _to_ddb(doc), "created_at": now, "updated_at": now}
        )
        items.append({"PK": f"PROJ#{PROJECT_ID}", "SK": f"FACTS#{did}", "data": _to_ddb(f)})
    return items


def page_objects(facts, pages_per_doc=1, text=None):
    objects = {}
    for f in facts:
        for i in range(pages_per_doc):
            body = text or f"{f['document_name']} page {i + 1} printed text. " * 3
            objects[f"{_segment_prefix(f['document_id'])}{i:04d}.json"] = {
                "segment_index": i,
                "segment_type": "PAGE",
                "format_parser": body,
                "ai_analysis": [{"analysis_query": "q", "content": "vision text"}],
            }
    return objects


@pytest.fixture
def env(monkeypatch):
    """Configured backend with Rahul (with obligations) and Sneha in one project."""
    config = get_config()
    monkeypatch.setattr(config, "file_check_function_name", FUNCTION_NAME)
    monkeypatch.setattr(config, "document_storage_bucket_name", BUCKET)
    monkeypatch.setattr(config, "aws_region", "ap-south-1")
    monkeypatch.setattr(config, "file_check_ask_model_id", MODEL_ID)
    monkeypatch.setattr(config, "file_check_ask_max_input_tokens", 12000)
    monkeypatch.setattr(config, "retention_days", 7)

    facts = rahul_with_obligations() + sneha()
    table = FakeDdbTable(ask_items(facts))
    monkeypatch.setattr(fc_index, "_table", table)  # the Lambda reads the same table
    s3 = FakeS3(page_objects(facts))
    bedrock = FakeBedrock()
    lambda_stub = HandlerLambda()
    with (
        patch("app.routers.file_check.get_project_item", return_value=_project()) as get_project,
        patch("app.ddb.facts.get_table", return_value=table),
        patch("app.ddb.documents.get_table", return_value=table),
        patch("app.ddb.ask_usage.get_table", return_value=table),
        patch("app.s3.get_s3_client", return_value=s3),
        patch("app.file_check_ask.get_bedrock_client", return_value=bedrock),
        use_lambda(lambda_stub),
    ):
        yield type(
            "Env",
            (),
            {
                "table": table,
                "s3": s3,
                "bedrock": bedrock,
                "lambda_stub": lambda_stub,
                "get_project": get_project,
                "facts": facts,
            },
        )


def _section(prompt, tag):
    match = re.search(rf"<{tag}>\n(.*?)\n</{tag}>", prompt, re.S)
    assert match, f"no <{tag}> section"
    return match.group(1)


# ------------------------------------------------------------------ happy path
class TestAsk:
    def test_answer_cost_and_grounding(self, env):
        response = client.post(
            ASK,
            headers=HEADERS,
            json={"question": "  Summarise the monthly obligations  ", "applicant": "Rahul Vijay Deshmukh"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["answer"] == "The car loan EMI is ₹8,200 [06_bank_statement]."
        assert data["model_id"] == MODEL_ID
        assert (data["input_tokens"], data["output_tokens"]) == (10000, 500)
        # 10,000 x $0.35/M + 500 x $2.95/M = $0.0035 + $0.001475
        assert data["cost_usd"] == pytest.approx(0.004975, abs=1e-12)
        assert data["pricing"] == {
            "input_per_million_usd": 0.35,
            "output_per_million_usd": 2.95,
            "region": "ap-south-1",
        }
        assert data["grounded_on"]["applicants"] == ["Rahul Vijay Deshmukh"]
        assert data["grounded_on"]["documents"] == [f["document_name"] for f in rahul_with_obligations()]
        assert set(data) == {
            "answer",
            "model_id",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "pricing",
            "grounded_on",
        }

        # The verdict comes from the file-check Lambda, for this applicant only.
        (call,) = env.lambda_stub.calls
        assert json.loads(call["Payload"]) == {"project_id": PROJECT_ID, "applicant": "Rahul Vijay Deshmukh"}

        # One Converse call: temperature 0, the grounding-only system prompt.
        (converse,) = env.bedrock.calls
        assert converse["modelId"] == MODEL_ID
        assert converse["inferenceConfig"] == {"temperature": 0, "maxTokens": 1024}
        assert converse["system"] == [{"text": SYSTEM_PROMPT}]
        assert "ONLY" in SYSTEM_PROMPT and "outside knowledge" in SYSTEM_PROMPT
        assert "Never guess" in SYSTEM_PROMPT and "not in the file" in SYSTEM_PROMPT.lower()
        assert [m["role"] for m in converse["messages"]] == ["user"]

        prompt = env.bedrock.prompt
        assert prompt.endswith("Question: Summarise the monthly obligations")
        verdict = json.loads(_section(prompt, "file_check_verdict"))
        (applicant,) = verdict["applicants"]
        assert applicant["foir"]["max_new_emi"] == 49550
        assert applicant["obligations"]["fixed_loan_emis"][0]["amount"] == 8200
        facts = [json.loads(line) for line in _section(prompt, "document_facts").splitlines()]
        assert [f["document_name"] for f in facts] == data["grounded_on"]["documents"]
        bank = next(f for f in facts if f["doc_type"] == "bank_statement")
        assert len(bank["fields"]["recurring_debits"]) == 12
        assert bank["fields"]["recurring_debits"][0]["narration"] == "ACH DR/MULSHI AUTO FINANCE/CAR LOAN EMI"
        pages = _section(prompt, "page_text")
        assert "=== 06_bank_statement_2026-03_to_2026-08.pdf, page 1 ===" in pages
        assert "<context_notes>" not in prompt  # everything fitted
        # Nothing about the other applicant reaches the model.
        assert "Sneha" not in prompt and "CKRPK7314M" not in prompt
        # Pages were read only for Rahul's documents.
        rahul_ids = {f["document_id"] for f in rahul_with_obligations()}
        assert {k.split("/")[3] for k in env.s3.gets} == rahul_ids

    def test_ledger_item_per_call(self, env):
        before = int(datetime.now(UTC).timestamp())
        response = client.post(ASK, headers=HEADERS, json={"question": "What is the FOIR?"})
        after = int(datetime.now(UTC).timestamp())

        assert response.status_code == 200
        (item,) = env.table.ledger()
        assert item["PK"] == f"PROJ#{PROJECT_ID}"
        assert re.fullmatch(r"FCASK#\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00#[0-9a-f]{32}", item["SK"])
        assert item["SK"].startswith(f"FCASK#{item['created_at']}#")
        assert (item["input_tokens"], item["output_tokens"]) == (10000, 500)
        assert item["cost_usd"] == Decimal("0.004975")
        assert item["model_id"] == MODEL_ID
        assert before + 7 * 86400 <= item["expires_at"] <= after + 7 * 86400
        # Only counts: no question, answer or file content is stored.
        assert set(item) == {
            "PK",
            "SK",
            "created_at",
            "model_id",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "expires_at",
        }

    def test_usage_sums_the_calls(self, env):
        env.bedrock.usage = (1000, 100)
        for question in ("First?", "Second?"):
            assert client.post(ASK, headers=HEADERS, json={"question": question}).status_code == 200

        response = client.get(USAGE, headers=HEADERS)

        assert response.status_code == 200
        assert response.json() == {
            "window_days": 7,
            "calls": 2,
            "input_tokens": 2000,
            "output_tokens": 200,
            # 2 x (1,000 x 0.35 + 100 x 2.95) / 1M
            "cost_usd": pytest.approx(0.00129, abs=1e-12),
        }

    def test_history_goes_before_the_question(self, env):
        history = [
            {"role": "assistant", "content": "Hello, ask me about the file."},  # leading assistant turn: dropped
            {"role": "user", "content": "Summarise the obligations"},
            {"role": "assistant", "content": "Car loan EMI ₹8,200 on the 5th."},
        ]
        response = client.post(
            ASK,
            headers=HEADERS,
            json={"question": "And the FOIR?", "applicant": "BQXPD4821K", "history": history},
        )

        assert response.status_code == 200
        messages = env.bedrock.calls[0]["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant", "user"]
        assert messages[0]["content"] == [{"text": "Summarise the obligations"}]
        assert messages[1]["content"] == [{"text": "Car loan EMI ₹8,200 on the 5th."}]
        assert "<file_check_verdict>" in messages[2]["content"][0]["text"]
        assert messages[2]["content"][0]["text"].endswith("Question: And the FOIR?")

    def test_answer_cut_at_the_output_limit_is_marked(self, env):
        env.bedrock.stop_reason = "max_tokens"
        response = client.post(ASK, headers=HEADERS, json={"question": "List every debit"})

        assert response.status_code == 200
        assert response.json()["answer"].endswith("[The answer was cut at the length limit.]")

    def test_ledger_failure_still_answers(self, env):
        def fail(**kwargs):
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "PutItem")

        env.table.put_item = fail
        response = client.post(ASK, headers=HEADERS, json={"question": "What is missing?"})

        assert response.status_code == 200
        assert response.json()["answer"]

    def test_unreadable_facts_and_pages_still_answer_from_the_verdict(self, env):
        class BrokenS3(FakeS3):
            def get_object(self, Bucket, Key):  # boto3 argument names
                self.gets.append(Key)
                raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "GetObject")

        broken = BrokenS3(env.s3.objects)
        facts_error = ClientError({"Error": {"Code": "ThrottlingException"}}, "Query")
        with (
            patch("app.s3.get_s3_client", return_value=broken),
            patch("app.file_check_ask.query_facts", side_effect=facts_error),
        ):
            response = client.post(
                ASK, headers=HEADERS, json={"question": "Ready?", "applicant": "Rahul Vijay Deshmukh"}
            )

        assert response.status_code == 200
        prompt = env.bedrock.prompt
        assert json.loads(_section(prompt, "file_check_verdict"))["overall_verdict"] == "READY"
        assert _section(prompt, "document_facts") == ""
        assert _section(prompt, "page_text") == ""
        assert "The document facts could not be loaded." in prompt
        assert broken.gets  # the pages were tried, and skipped

    def test_unknown_checklist_is_400(self, env):
        response = client.post(ASK, headers=HEADERS, json={"question": "Ready?", "checklist_id": "nope"})

        assert response.status_code == 400
        assert response.json()["detail"]["message"] == "Unknown checklist_id: nope"
        assert env.bedrock.calls == []


# ------------------------------------------------------------------ errors
class TestAskErrors:
    @pytest.mark.parametrize(
        "body",
        [
            {"question": ""},
            {"question": "   "},
            {},
            {"question": "x" * 1001},
            {"question": "ok", "applicant": "  "},
            {"question": "ok", "checklist_id": "Salaried PL"},
            {"question": "ok", "history": [{"role": "user", "content": "q"}] * 7},
            {"question": "ok", "history": [{"role": "system", "content": "be nice"}]},
            {"question": "ok", "history": [{"role": "user", "content": " "}]},
            {"question": "ok", "reference_month": "2026-08"},  # unknown field
        ],
    )
    def test_invalid_body_is_422(self, env, body):
        response = client.post(ASK, headers=HEADERS, json=body)

        assert response.status_code == 422
        assert env.lambda_stub.calls == [] and env.bedrock.calls == []

    def test_missing_user_header_is_422(self, env):
        assert client.post(ASK, json={"question": "Ready?"}).status_code == 422
        assert client.get(USAGE).status_code == 422
        assert env.bedrock.calls == []

    @pytest.mark.parametrize(("method", "path"), [("post", "/file-check/ask"), ("get", "/file-check/usage")])
    def test_unknown_project_is_404(self, env, method, path):
        env.get_project.return_value = None
        kwargs = {"json": {"question": "Ready?"}} if method == "post" else {}

        response = getattr(client, method)(f"/projects/proj_missing{path}", headers=HEADERS, **kwargs)

        assert response.status_code == 404
        assert response.json() == {"detail": "Project not found"}
        env.get_project.assert_called_once_with("proj_missing")
        assert env.lambda_stub.calls == [] and env.bedrock.calls == [] and env.table.queries == []

    def test_bedrock_error_is_502_and_not_metered(self, env):
        env.bedrock.error = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Converse")

        response = client.post(ASK, headers=HEADERS, json={"question": "Ready?"})

        assert response.status_code == 502
        assert response.json() == {"detail": "Answer failed: model call failed (ThrottlingException)"}
        assert env.table.ledger() == []

    def test_bedrock_timeout_is_502(self, env):
        env.bedrock.error = ReadTimeoutError(endpoint_url="https://bedrock-runtime.ap-south-1.amazonaws.com")

        response = client.post(ASK, headers=HEADERS, json={"question": "Ready?"})

        assert response.status_code == 502
        assert response.json() == {"detail": "Answer failed: model call failed (ReadTimeoutError)"}

    def test_empty_model_answer_is_502_but_metered(self, env):
        env.bedrock.text = "   "

        response = client.post(ASK, headers=HEADERS, json={"question": "Ready?"})

        assert response.status_code == 502
        assert response.json() == {"detail": "Answer failed: the model returned no answer"}
        assert len(env.table.ledger()) == 1  # the tokens were billed

    def test_file_check_failure_is_502_without_model_call(self, env):
        with use_lambda(canned_lambda({"error": "file check failed: KeyError"})):
            response = client.post(ASK, headers=HEADERS, json={"question": "Ready?"})

        assert response.status_code == 502
        assert env.bedrock.calls == []

    def test_not_configured_is_503(self, env, monkeypatch):
        monkeypatch.setattr(get_config(), "file_check_function_name", "")

        response = client.post(ASK, headers=HEADERS, json={"question": "Ready?"})

        assert response.status_code == 503
        assert env.bedrock.calls == []


# ------------------------------------------------------------------ context cap
class TestContextCap:
    def test_request_stays_under_the_token_cap_and_cuts_page_text_first(self, env, monkeypatch):
        monkeypatch.setattr(get_config(), "file_check_ask_max_input_tokens", 12000)
        long_page = "ACH DR/MULSHI AUTO FINANCE/CAR LOAN EMI 8,200.00 " * 200  # ~10k chars a page
        env.s3.objects = page_objects(env.facts, pages_per_doc=4, text=long_page)
        history = [{"role": "user", "content": "q " * 3000}, {"role": "assistant", "content": "a " * 3000}]

        response = client.post(
            ASK,
            headers=HEADERS,
            json={"question": "Summarise the obligations", "applicant": "Rahul Vijay Deshmukh", "history": history},
        )

        assert response.status_code == 200
        call = env.bedrock.calls[0]
        total_chars = len(call["system"][0]["text"]) + sum(len(m["content"][0]["text"]) for m in call["messages"])
        assert total_chars / CHARS_PER_TOKEN <= 12000
        prompt = env.bedrock.prompt
        # Verdict and facts complete; the page text was cut and says so.
        verdict = json.loads(_section(prompt, "file_check_verdict"))
        assert verdict["applicants"][0]["foir"]["max_new_emi"] == 49550
        facts = [json.loads(line) for line in _section(prompt, "document_facts").splitlines()]
        assert len(facts) == 7
        assert "cut to fit the size limit" in _section(prompt, "page_text")
        assert "Page text:" in _section(prompt, "context_notes")
        # Pages are read only while there is room (not all 28).
        assert len(env.s3.gets) < 28

    def test_whole_project_verdict_is_compacted_when_it_does_not_fit(self, env, monkeypatch):
        monkeypatch.setattr(get_config(), "file_check_ask_max_input_tokens", 5000)

        response = client.post(ASK, headers=HEADERS, json={"question": "Which files are ready?"})

        assert response.status_code == 200
        prompt = env.bedrock.prompt
        assert "No page text was given" in prompt or "Page text:" in prompt
        verdict_text = _section(prompt, "file_check_verdict")
        assert '"evidence"' not in verdict_text
        assert len(prompt) + len(SYSTEM_PROMPT) <= 5000 * CHARS_PER_TOKEN
        assert response.json()["grounded_on"]["applicants"] == ["Rahul Vijay Deshmukh", "Sneha Anil Kulkarni"]


class TestBuildContext:
    VERDICT = {
        "overall_verdict": "READY",
        "applicants": [
            {
                "applicant": "A",
                "obligations": {"fixed_loan_emis": [{"amount": 1, "evidence": [{"date": "2026-01-05"}] * 50}]},
            }
        ],
    }

    @staticmethod
    def docs(n=2):
        return [
            SourceDocument(
                document_id=f"d{i}",
                document_name=f"doc{i}.pdf",
                facts={"document_name": f"doc{i}.pdf", "doc_type": "other", "fields": {"v": "f" * 300}},
            )
            for i in range(n)
        ]

    @staticmethod
    def pages(per_doc=3, size=500):
        store = {
            f"d{d}/{p}": {"segment_index": p, "format_parser": f"doc{d} p{p} " + "t" * size}
            for d in range(2)
            for p in range(per_doc)
        }
        listed = []

        def page_keys(doc):
            listed.append(doc.document_id)
            return [f"{doc.document_id}/{p}" for p in range(per_doc)]

        return store, listed, page_keys

    def test_everything_fits(self):
        store, _, page_keys = self.pages()
        ctx = build_context(self.VERDICT, self.docs(), 100_000, page_keys, store.get)

        assert ctx.stats["truncated"] is False
        assert ctx.stats["pages_included"] == 6 and ctx.stats["facts_included"] == 2
        assert "<context_notes>" not in ctx.text

    @staticmethod
    def fixed_len(docs):
        """Characters the verdict and the facts lines take."""
        dumps = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
        return len(dumps(TestBuildContext.VERDICT)) + sum(len(dumps(facts_view(d.facts))) + 1 for d in docs)

    def test_pages_are_cut_first_and_round_robin(self):
        store, _, page_keys = self.pages()
        docs = self.docs()
        chunk = len("=== doc0.pdf, page 1 ===\n") + len("doc0 p0 " + "t" * 500) + 2
        budget = CONTEXT_ENVELOPE_CHARS + self.fixed_len(docs) + int(2.5 * chunk)

        ctx = build_context(self.VERDICT, docs, budget, page_keys, store.get)

        assert len(ctx.text) <= budget
        assert ctx.stats["facts_included"] == 2 and not ctx.stats["verdict_compacted"]
        # Page 1 of both documents, then page 2 of the first one, cut.
        assert (ctx.stats["pages_included"], ctx.stats["pages_truncated"], ctx.stats["pages_omitted"]) == (2, 1, 3)
        pages = _section(ctx.text, "page_text")
        assert pages.index("doc0.pdf, page 1") < pages.index("doc1.pdf, page 1") < pages.index("doc0.pdf, page 2")
        assert "doc1.pdf, page 2" not in pages
        assert pages.endswith("cut to fit the size limit]")
        assert "Page text: 3 of 6 page(s) given" in _section(ctx.text, "context_notes")

    def test_facts_are_cut_before_the_verdict_and_pages_are_not_read(self):
        store, listed, page_keys = self.pages()
        verdict_len = len(json.dumps(self.VERDICT, ensure_ascii=False, separators=(",", ":")))
        budget = CONTEXT_ENVELOPE_CHARS + verdict_len + 300  # the verdict and part of one facts line

        ctx = build_context(self.VERDICT, self.docs(), budget, page_keys, store.get)

        assert not ctx.stats["verdict_compacted"]
        assert json.loads(_section(ctx.text, "file_check_verdict")) == self.VERDICT
        assert (ctx.stats["facts_included"], ctx.stats["facts_truncated"], ctx.stats["facts_omitted"]) == (0, 1, 1)
        assert listed == []  # no room left: S3 is not even listed
        assert "No page text was given" in ctx.text

    def test_verdict_is_compacted_then_cut_last(self):
        store, _, page_keys = self.pages()
        compact_len = len(json.dumps(compact_verdict(self.VERDICT), separators=(",", ":")))

        ctx = build_context(self.VERDICT, self.docs(), compact_len + CONTEXT_ENVELOPE_CHARS, page_keys, store.get)
        assert ctx.stats["verdict_compacted"] and not ctx.stats["verdict_truncated"]
        assert json.loads(_section(ctx.text, "file_check_verdict")) == compact_verdict(self.VERDICT)

        ctx = build_context(self.VERDICT, self.docs(), 600, page_keys, store.get)
        assert ctx.stats["verdict_truncated"]
        assert "The verdict was cut" in ctx.text


# ------------------------------------------------------------------ helpers
def test_cost_math():
    assert cost_usd(0, 0) == 0
    assert cost_usd(1_000_000, 0) == pytest.approx(0.35)
    assert cost_usd(0, 1_000_000) == pytest.approx(2.95)
    assert cost_usd(12000, 1024) == pytest.approx(0.0042 + 0.0030208, abs=1e-12)


def test_facts_view_leaves_out_fields_without_a_value():
    """A record has a key for every field of every document type: only the values are sent."""
    record = {
        "document_name": "08_credit_report.pdf",
        "doc_type": "credit_report",
        "fields": {
            "credit_score": 771,
            "enquiries_30d": 0,
            "tradelines": [{"lender": "Mulshi Auto Finance", "account_last4": "4512"}],
            "mobile": None,
            "salary_credits": [],
            "employer": "",
        },
        "grounding": {"grounded": True, "unverified_fields": ["credit_score"]},
    }
    assert facts_view(record) == {
        "document_name": "08_credit_report.pdf",
        "doc_type": "credit_report",
        "fields": {
            "credit_score": 771,
            "enquiries_30d": 0,
            "tradelines": [{"lender": "Mulshi Auto Finance", "account_last4": "4512"}],
        },
        "unverified_fields": ["credit_score"],
        "grounded": True,
    }
    assert facts_view({"fields": None})["fields"] == {}


def test_page_text_prefers_machine_text():
    machine = "Salary slip for August 2026, net pay 82,500, printed by the employer."
    assert page_text({"format_parser": machine, "ai_analysis": [{"content": "vision"}]}) == machine
    # Too little machine text (a scan): the vision transcription is used.
    assert page_text({"paddleocr": "x", "ai_analysis": [{"content": "vision text"}]}) == "vision text"
    assert page_text({"segment_type": "VIDEO", "format_parser": machine}) == ""


def test_fit_history_keeps_newest_turns_alternating():
    history = [
        {"role": "user", "content": "old " * 500},
        {"role": "assistant", "content": "old answer " * 100},
        {"role": "user", "content": "q1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]
    # The oldest question does not fit; the answer it leaves at the start is dropped
    # and the two consecutive questions are merged (Converse roles must alternate).
    assert fit_history(history, 1200) == [
        {"role": "user", "content": "q1\n\nq2"},
        {"role": "assistant", "content": "a2"},
    ]
    # A long turn is cut to the room left.
    (kept,) = fit_history([{"role": "user", "content": "x" * 5000}], 1000)
    assert len(kept["content"]) == 1000 and kept["content"].endswith("cut to fit the size limit]")

    messages = build_messages([{"role": "user", "content": "unanswered"}], "<ctx>", "new?")
    assert messages == [{"role": "user", "content": [{"text": "unanswered\n\n<ctx>\n\nQuestion: new?"}]}]


def test_usage_window_and_pagination():
    now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    table = FakeDdbTable(page_size=1)
    other = [
        {"PK": f"PROJ#{PROJECT_ID}", "SK": "FACTS#d1", "input_tokens": 999},
        {"PK": f"PROJ#{PROJECT_ID}", "SK": "DOC#d1"},
        {"PK": "PROJ#proj_other", "SK": f"FCASK#{(now - timedelta(days=1)).isoformat()}#x", "input_tokens": 999},
    ]
    table.items.extend(other)
    with patch("app.ddb.ask_usage.get_table", return_value=table):
        for days_ago, tokens in ((8, (5000, 5000)), (6.9, (1000, 100)), (1, (2000, 200)), (0, (10, 1))):
            put_ask_usage(
                PROJECT_ID,
                model_id=MODEL_ID,
                input_tokens=tokens[0],
                output_tokens=tokens[1],
                cost_usd=cost_usd(*tokens),
                retention_days=7,
                now=now - timedelta(days=days_ago),
            )
        usage = sum_ask_usage(PROJECT_ID, window_days=7, now=now)

    assert usage == {
        "window_days": 7,
        "calls": 3,  # the call 8 days ago is outside the window
        "input_tokens": 3010,
        "output_tokens": 301,
        "cost_usd": pytest.approx(cost_usd(1000, 100) + cost_usd(2000, 200) + cost_usd(10, 1), abs=1e-12),
    }
    assert len(table.queries) == 3  # one page per item
    assert table.queries[0]["ProjectionExpression"] == "input_tokens, output_tokens, cost_usd"
    expires = sorted(i["expires_at"] for i in table.ledger() if i["PK"] == f"PROJ#{PROJECT_ID}")
    assert expires[-1] == int(now.timestamp()) + 7 * 86400


def test_openapi_documents_the_ask_contract():
    spec = app.openapi()
    schemas = spec["components"]["schemas"]
    assert set(schemas["AskRequest"]["properties"]) == {"question", "checklist_id", "applicant", "history"}
    assert schemas["AskRequest"]["required"] == ["question"]
    assert set(schemas["AskResponse"]["properties"]) == {
        "answer",
        "model_id",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "pricing",
        "grounded_on",
    }
    assert set(schemas["AskUsageResponse"]["properties"]) == {
        "window_days",
        "calls",
        "input_tokens",
        "output_tokens",
        "cost_usd",
    }
    post = spec["paths"]["/projects/{project_id}/file-check/ask"]["post"]
    assert post["tags"] == ["file-check"]
    assert {"400", "404", "422", "502", "503"} <= set(post["responses"])
    assert "/projects/{project_id}/file-check/usage" in spec["paths"]
