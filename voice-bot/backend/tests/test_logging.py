# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import io
import json
import logging

from loguru import logger

from logging_setup import redact, setup_logging


def test_redact_masks_identifiers():
    line = (
        "pan CKRPK7314M aadhaar 1234 5678 9012 phone +91 9876543210 or 9876543210 "
        "mail sneha@example.com token=eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl "
        "call proj_DemoSneha_LoanFile01 status=404"
    )
    out = redact(line)
    for secret in ("CKRPK7314M", "1234 5678 9012", "9876543210", "sneha@example.com", "eyJhbGci"):
        assert secret not in out
    assert "[PII:PAN]" in out and "[PII:AADHAAR]" in out and "[PII:PHONE]" in out and "[PII:EMAIL]" in out
    assert "proj_DemoSneha_LoanFile01" in out and "status=404" in out  # ids and codes stay useful


def _capture(level="INFO", third_party_debug=False, monkeypatch=None):
    stream = io.StringIO()
    if monkeypatch is not None:
        monkeypatch.setenv("LOG_THIRD_PARTY_DEBUG", "true" if third_party_debug else "false")
    setup_logging(level, as_json=True, stream=stream)
    return stream


def _lines(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_json_lines_with_bound_fields_and_no_pii(monkeypatch):
    stream = _capture(monkeypatch=monkeypatch)
    logger.bind(event="tool", tool="file_status", status="ok", note="caller said CKRPK7314M").info("tool call for 9876543210")
    try:
        raise ValueError("payload had sneha@example.com")
    except ValueError:
        logger.bind(event="boom").exception("failed")
    logger.remove()
    lines = _lines(stream)
    assert lines[0]["event"] == "tool" and lines[0]["tool"] == "file_status" and lines[0]["level"] == "INFO"
    assert "9876543210" not in json.dumps(lines) and "CKRPK7314M" not in json.dumps(lines)
    assert lines[1]["exc_type"] == "ValueError" and "sneha@example.com" not in json.dumps(lines[1])


def test_third_party_debug_text_is_dropped(monkeypatch):
    stream = _capture("DEBUG", monkeypatch=monkeypatch)
    logger.patch(lambda r: r.update(name="pipecat.services.aws.tts")).debug("Generating TTS [आपकी salary slip बाकी है]")
    logger.patch(lambda r: r.update(name="tools")).debug("own debug line")
    logger.patch(lambda r: r.update(name="pipecat.pipeline.worker")).info("pipeline started")
    logger.remove()
    messages = [line["msg"] for line in _lines(stream)]
    assert "own debug line" in messages and "pipeline started" in messages
    assert not any("salary slip" in m for m in messages)


def test_info_level_hides_debug(monkeypatch):
    stream = _capture("INFO", monkeypatch=monkeypatch)
    logger.patch(lambda r: r.update(name="tools")).debug("hidden")
    logger.patch(lambda r: r.update(name="tools")).info("shown")
    logger.remove()
    assert [line["msg"] for line in _lines(stream)] == ["shown"]


def test_uvicorn_access_log_is_off(monkeypatch):
    _capture(monkeypatch=monkeypatch)
    assert logging.getLogger("uvicorn.access").disabled
    logger.remove()


def test_phone_url_secrets_are_masked_in_handshake_logs(monkeypatch):
    key = "Kq7" * 12  # PHONE_URL_KEY
    sealed = "gAAAAABmZ3J1bmNoX3Rva2VuX2V4YW1wbGVfdmFsdWVfMTIzNDU2Nzg5MA=="
    stream = _capture(monkeypatch=monkeypatch)
    uvicorn_log = logging.getLogger("uvicorn.error")
    # Uvicorn logs every WebSocket handshake with its path, at INFO.
    uvicorn_log.info('%s - "WebSocket %s" [accepted]', ("10.0.0.5", 51000), f"/phone/exotel/ws/{key}?sample-rate=16000")
    uvicorn_log.info('%s - "WebSocket %s" [accepted]', ("10.0.0.5", 51001), f"/phone/exotel/ws/{key}/{sealed}?sample-rate=16000")
    uvicorn_log.info('%s - "WebSocket %s" 403', ("10.0.0.5", 51002), f"/phone/plivo/ws/{sealed}")
    logger.info(f"POST /phone/plivo/answer?ctx={sealed} and /phone/plivo/transfer/{sealed} and /phone/exotel/status/{key}")
    logger.remove()
    out = json.dumps(_lines(stream))
    assert key not in out and sealed not in out and "gAAAA" not in out
    assert out.count("/phone/exotel/ws/[KEY]") == 2 and "/phone/plivo/ws/[TOKEN]" in out and "?sample-rate=16000" in out
    assert "ctx=[REDACTED]" in out and "/phone/plivo/transfer/[TOKEN]" in out and "/phone/exotel/status/[KEY]" in out


def test_ids_with_digit_groups_are_not_mistaken_for_aadhaar():
    assert redact("call 12345678-1234-1234-1234-123456789abc ended") == "call 12345678-1234-1234-1234-123456789abc ended"
    assert redact("aadhaar 1234-5678-9012 and 12" "3456789012") == "aadhaar [PII:AADHAAR] and [PII:AADHAAR]"
