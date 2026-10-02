# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import httpx
import pytest

import fixtures
from fakes import IDP_URL, S3_URL, FakeIdp
from idp_client import IdpClient, IdpError, IdpNotConfigured, _path_label


async def test_every_call_is_sigv4_signed_for_execute_api_in_mumbai():
    fake = FakeIdp()
    idp = IdpClient(IDP_URL, caller_id="voice-bot", transport=fake.transport())
    projects = await idp.list_projects(user="rohan.iyer")
    assert [p["name"] for p in projects] == [p["name"] for p in fixtures.PROJECTS]
    call = fake.calls[0]
    assert call.method == "GET" and call.path == "/projects"
    auth = call.headers["authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIAFAKEFAKEFAKEFAKE/")
    assert "/ap-south-1/execute-api/aws4_request" in auth and "x-user-id" in auth  # the audit label is signed
    assert call.headers["x-user-id"] == "rohan.iyer"
    assert call.headers["x-amz-security-token"] == "fake-session-token"
    assert fake.unsigned == 0
    await idp.aclose()


async def test_file_check_and_eligibility_bodies():
    fake = FakeIdp()
    idp = IdpClient(IDP_URL, transport=fake.transport())
    result = await idp.file_check(fixtures.SNEHA_PROJECT_ID, "Sneha Kulkarni")
    assert result["applicants"][0]["applicant"] == "Sneha Anil Kulkarni"
    assert fake.calls[-1].body == {"applicant": "Sneha Kulkarni"}
    assert fake.calls[-1].headers["x-user-id"] == "voice-bot"  # default label
    calc = await idp.eligibility(fixtures.SNEHA_PROJECT_ID, "CKRPK7314M")
    assert calc["best_lender"] == "Bajaj Finance" and fake.calls[-1].body == {"applicant": "CKRPK7314M"}
    with pytest.raises(IdpError) as err:
        await idp.eligibility(fixtures.SNEHA_PROJECT_ID, "Sneha Kulkarni")
    assert err.value.status == 404 and "No saved eligibility inputs" in err.value.detail


async def test_retries_once_on_503_then_gives_up():
    fake = FakeIdp(fail={r"/projects$": 503})
    idp = IdpClient(IDP_URL, transport=fake.transport())
    assert len(await idp.list_projects()) == 4  # second attempt succeeds
    assert len(fake.calls) == 2

    calls = []

    def always_503(request):
        calls.append(request)
        return httpx.Response(503, json={"detail": "down"})

    idp2 = IdpClient(IDP_URL, transport=httpx.MockTransport(always_503))
    with pytest.raises(IdpError) as err:
        await idp2.list_projects()
    assert err.value.status == 503 and len(calls) == 2


async def test_network_errors_become_idp_errors():
    def broken(request):
        raise httpx.ConnectError("no route")

    idp = IdpClient(IDP_URL, transport=httpx.MockTransport(broken))
    with pytest.raises(IdpError) as err:
        await idp.list_projects()
    assert err.value.status == 0


async def test_upload_flow_signs_the_api_but_not_s3(tmp_path):
    fake = FakeIdp()
    idp = IdpClient(IDP_URL, transport=fake.transport())
    path = tmp_path / "call.wav"
    path.write_bytes(b"RIFF....WAVEfmt " + b"\0" * 100)
    ticket = await idp.create_document(fixtures.QA_PROJECT_ID, "call.wav", "audio/wav", path.stat().st_size, use_transcribe=True,
                                       transcribe_options={"language_mode": "multi", "language_options": ["hi-IN"]})
    assert ticket.upload_url.startswith(S3_URL)
    await idp.put_file(ticket.upload_url, str(path), "audio/wav")
    await idp.mark_uploaded(fixtures.QA_PROJECT_ID, ticket.document_id)
    s3_put = next(c for c in fake.calls if c.path.startswith(S3_URL))
    assert "authorization" not in s3_put.headers  # the presigned URL carries the signature
    assert s3_put.headers["content-type"] == "audio/wav" and int(s3_put.headers["content-length"]) == path.stat().st_size
    assert fake.calls[-1].body == {"status": "uploaded"}


async def test_not_configured():
    with pytest.raises(IdpNotConfigured):
        await IdpClient("").list_projects()


def test_path_label_hides_ids():
    assert _path_label("projects/proj_abc/eligibility/calculate") == "projects/{id}/eligibility/calculate"
    assert _path_label("projects/p/documents/d/status") == "projects/{id}/documents/{id}/status"


def _signature_checks_out(request: httpx.Request) -> bool:
    """What API Gateway does: rebuild the canonical request from the bytes received, sign it, compare."""
    import re

    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    auth = request.headers["authorization"]
    signed = re.search(r"SignedHeaders=([^,\s]+)", auth).group(1).split(";")
    given = re.search(r"Signature=([0-9a-f]{64})", auth).group(1)
    # conftest.py's fake credentials: the ones the client signed with.
    creds = Credentials("AKIAFAKEFAKEFAKEFAKE", "fake/secret/key/for/tests/only/0000000000", "fake-session-token")
    received = AWSRequest(
        method=request.method,
        url=str(request.url),
        data=request.content,
        headers={k: v for k, v in request.headers.items() if k.lower() in signed},
    )
    received.context["timestamp"] = request.headers["x-amz-date"]
    signer = SigV4Auth(creds, "execute-api", "ap-south-1")
    canonical = signer.canonical_request(received)
    return signer.signature(signer.string_to_sign(received, canonical), received) == given


@pytest.mark.parametrize("call", ["list", "file_check", "eligibility", "inputs", "create", "status"])
async def test_signatures_verify_against_the_bytes_sent(call):
    seen: list[httpx.Request] = []
    fake = FakeIdp()

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return fake.handle(request)

    idp = IdpClient(IDP_URL + "/", transport=httpx.MockTransport(capture))  # the stack output ends with "/"
    pid = fixtures.SNEHA_PROJECT_ID
    run = {
        "list": lambda: idp.list_projects(user="asha.verma"),
        "file_check": lambda: idp.file_check(pid, "स्नेहा Kulkarni", user="asha.verma"),  # non-ASCII body
        "eligibility": lambda: idp.eligibility(pid, "CKRPK7314M"),
        "inputs": lambda: idp.eligibility_inputs(pid, "Sneha Anil Kulkarni"),  # query string with spaces
        "create": lambda: idp.create_document(fixtures.QA_PROJECT_ID, "voicebot_browser_x.wav", "audio/wav", 64000, True,
                                              {"language_mode": "multi", "language_options": ["hi-IN", "en-IN", "mr-IN"]}),
        "status": lambda: idp.mark_uploaded(fixtures.QA_PROJECT_ID, "doc-1"),
    }[call]
    await run()
    request = seen[-1]
    assert "//" not in request.url.path
    assert "x-user-id" in request.headers["authorization"] and "content-type" in request.headers["authorization"] or request.method == "GET"
    assert _signature_checks_out(request)
    # A changed byte breaks it (the check has teeth).
    tampered = httpx.Request(request.method, request.url, headers={**request.headers, "x-user-id": "someone-else"}, content=request.content)
    assert not _signature_checks_out(tampered)
    await idp.aclose()
