# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Cross-check of the browser protocol against the backend's real serializer code.

Checks backend/serializers.py BrowserJsonSerializer (the Mumbai rewrite) and, if it still exists,
the original backend/JsonSerializer.py. The browser side is the real frontend code
(src/lib/protocol.js), run through js_codec.mjs.

Needs a Python with pipecat-ai 1.3.0 (the backend requirements) and Node 20+:
    python test/conformance/test_json_serializer.py      (from the frontend folder)
Exit code 0 = the frontend and the backend agree on every frame and event.
"""
import asyncio
import json
import math
import os
import pathlib
import struct
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(HERE)  # the backend modules may run load_dotenv() on import: keep them away from backend/.env

from pipecat.frames.frames import (  # noqa: E402
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageUrgentFrame,
)

NODE = os.environ.get("NODE", "node")
RATE = 16000

# Every UI event the Mumbai backend emits (bot.py, tools.py), as the browser must understand it.
UI_EVENTS = [
    ({"event": "call_started", "call_id": "c-42", "language": "hi"}, "callStarted"),
    ({"event": "user_transcript", "text": "Sneha Kulkarni ki file mein kya baaki hai?"}, "caption"),
    ({"event": "tool", "name": "file_status", "status": "started"}, "tool"),
    ({"event": "file_status", "verdict": "NOT READY", "missing": ["June 2026 salary slip"], "mismatches": 1}, "result"),
    ({"event": "eligibility", "best_lender": "Demo Bank", "amount": "4.5 lakh"}, "result"),
    ({"event": "reminder", "template": "T1", "language": "hi", "channel": "whatsapp",
      "text": "नमस्ते Sneha, बाकी documents: June 2026 salary slip", "placeholders": ["upload_link"]}, "result"),
    ({"event": "language", "language": "mr"}, "language"),
    ({"event": "bot_transcript", "text": "Aapki file mein", "interrupted": True}, "caption"),
    ({"event": "call_ended", "reason": "time_cap"}, "callEnded"),
    ({"event": "recording", "status": "uploaded", "document_id": "d-1"}, "recording"),
]


def js(payload: dict) -> dict:
    done = subprocess.run([NODE, str(HERE / "js_codec.mjs")], input=json.dumps(payload),
                          capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def mic_samples() -> list[int]:
    """One 100 ms microphone frame (1600 samples), including the PCM16 extremes."""
    mic = [0, 1, -1, 32767, -32768, 256, 12345, -12345]
    return mic + [int(12000 * math.sin(i / 5.0)) for i in range(1600 - len(mic))]


async def check_audio(ser, mic: list[int], mic_text: str) -> str:
    """browser -> bot media, bot -> browser media and interruption."""
    frame = await ser.deserialize(mic_text)
    if not isinstance(frame, InputAudioRawFrame):
        return f"FAIL: browser media frame gave {frame!r}"
    if frame.audio != struct.pack(f"<{len(mic)}h", *mic):
        return "FAIL: browser media frame decoded to different PCM bytes"
    if frame.sample_rate != RATE or frame.num_channels != 1:
        return f"FAIL: {frame.sample_rate} Hz / {frame.num_channels} ch, expected {RATE} Hz mono"
    bot = [int(9000 * math.sin(i / 3.0)) for i in range(640)]
    media = await ser.serialize(OutputAudioRawFrame(audio=struct.pack(f"<{len(bot)}h", *bot),
                                                    sample_rate=RATE, num_channels=1))
    interruption = await ser.serialize(InterruptionFrame())
    parsed = js({"parse": [media, interruption]})["parsed"]
    if parsed[0] != {"kind": "media", "samples": bot}:
        return "FAIL: bot audio decoded differently in the browser"
    if parsed[1] != {"kind": "interruption"}:
        return f"FAIL: interruption parsed as {parsed[1]}"
    return (f"OK: mic {len(mic)} samples / {len(frame.audio)} bytes / {frame.sample_rate} Hz mono in; "
            f"bot audio {len(bot)} samples and {interruption} out")


async def check_ui_events(ser) -> str:
    texts = []
    for event, _ in UI_EVENTS:
        out = await ser.serialize(OutputTransportMessageUrgentFrame(message=event))
        if not out:
            return f"FAIL: the serializer drops {event['event']}"
        texts.append(out)
    parsed = js({"parse": texts})["parsed"]
    kinds = [p["kind"] for p in parsed]
    expected = [kind for _, kind in UI_EVENTS]
    if kinds != expected:
        return f"FAIL: parsed kinds {kinds}, expected {expected}"
    user, bot = parsed[1], parsed[7]
    if user["role"] != "user" or not user["turn"] or user["text"] != UI_EVENTS[1][0]["text"]:
        return f"FAIL: caller caption {user}"
    if bot["role"] != "bot" or bot["interrupted"] is not True:
        return f"FAIL: bot caption {bot}"
    if parsed[3]["card"]["missing"] != ["June 2026 salary slip"] or parsed[4]["card"]["amount"] != "4.5 lakh":
        return f"FAIL: result cards {parsed[3]} {parsed[4]}"
    if parsed[5]["card"]["language"] != "hi-IN" or parsed[6]["language"] != "mr-IN":
        return "FAIL: language codes"
    return f"OK: {len(UI_EVENTS)} UI events (captions, lookups, result cards, language, call start/end, recording)"


async def main() -> int:
    mic = mic_samples()
    mic_text = js({"encode": mic})["encoded"]
    report: dict[str, str] = {}

    try:
        from serializers import BrowserJsonSerializer  # backend/serializers.py (Mumbai rewrite)
    except Exception as e:  # noqa: BLE001
        BrowserJsonSerializer = None
        report["BrowserJsonSerializer"] = f"MISSING: backend/serializers.py not importable ({type(e).__name__}: {e})"
    if BrowserJsonSerializer is not None:
        ser = BrowserJsonSerializer(RATE)
        report["BrowserJsonSerializer audio"] = await check_audio(ser, mic, mic_text)
        report["BrowserJsonSerializer events"] = await check_ui_events(ser)

    try:
        from JsonSerializer import JsonSerializer  # the original sample's serializer
    except Exception:  # noqa: BLE001
        report["JsonSerializer (original)"] = "SKIPPED: removed by the Mumbai rewrite"
    else:
        legacy = JsonSerializer(RATE)
        report["JsonSerializer (original) audio"] = await check_audio(legacy, mic, mic_text)
        caption = await legacy.serialize(OutputTransportMessageUrgentFrame(message=UI_EVENTS[1][0]))
        report["JsonSerializer (original) captions"] = (
            "OK" if caption else "NONE: serialize() returns None for transport messages (audio only)")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    failed = any(v.startswith(("FAIL", "MISSING")) for v in report.values())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
