# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""One voice call: the Pipecat pipeline, Mumbai only.

caller audio -> Silero VAD + smart-turn -> Amazon Transcribe streaming (hi-IN / en-IN / mr-IN)
  -> Bedrock Kimi K2.5 (fallback gpt-oss-120b), in-Region, with the loan tools
  -> Amazon Polly Kajal (neural) -> caller
Both sides are recorded (stereo WAV) and uploaded to the IDP's Telecaller QA
project after the call, then deleted here.

Call rules wired here, not left to the model:
- the opening line (AI assistant + recorded call) is fixed text and the caller
  cannot talk over it;
- the caller is muted while a tool runs (a "haan" would otherwise cancel it),
  and a fixed filler line covers the wait;
- silence: two "are you there?" prompts, then goodbye; hard cap CALL_MAX_SECONDS;
- every sentence passes the compliance filter before it is spoken.
Fixed lines are captioned in the browser like the model's replies, and the
browser is told why the bot ended a call (call_ended).
A Transcribe error ends the call only if the stream is still down after
STT_GRACE_S (Pipecat reconnects a dropped stream by itself).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from loguru import logger
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    EndFrame,
    EndTaskFrame,
    Frame,
    FunctionCallResultProperties,
    OutputTransportMessageUrgentFrame,
    STTUpdateSettingsFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.aws.stt import AWSTranscribeSTTService
from pipecat.turns.user_mute.base_user_mute_strategy import BaseUserMuteStrategy
from pipecat.turns.user_mute.function_call_user_mute_strategy import FunctionCallUserMuteStrategy
from pipecat.workers.runner import WorkerRunner

from config import Settings
from idp_client import IdpClient
from llm import MumbaiBedrockLLMService
from prompt import (
    FILLERS,
    GOODBYE,
    IDLE_PROMPT,
    LANG_NAME,
    SAY_AGAIN,
    TECH_PROBLEM,
    TIME_UP,
    TRANSFER,
    Persona,
    greeting,
    short_lang,
    system_prompt,
)
from recording import CallRecorder
from reminders import first_name
from speech import SAMPLE_RATE, ComplianceTextFilter, create_stt, create_tts, polly_rate_for, transcribe_language
from tools import CallSession, LoanTools, run_tool, tool_schemas

TOOL_TIMEOUT_S = 25.0
STT_GRACE_S = 6.0  # Pipecat retries a dropped Transcribe stream 3 times with back-off within this
# session.ended_reason -> the browser's end-of-call reason (frontend END_REASON)
_BROWSER_END_REASON = {"time_cap": "time_cap", "silence": "silence", "transferred": "transferred"}


class GreetingUserMuteStrategy(BaseUserMuteStrategy):
    """Mute the caller until the opening line (AI + recording notice) has been spoken.

    Unlike Pipecat's MuteUntilFirstBotComplete, it gives up after ``max_s`` so a
    failed greeting can never leave the caller muted for the whole call.
    """

    def __init__(self, max_s: float = 25.0):
        super().__init__()
        self._max_s = max_s
        self._started: float | None = None
        self._done = False

    async def process_frame(self, frame: Frame) -> bool:
        await super().process_frame(frame)
        if self._started is None:
            self._started = time.monotonic()
        if isinstance(frame, BotStoppedSpeakingFrame):
            self._done = True
        if not self._done and time.monotonic() - self._started > self._max_s:
            self._done = True
        return not self._done


class CallSlots:
    """Cap on concurrent calls across all channels (each holds a Transcribe stream and VAD/turn models)."""

    def __init__(self, limit: int = 8):
        self.limit = limit
        self.active = 0

    def take(self) -> bool:
        if self.active >= self.limit:
            return False
        self.active += 1
        return True

    def give(self) -> None:
        self.active = max(0, self.active - 1)


@dataclass
class BotDeps:
    """What a call needs. Tests swap the factories for fakes."""

    settings: Settings
    idp: IdpClient
    stt_factory: Callable[[str], object] | None = None
    tts_factory: Callable[[list], object] | None = None
    llm_factory: Callable[[str], MumbaiBedrockLLMService] | None = None
    vad_factory: Callable[[], object] | None = None
    recorder_factory: Callable[[CallSession], CallRecorder | None] | None = None
    user_turn_strategies: object | None = None  # None: Pipecat's default (VAD start + smart-turn stop)
    on_call_end: Callable[[CallSession, dict], Awaitable[None]] | None = None
    slots: CallSlots = field(default_factory=CallSlots)


def new_call_id() -> str:
    return uuid.uuid4().hex[:12]


async def run_call(
    transport,
    session: CallSession,
    deps: BotDeps,
    on_transfer: Callable[[CallSession], Awaitable[dict]] | None = None,
) -> dict:
    """Run one call on an already-created Pipecat transport. Returns a small summary."""
    settings = deps.settings
    persona = Persona(assistant_name=settings.assistant_name, dsa_name=settings.dsa_name)
    session.language = short_lang(session.language)
    log = logger.bind(call_id=session.call_id, channel=session.channel)

    compliance = ComplianceTextFilter(lambda: session.language)
    stt = deps.stt_factory(session.language) if deps.stt_factory else create_stt(settings.region, session.language)
    if deps.tts_factory:
        tts = deps.tts_factory([compliance])
    else:
        # Plivo lines are 8 kHz: Polly renders 8 kHz so the line's QQ resampler sees no sound above 4 kHz.
        tts = create_tts(
            settings.region, settings.polly_voice, text_filters=[compliance], polly_sample_rate=polly_rate_for(session.channel)
        )
    instructions = system_prompt(persona, session.channel, session.language, applicant=session.applicant_hint)
    if deps.llm_factory:
        llm = deps.llm_factory(instructions)
    else:
        llm = MumbaiBedrockLLMService(
            model_chain=list(settings.model_chain),
            region=settings.region,
            system_instruction=instructions,
            model_params=settings.model_params,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
            first_event_timeout_s=settings.llm_first_event_timeout_s,
            first_output_timeout_s=settings.llm_first_output_timeout_s,
        )

    worker_ref: dict[str, PipelineWorker] = {}

    async def emit(event: dict) -> None:
        if session.channel == "browser" and "worker" in worker_ref:
            await worker_ref["worker"].queue_frame(OutputTransportMessageUrgentFrame(message=event))

    async def caption(text: str) -> None:
        """Browser caption for a fixed line (the model's own replies are captioned by the aggregator)."""
        await emit({"event": "bot_transcript", "text": text})

    async def say_from_llm(service, text: str) -> None:
        """Speak a fixed line from inside a tool handler (queued in order with the model's speech)."""
        await service.push_frame(TTSSpeakFrame(text, append_to_context=False))
        await caption(text)

    async def announce_end(reason: str) -> None:
        await emit({"event": "call_ended", "reason": reason})

    session.emit = emit
    tools = LoanTools(deps.idp, settings, session)
    schemas = tool_schemas(session.channel)

    # ---------------------------------------------------------------- tool handlers
    async def h_file_status(params):
        session.tools_used.append("file_status")
        result = await run_tool("file_status", tools.file_status(str(params.arguments.get("applicant", ""))), TOOL_TIMEOUT_S)
        await params.result_callback(result)

    async def h_eligibility(params):
        session.tools_used.append("eligibility")
        result = await run_tool("eligibility", tools.eligibility(str(params.arguments.get("applicant", ""))), TOOL_TIMEOUT_S)
        await params.result_callback(result)

    async def h_reminder(params):
        session.tools_used.append("reminder")
        args = params.arguments
        result = await run_tool(
            "reminder",
            tools.reminder(str(args.get("applicant", "")), str(args.get("language") or session.language), str(args.get("channel") or "whatsapp")),
            TOOL_TIMEOUT_S,
        )
        await params.result_callback(result)

    async def h_verify_caller(params):
        session.tools_used.append("verify_caller")
        args = params.arguments
        result = await run_tool(
            "verify_caller", tools.verify_caller(str(args.get("applicant", "")), str(args.get("date_of_birth", ""))), TOOL_TIMEOUT_S
        )
        await params.result_callback(result)

    async def h_switch_language(params):
        session.tools_used.append("switch_language")
        lang = short_lang(str(params.arguments.get("language", "")))
        if lang != session.language:
            session.language = lang
            await params.llm.push_frame(
                STTUpdateSettingsFrame(delta=AWSTranscribeSTTService.Settings(language=transcribe_language(lang))),
                FrameDirection.UPSTREAM,
            )
            await emit({"event": "language", "language": lang})
            log.bind(event="language_switch", language=lang).info("language switched")
        await params.result_callback({"switched": True, "language": LANG_NAME[lang], "say": f"Reply in {LANG_NAME[lang]} from now on."})

    async def h_end_call(params):
        session.tools_used.append("end_call")
        session.ended_reason = str(params.arguments.get("reason") or "done")[:40]
        if not llm.spoke_this_response:
            await say_from_llm(params.llm, GOODBYE[session.language])
        await params.result_callback({"ok": True}, properties=FunctionCallResultProperties(run_llm=False))
        await announce_end("end_call")
        await params.llm.push_frame(EndTaskFrame(reason="end_call"), FrameDirection.UPSTREAM)

    async def h_transfer(params):
        session.tools_used.append("transfer_to_human")
        if on_transfer is None:
            await params.result_callback({"transferred": False, "say": "This call cannot be transferred. Say a telecaller will call back."})
            return
        outcome = await on_transfer(session)
        if not outcome.get("transferred"):
            await params.result_callback({"transferred": False, "say": outcome.get("say") or "No telecaller is available now. Say a telecaller will call back."})
            return
        session.transfer_requested = True
        session.ended_reason = "transferred"
        await say_from_llm(params.llm, TRANSFER[session.language])
        await params.result_callback({"transferred": True}, properties=FunctionCallResultProperties(run_llm=False))
        if outcome.get("end_stream"):
            await announce_end("transferred")
            await params.llm.push_frame(EndTaskFrame(reason="transfer"), FrameDirection.UPSTREAM)

    handlers = {
        "file_status": h_file_status,
        "eligibility": h_eligibility,
        "reminder": h_reminder,
        "verify_caller": h_verify_caller,
        "switch_language": h_switch_language,
        "end_call": h_end_call,
        "transfer_to_human": h_transfer,
    }

    def never_raises(name, handler):
        # A handler that raised would leave its call without a result: the caller would stay
        # muted (FunctionCallUserMuteStrategy) until Pipecat's timeout. Always answer.
        async def safe(params):
            try:
                await handler(params)
            except Exception as e:  # noqa: BLE001
                log.bind(event="tool_crash", tool=name, error=type(e).__name__).warning("tool handler failed")
                await params.result_callback({"error": "failed", "say": "Apologise and offer a call back from the team."})

        return safe

    for schema in schemas:
        llm.register_function(schema.name, never_raises(schema.name, handlers[schema.name]), timeout_secs=TOOL_TIMEOUT_S + 10)

    @llm.event_handler("on_function_calls_started")
    async def _on_tools(service, function_calls):
        names = [fc.function_name for fc in function_calls]
        filler_for = next((n for n in names if n in FILLERS), None)
        if filler_for and not service.spoke_this_response:
            await say_from_llm(service, FILLERS[filler_for][session.language])
        for name in names:
            await emit({"event": "tool", "name": name, "status": "started"})

    # ---------------------------------------------------------------- context & pipeline
    opening = greeting(persona, session.language, session.channel, first_name(session.applicant_hint) if session.applicant_hint else None)
    context = LLMContext(
        messages=[
            {"role": "user", "content": f"(Call connected on {session.channel}. Caller language: {LANG_NAME[session.language]}.)"},
            {"role": "assistant", "content": opening},
        ],
        tools=ToolsSchema(standard_tools=schemas),
    )
    vad = deps.vad_factory() if deps.vad_factory else SileroVADAnalyzer(params=VADParams(min_volume=settings.vad_min_volume))
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=vad,
            user_turn_strategies=deps.user_turn_strategies,
            user_mute_strategies=[GreetingUserMuteStrategy(), FunctionCallUserMuteStrategy()],
            user_idle_timeout=settings.user_idle_s,
        ),
    )
    recorder = None
    if deps.recorder_factory is not None:
        recorder = deps.recorder_factory(session)
    elif settings.recording_enabled and deps.idp.configured:
        recorder = CallRecorder(settings, deps.idp, session)

    stages = [transport.input()]
    if recorder is not None:
        stages.append(recorder.tap)  # the caller's audio, also while muted (see recording.py)
    stages += [stt, aggregators.user(), llm, tts, transport.output()]
    if recorder is not None:
        stages.append(recorder.processor)
    stages.append(aggregators.assistant())
    worker = PipelineWorker(
        Pipeline(stages),
        params=PipelineParams(audio_in_sample_rate=SAMPLE_RATE, audio_out_sample_rate=SAMPLE_RATE),
        enable_rtvi=False,
        idle_timeout_secs=settings.call_max_s + 60,
    )
    worker_ref["worker"] = worker

    async def speak(text: str) -> None:
        await worker.queue_frames([TTSSpeakFrame(text, append_to_context=False)])
        await caption(text)

    session.speak = speak

    idle_prompts = {"n": 0}
    cap_task: dict[str, asyncio.Task] = {}
    errors = {"llm": 0, "stt": 0, "tts": 0, "other": 0}
    ending = {"yes": False}
    stt_watch: dict[str, asyncio.Task] = {}

    async def end_with(line: str, reason: str) -> None:
        if ending["yes"]:
            return
        ending["yes"] = True
        if session.ended_reason is None:
            session.ended_reason = reason
        await announce_end(_BROWSER_END_REASON.get(reason, "technical" if reason.endswith("_error") else reason))
        await worker.queue_frames([TTSSpeakFrame(line, append_to_context=False), EndFrame()])
        await caption(line)

    def stt_alive() -> bool:
        """Transcribe's WebSocket is open, or Pipecat is reconnecting it."""
        if getattr(stt, "_reconnect_in_progress", False):
            return True
        ws = getattr(stt, "_websocket", None)
        state = getattr(getattr(ws, "state", None), "name", None)
        return ws is not None and state in (None, "OPEN")

    async def stt_watchdog() -> None:
        await asyncio.sleep(STT_GRACE_S)
        if not stt_alive():
            log.bind(event="stt_down").warning("Transcribe stream did not come back")
            await end_with(TECH_PROBLEM[session.language], "stt_error")

    @worker.event_handler("on_pipeline_error")
    async def _on_error(_worker, frame):
        source = frame.processor
        kind = "llm" if source is llm else "stt" if source is stt else "tts" if source is tts else "other"
        errors[kind] += 1
        log.bind(event="pipeline_error", source=kind, fatal=bool(frame.fatal), count=errors[kind]).warning("pipeline error")
        if kind == "stt" and not frame.fatal:
            # Pipecat reconnects a dropped Transcribe stream; end the call only if it stays down.
            if "t" not in stt_watch or stt_watch["t"].done():
                stt_watch["t"] = asyncio.create_task(stt_watchdog())
        elif kind == "stt" or (kind == "llm" and errors["llm"] >= 2) or (kind == "tts" and errors["tts"] >= 3):
            await end_with(TECH_PROBLEM[session.language], f"{kind}_error")
        elif kind == "llm":
            await speak(SAY_AGAIN[session.language])

    async def call_cap() -> None:
        await asyncio.sleep(settings.call_max_s)
        log.bind(event="call_cap").info("call reached the time cap")
        await end_with(TIME_UP[session.language], "time_cap")

    @transport.event_handler("on_client_connected")
    async def _on_connected(*_args):
        log.bind(event="call_start", language=session.language, outbound=session.outbound).info("call connected")
        if recorder is not None:
            try:
                await recorder.start()
            except OSError as e:
                log.bind(event="recording_start_failed", error=type(e).__name__).warning("call continues without a recording")
        cap_task["t"] = asyncio.create_task(call_cap())
        await emit({"event": "call_started", "call_id": session.call_id, "language": session.language})
        await speak(opening)

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnected(*_args):
        if session.ended_reason is None:
            session.ended_reason = "caller_hung_up"
        await worker.cancel()

    user_agg, assistant_agg = aggregators.user(), aggregators.assistant()

    @user_agg.event_handler("on_user_turn_idle")
    async def _on_idle(_aggregator):
        idle_prompts["n"] += 1
        if idle_prompts["n"] <= 2:
            await speak(IDLE_PROMPT[session.language])
        else:
            await end_with(GOODBYE[session.language], "silence")

    @user_agg.event_handler("on_user_turn_stopped")
    async def _on_user_turn(_aggregator, _strategy, message):
        idle_prompts["n"] = 0
        await emit({"event": "user_transcript", "text": getattr(message, "content", "")})

    @assistant_agg.event_handler("on_assistant_turn_stopped")
    async def _on_bot_turn(_aggregator, message):
        text = getattr(message, "content", "")
        if text:
            await emit({"event": "bot_transcript", "text": text, "interrupted": bool(getattr(message, "interrupted", False))})

    runner = WorkerRunner(handle_sigint=False)
    started = time.monotonic()
    try:
        await runner.add_workers(worker)
        await runner.run()
    finally:
        for task in (cap_task.get("t"), stt_watch.get("t")):
            if task is not None:
                task.cancel()
        session.emit = None
        session.speak = None
        document_id = None
        if recorder is not None:
            try:
                document_id = await recorder.finish_and_upload()
            except Exception as e:  # noqa: BLE001
                log.bind(event="recording_failed", error=type(e).__name__).warning("recording not uploaded")
        summary = {
            "call_id": session.call_id,
            "channel": session.channel,
            "seconds": round(time.monotonic() - started, 1),
            "ended": session.ended_reason or "pipeline_ended",
            "tools": ",".join(session.tools_used),
            "verdict": session.verdict,
            "model": getattr(llm, "last_model", None),
            "recording_document_id": document_id,
            "compliance_rewrites": compliance.rewrites,
        }
        log.bind(event="call_end", **{k: v for k, v in summary.items() if k not in ("call_id", "channel")}).info("call ended")
        if deps.on_call_end is not None:
            try:
                await deps.on_call_end(session, summary)
            except Exception:  # noqa: BLE001
                log.warning("on_call_end hook failed")
    return summary


def websocket_transport(websocket, serializer, audio_out_10ms_chunks: int = 4):
    from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

    return FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_in_sample_rate=SAMPLE_RATE,
            audio_out_enabled=True,
            audio_out_sample_rate=SAMPLE_RATE,
            add_wav_header=False,
            serializer=serializer,
            audio_out_10ms_chunks=audio_out_10ms_chunks,
        ),
    )


def webrtc_transport(connection):
    """WhatsApp calls arrive as WebRTC (aiortc). Imported lazily: aiortc is only needed for WhatsApp."""
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    return SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_in_sample_rate=SAMPLE_RATE,
            audio_out_enabled=True,
            audio_out_sample_rate=SAMPLE_RATE,
        ),
    )
