# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Bedrock Converse streaming LLM with a model fallback chain (AWS-sold models, ap-south-1).

Built on Pipecat 1.3.0's AWSBedrockLLMService, whose ``_process_context`` is
replaced to fix what a phone bot on open-weight models needs:

- Fallback: Kimi K2.5 -> gpt-oss-120b (LLM_MODEL_CHAIN), both invoked in-Region in
  ap-south-1 (config.py refuses cross-Region profiles). A model that
  errors, sends nothing within LLM_FIRST_EVENT_TIMEOUT_S, or has neither speech
  nor a tool call within LLM_FIRST_OUTPUT_TIMEOUT_S (long hidden reasoning) is
  skipped for this turn (and for a while, for access/throttling/output errors);
  the next model gets the same request. Nothing is retried once text has been
  spoken, except when the model then garbled a tool call.
- Tool calls: every toolUse block of a response is run (1.3.0 kept only the
  last one), also when a model ends with stopReason "end_turn" instead of
  "tool_use".
- Kimi on Bedrock ConverseStream is known to sometimes write a tool call as text
  (``<|tool_call_begin|>functions.x:0<|tool_call_argument_begin|>{...}``,
  ``<function=x>...``, or bare JSON) or inside its reasoning, with stopReason
  "end_turn" (public reports, Feb-Aug 2026). That markup is never spoken: the
  call is recovered when it is unambiguous, otherwise the turn goes to the next
  model. A turn with no speakable text and no tool call (e.g. a run of "!!!!"
  padding, seen on Bedrock Kimi in Apr 2026) also goes to the next model.
- Reasoning: reasoningContent deltas and <think>...</think> text are never spoken.
- Request shape: tool results go as text blocks (not every model accepts
  ``json`` blocks), the conversation starts with a user turn and ends with a
  user turn (text spoken before a tool call is moved back in front of it).
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from loguru import logger
from pipecat.frames.frames import FunctionCallFromLLM, LLMFullResponseEndFrame, LLMFullResponseStartFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.aws.llm import AWSBedrockLLMService
from pipecat.utils.tracing.service_decorators import traced_llm

# model id -> monotonic time until which it is skipped (shared by all calls in this process)
_SKIP_UNTIL: dict[str, float] = {}

# Compared in lower case: errors inside the event stream use camelCase (modelStreamErrorException).
_LONG_SKIP_CODES = {"accessdeniedexception", "resourcenotfoundexception", "unrecognizedclientexception"}
_SHORT_SKIP_CODES = {
    "throttlingexception",
    "servicequotaexceededexception",
    "modelnotreadyexception",
    "serviceunavailableexception",
    "modeltimeoutexception",
    "internalserverexception",
    "modelstreamerrorexception",
    # our own output checks (ModelOutputError)
    "emptyresponse",
    "junkoutput",
    "unparsedtoolcall",
    "slowoutput",
}
LONG_SKIP_S = 600.0
SHORT_SKIP_S = 30.0
MAX_SPOKEN_CHARS = 900  # per response; the prompt asks for at most 45 words


def reset_model_health() -> None:
    _SKIP_UNTIL.clear()


class ModelOutputError(Exception):
    """The model answered, but with nothing a caller can be given (see ``code``)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _error_code(error: BaseException) -> str:
    if isinstance(error, ClientError):
        return str(error.response.get("Error", {}).get("Code") or "ClientError")
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return "Timeout"
    code = getattr(error, "code", None) or getattr(error, "error_code", None)
    return str(code or type(error).__name__)


def _mark_failed(model_id: str, error: BaseException) -> str:
    code = _error_code(error)
    message = str(getattr(error, "response", {}).get("Error", {}).get("Message", "")) if isinstance(error, ClientError) else ""
    low = code.lower()
    if low in _LONG_SKIP_CODES or (low == "validationexception" and re.search(r"model identifier|not supported|on-demand throughput", message, re.I)):
        _SKIP_UNTIL[model_id] = time.monotonic() + LONG_SKIP_S
    elif low in _SHORT_SKIP_CODES or code == "Timeout":
        _SKIP_UNTIL[model_id] = time.monotonic() + SHORT_SKIP_S
    return code


class ThinkTagStripper:
    """Removes <think>/<thinking>/<reasoning> sections from streamed text."""

    _OPEN = re.compile(r"<(think|thinking|reasoning)>", re.I)
    _CLOSE = re.compile(r"</(think|thinking|reasoning)>", re.I)
    _PARTIAL = re.compile(r"</?[a-zA-Z]{0,9}$")

    def __init__(self):
        self._buf = ""
        self._inside = False

    def feed(self, text: str) -> str:
        self._buf += text
        out: list[str] = []
        while self._buf:
            if self._inside:
                m = self._CLOSE.search(self._buf)
                if not m:
                    self._buf = self._buf[-12:]
                    break
                self._buf = self._buf[m.end():]
                self._inside = False
                continue
            m = self._OPEN.search(self._buf)
            stray = self._CLOSE.search(self._buf)
            if stray and (not m or stray.start() < m.start()):
                out.append(self._buf[: stray.start()])
                self._buf = self._buf[stray.end():]
                continue
            if m:
                out.append(self._buf[: m.start()])
                self._buf = self._buf[m.end():]
                self._inside = True
                continue
            tail = self._PARTIAL.search(self._buf)
            if tail:
                out.append(self._buf[: tail.start()])
                self._buf = self._buf[tail.start():]
            else:
                out.append(self._buf)
                self._buf = ""
            break
        return "".join(out)

    def flush(self) -> str:
        rest = "" if self._inside else self._buf
        self._buf, self._inside = "", False
        return rest


# ------------------------------------------------------------------ speech guard
# Where tool-call markup starts in a text stream (it is never spoken).
_LEAK_START = re.compile(
    r"<\|"  # special tokens: <|tool_calls_section_begin|>, <|tool_call_begin|>, <|channel|>, ...
    r"|<function(?=[=\s>])"  # <function=name>...</function>
    r"|<tool_call(?=[\s>])"  # <tool_call>{"name": ..., "arguments": ...}</tool_call>
    r"|\{"  # bare JSON arguments (a spoken reply never contains a brace)
    r"|(?<![\w.])functions\.[A-Za-z_][\w-]*:\d"  # functions.file_status:0
)
# A chunk may end in the middle of a marker: hold such a tail until the next chunk.
_LEAK_TAILS = (
    re.compile(r"<[A-Za-z_|]{0,10}$"),
    re.compile(r"(?<![\w.])f(?:u(?:n(?:c(?:t(?:i(?:o(?:n(?:s(?:\.(?:[A-Za-z_][\w-]*:?)?)?)?)?)?)?)?)?)?)?$"),
)
_SPEAKABLE = re.compile(r"[^\W_]")  # a letter or a digit, in any script
JUNK_LIMIT = 48  # characters without a letter or digit before the model is given up on


class SpeechGuard:
    """Decides which streamed model text the caller hears.

    - <think> sections are dropped (ThinkTagStripper);
    - from the first tool-call marker on, the rest of the response is held in
      ``held`` (parsed for tool calls at the end, never spoken);
    - nothing is released before the text has a letter or digit (padding such as
      "!!!!" is not speech); ``junk`` turns true after JUNK_LIMIT such characters;
    - at most ``max_chars`` characters are spoken per response.
    """

    def __init__(self, max_chars: int = MAX_SPOKEN_CHARS):
        self._think = ThinkTagStripper()
        self._pending = ""
        self._started = False
        self.max_chars = max_chars
        self.held = ""
        self.leaked = False
        self.junk = False
        self.spoken_chars = 0
        self.truncated = False

    def feed(self, text: str) -> str:
        return self._process(self._think.feed(text), final=False)

    def flush(self) -> str:
        return self._process(self._think.flush(), final=True)

    def _process(self, text: str, final: bool) -> str:
        if self.leaked:
            self.held += text
            return ""
        buf, self._pending = self._pending + text, ""
        marker = _LEAK_START.search(buf)
        if marker:
            self.leaked = True
            self.held = buf[marker.start():]
            buf = buf[: marker.start()]
        elif not final:
            for tail in _LEAK_TAILS:
                m = tail.search(buf)
                if m:
                    self._pending = buf[m.start():]
                    buf = buf[: m.start()]
                    break
        if not self._started:
            if not _SPEAKABLE.search(buf):
                if final:
                    self._pending = ""
                else:
                    self._pending = buf + self._pending
                    if len(self._pending.strip()) >= JUNK_LIMIT:
                        self.junk = True
                return ""
            self._started = True
        return self._limit(buf)

    def _limit(self, text: str) -> str:
        room = self.max_chars - self.spoken_chars
        if room <= 0:
            self.truncated = self.truncated or bool(text.strip())
            return ""
        if len(text) > room:
            cut = text.rfind(" ", 0, room)
            text = text[: cut if cut > 0 else room]
            self.truncated = True
        self.spoken_chars += len(text)
        return text


# ------------------------------------------------------------------ leaked tool calls
_NATIVE_CALL = re.compile(
    r"<\|tool_call_begin\|>(.*?)(?=<\|tool_call_end\|>|<\|tool_call_begin\|>|<\|tool_calls_section_end\|>|\Z)", re.S
)
_XML_CALL = re.compile(r"<function=([\w.\-]+)>(.*?)(?=</function>|<function=|\Z)", re.S)
_XML_PARAM = re.compile(r"<parameter=([\w\-]+)>\s*(.*?)\s*(?=</parameter>|<parameter=|</function>|\Z)", re.S)
_TAGGED_CALL = re.compile(r"<tool_call>\s*(.*?)\s*(?=</tool_call>|<tool_call>|\Z)", re.S)


def _json_objects(text: str) -> list[dict]:
    """Every top-level JSON object in ``text``, in order."""
    decoder = json.JSONDecoder()
    found: list[dict] = []
    i = 0
    while True:
        i = text.find("{", i)
        if i < 0:
            return found
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            found.append(obj)
        i = end


def _first_json_object(text: str) -> dict | None:
    objects = _json_objects(text)
    return objects[0] if objects else None


def _named_call(obj: dict) -> tuple[str | None, dict | None]:
    """{"name": x, "arguments": {...}} (or parameters/input) -> (x, {...}); anything else -> (None, obj)."""
    name = obj.get("name")
    for key in ("arguments", "parameters", "input", "args"):
        value = obj.get(key)
        if isinstance(name, str) and isinstance(value, str):
            value = _first_json_object(value)
        if isinstance(name, str) and isinstance(value, dict):
            return name, value
    return None, obj


def _name_in_header(header: str) -> str | None:
    """'functions.file_status:0' / 'file_status:1' / 'file_status' -> 'file_status'; 'tooluse_x' -> None."""
    header = header.strip()
    m = re.search(r"functions\.([A-Za-z_][\w\-]*)", header)
    if m:
        return m.group(1)
    m = re.fullmatch(r"([A-Za-z_][\w\-]*?)(?::\d+)?", header)
    if m and not header.lower().startswith("tooluse"):
        return m.group(1)
    return None


def tool_specs_of(request: dict) -> dict[str, tuple[set, set]]:
    """name -> (property names, required names) of the tools offered in a Converse request."""
    specs: dict[str, tuple[set, set]] = {}
    for tool in (request.get("toolConfig") or {}).get("tools") or []:
        spec = tool.get("toolSpec") or {}
        name = spec.get("name")
        schema = (spec.get("inputSchema") or {}).get("json") or {}
        if name and name != "no_operation":
            specs[name] = (set((schema.get("properties") or {}).keys()), set(schema.get("required") or []))
    return specs


def _infer_tool(arguments: dict, specs: dict[str, tuple[set, set]]) -> str | None:
    keys = set(arguments)
    fits = [name for name, (props, required) in specs.items() if keys and required <= keys <= props]
    return fits[0] if len(fits) == 1 else None


def recover_tool_calls(text: str, specs: dict[str, tuple[set, set]], markers_only: bool = False) -> list[tuple[str, dict]]:
    """Tool calls a model wrote as text: [(name, arguments)], only for tools in ``specs``.

    Formats: Kimi's native markers, <function=name> (JSON body or <parameter=k> pairs),
    <tool_call>{...}</tool_call>, and (unless markers_only) bare JSON. A call without
    a name is kept only when exactly one offered tool fits its argument names.
    """
    raw: list[tuple[str | None, dict | None]] = []
    for m in _NATIVE_CALL.finditer(text):
        body = m.group(1)
        header, sep, args = body.partition("<|tool_call_argument_begin|>")
        if not sep:
            brace = body.find("{")
            header, args = (body[:brace], body[brace:]) if brace >= 0 else (body, "")
        raw.append((_name_in_header(header), _first_json_object(args) if args.strip() else {}))
    if not raw:
        for m in _XML_CALL.finditer(text):
            body = m.group(2)
            arguments = _first_json_object(body)
            if arguments is None:
                arguments = {k: v for k, v in _XML_PARAM.findall(body)}
            raw.append((m.group(1).removeprefix("functions."), arguments))
    if not raw:
        for m in _TAGGED_CALL.finditer(text):
            obj = _first_json_object(m.group(1))
            if obj is not None:
                raw.append(_named_call(obj))
    if not raw and not markers_only:
        raw = [_named_call(obj) for obj in _json_objects(text)]
    calls: list[tuple[str, dict]] = []
    for name, arguments in raw:
        if not isinstance(arguments, dict):
            continue
        if name not in specs:
            name = _infer_tool(arguments, specs)
        if name:
            calls.append((name, arguments))
    return calls


_MARKERS_IN_REASONING = re.compile(r"<\|tool_call_begin\|>|<function=|<tool_call>")


# ------------------------------------------------------------------ request shaping
def _text_only(message: dict) -> bool:
    content = message.get("content")
    return isinstance(content, list) and bool(content) and all(set(block) <= {"text"} for block in content)


def _has(message: dict, kind: str) -> bool:
    content = message.get("content")
    return isinstance(content, list) and any(kind in block for block in content)


def normalize_messages(messages: list[dict]) -> list[dict]:
    """Make a Converse message list every model accepts (see module docstring)."""
    msgs = copy.deepcopy(messages)
    for message in msgs:
        if isinstance(message.get("content"), str):
            message["content"] = [{"text": message["content"] or "(empty)"}]
        for block in message.get("content") or []:
            result = block.get("toolResult")
            if result:
                parts = []
                for part in result.get("content") or []:
                    if "json" in part:
                        parts.append({"text": json.dumps(part["json"], ensure_ascii=False)})
                    elif "text" in part:
                        parts.append({"text": part["text"] or "(empty)"})
                result["content"] = parts or [{"text": "(empty)"}]
    # Text the model spoke before a tool call reaches the context after the tool turn (the
    # tool messages are added when the call starts, the spoken text when the bot has said it).
    # When that text is the last message, the next request would end with the assistant:
    # put it back in front of its tool call, where the model produced it.
    if len(msgs) >= 3:
        a, u, t = msgs[-3], msgs[-2], msgs[-1]
        if (
            t["role"] == "assistant" and _text_only(t)
            and u["role"] == "user" and _has(u, "toolResult") and not _has(u, "text")
            and a["role"] == "assistant" and _has(a, "toolUse")
        ):
            a["content"] = t["content"] + a["content"]
            msgs.pop()
    # Merge neighbours with the same role.
    merged: list[dict] = []
    for message in msgs:
        if merged and merged[-1]["role"] == message["role"]:
            merged[-1]["content"] = merged[-1]["content"] + message["content"]
        else:
            merged.append(message)
    # Converse wants a user turn first and a user turn last.
    if merged and merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": [{"text": "(call connected)"}]})
    while merged and merged[-1]["role"] == "assistant":
        merged.pop()
    if not merged:
        merged = [{"role": "user", "content": [{"text": "(call connected)"}]}]
    return merged


@dataclass
class _StreamResult:
    emitted: bool = False  # text was pushed (spoken): too late to switch model
    function_calls: list = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    stop_reason: str | None = None
    unparsed_leak: bool = False  # tool-call markup that could not be turned into a call
    recovered: int = 0  # tool calls recovered from text or reasoning


class MumbaiBedrockLLMService(AWSBedrockLLMService):
    """AWSBedrockLLMService with a fallback chain and robust tool calling (see module docstring)."""

    def __init__(
        self,
        *,
        model_chain: list[str],
        region: str,
        system_instruction: str | None = None,
        model_params: dict | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        first_event_timeout_s: float = 8.0,
        first_output_timeout_s: float = 10.0,
        max_spoken_chars: int = MAX_SPOKEN_CHARS,
        client_factory=None,
        **kwargs,
    ):
        if not model_chain:
            raise ValueError("model_chain is empty")
        settings = AWSBedrockLLMService.Settings(
            model=model_chain[0],
            max_tokens=max_tokens,
            temperature=temperature,
            system_instruction=system_instruction,
        )
        config = Config(connect_timeout=5, read_timeout=30, retries={"total_max_attempts": 1, "mode": "standard"})
        super().__init__(aws_region=region, settings=settings, client_config=config, **kwargs)
        self._chain = list(model_chain)
        self._model_params = dict(model_params or {})
        self._first_event_timeout = first_event_timeout_s
        self._first_output_timeout = max(first_output_timeout_s, first_event_timeout_s)
        self._max_spoken_chars = max_spoken_chars
        self._client_factory = client_factory
        self.spoke_this_response = False
        self.last_model: str | None = None

    # ------------------------------------------------------------- helpers
    @asynccontextmanager
    async def _client(self):
        if self._client_factory is not None:
            async with self._client_factory() as client:
                yield client
        else:
            async with self._aws_session.client(service_name="bedrock-runtime", **self._aws_params) as client:
                yield client

    def candidates(self) -> list[str]:
        now = time.monotonic()
        healthy = [m for m in self._chain if _SKIP_UNTIL.get(m, 0) <= now]
        # Every model sick: still try them all rather than stay silent.
        return healthy or list(self._chain)

    def build_request(self, context: LLMContext) -> dict:
        params = self._get_llm_invocation_params(context)
        messages = normalize_messages(params["messages"])
        request: dict = {"messages": messages}
        if params["system"]:
            request["system"] = params["system"]
        tools = params["tools"]
        if not tools and any(_has(m, "toolUse") or _has(m, "toolResult") for m in messages):
            tools = [self._create_no_op_tool()]
        if tools:
            request["toolConfig"] = {"tools": tools}
        inference = self._build_inference_config()
        if inference:
            request["inferenceConfig"] = inference
        return request

    async def _emit(self, text: str, result: _StreamResult) -> None:
        result.emitted = True
        self.spoke_this_response = self.spoke_this_response or bool(text.strip())
        await self._push_llm_text(text)

    async def _consume(self, stream, context: LLMContext, result: _StreamResult, specs: dict | None = None) -> None:
        """Read one Converse stream: speak what may be spoken, collect the tool calls.

        Raises ModelOutputError / asyncio.TimeoutError when the model should be given up on.
        """
        guard = SpeechGuard(self._max_spoken_chars)
        tools: dict[int, dict] = {}
        order: list[int] = []
        reasoning: list[str] = []
        started = time.monotonic()
        got_event = False
        iterator = stream.__aiter__()
        while True:
            if result.emitted or order:  # speech or a tool call has started: no deadline any more
                event_timeout = None
            else:
                limit = self._first_output_timeout if got_event else min(self._first_event_timeout, self._first_output_timeout)
                event_timeout = max(0.01, started + limit - time.monotonic())
            try:
                if event_timeout is None:
                    event = await iterator.__anext__()
                else:
                    event = await asyncio.wait_for(iterator.__anext__(), timeout=event_timeout)
            except StopAsyncIteration:
                break
            except (asyncio.TimeoutError, TimeoutError):
                if got_event:
                    raise ModelOutputError("SlowOutput") from None
                raise
            got_event = True
            if "contentBlockStart" in event:
                start = event["contentBlockStart"].get("start", {})
                idx = event["contentBlockStart"].get("contentBlockIndex", len(order))
                if "toolUse" in start:
                    tools[idx] = {"id": start["toolUse"].get("toolUseId", ""), "name": start["toolUse"].get("name", ""), "input": ""}
                    order.append(idx)
            elif "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                idx = event["contentBlockDelta"].get("contentBlockIndex", order[-1] if order else 0)
                if "text" in delta:
                    text = guard.feed(delta["text"])
                    if text:
                        await self._emit(text, result)
                    elif guard.junk and not result.emitted and not order:
                        raise ModelOutputError("JunkOutput")
                elif "toolUse" in delta:
                    block = tools.get(idx)
                    if block is None:  # a delta without a start: attach to the last tool block
                        block = tools[order[-1]] if order else None
                    if block is not None:
                        block["input"] += delta["toolUse"].get("input", "") or ""
                elif "reasoningContent" in delta:  # never spoken; kept to recover a tool call written there
                    piece = delta["reasoningContent"].get("text") or ""
                    if piece and sum(len(r) for r in reasoning) < 20000:
                        reasoning.append(piece)
            elif "messageStop" in event:
                result.stop_reason = event["messageStop"].get("stopReason")
            if "metadata" in event and "usage" in event["metadata"]:
                usage = event["metadata"]["usage"]
                result.prompt_tokens += usage.get("inputTokens", 0)
                result.completion_tokens += usage.get("outputTokens", 0)
                result.cache_read += usage.get("cacheReadInputTokens", 0)
                result.cache_write += usage.get("cacheWriteInputTokens", 0)
        rest = guard.flush()
        if rest:
            await self._emit(rest, result)
        if guard.truncated:
            logger.bind(event="llm_reply_truncated", chars=guard.spoken_chars).warning("model reply cut at the spoken-length cap")
        for idx in order:
            block = tools[idx]
            if not block["name"] or block["name"] == "no_operation":
                continue
            try:
                arguments = json.loads(block["input"]) if block["input"].strip() else {}
            except json.JSONDecodeError:
                logger.bind(event="llm_bad_tool_args", tool=block["name"]).warning("tool arguments are not JSON")
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            result.function_calls.append(
                FunctionCallFromLLM(
                    context=context,
                    tool_call_id=block["id"] or f"call_{idx}",
                    function_name=block["name"],
                    arguments=arguments,
                )
            )
        if result.function_calls:
            if guard.held:
                logger.bind(event="llm_markup_dropped").info("tool-call text next to a real tool call was not spoken")
            return
        thought = "".join(reasoning)
        recovered = recover_tool_calls(guard.held, specs or {}) if guard.held else []
        if not recovered and _MARKERS_IN_REASONING.search(thought):
            recovered = recover_tool_calls(thought, specs or {}, markers_only=True)
        for name, arguments in recovered:
            result.function_calls.append(
                FunctionCallFromLLM(
                    context=context, tool_call_id=f"recovered_{uuid.uuid4().hex[:12]}", function_name=name, arguments=arguments
                )
            )
        result.recovered = len(recovered)
        if recovered:
            logger.bind(event="llm_tool_call_recovered", tools=",".join(n for n, _ in recovered)).warning("tool call written as text was recovered")
        elif guard.held:
            result.unparsed_leak = True

    # ------------------------------------------------------------- main loop
    @traced_llm
    async def _process_context(self, context: LLMContext):
        result = _StreamResult()
        self.spoke_this_response = False
        try:
            await self.push_frame(LLMFullResponseStartFrame())
            await self.start_processing_metrics()
            await self.start_ttfb_metrics()
            base_request = self.build_request(context)
            specs = tool_specs_of(base_request)
            failures: list[str] = []
            served_by = None
            async with self._client() as client:
                for model_id in self.candidates():
                    request = dict(base_request, modelId=model_id)
                    extra = self._model_params.get(model_id)
                    if extra:
                        request["additionalModelRequestFields"] = extra
                    result = _StreamResult()  # this model's attempt only
                    try:
                        response = await asyncio.wait_for(client.converse_stream(**request), timeout=self._first_event_timeout)
                    except (ClientError, BotoCoreError, asyncio.TimeoutError, TimeoutError, OSError) as e:
                        failures.append(f"{model_id}:{_mark_failed(model_id, e)}")
                        continue
                    await self.stop_ttfb_metrics()
                    try:
                        await self._consume(response["stream"], context, result, specs)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001 - mid-stream error
                        code = _mark_failed(model_id, e)
                        if result.emitted:
                            # The caller heard part of this answer: keep it rather than start a second voice.
                            logger.bind(event="llm_stream_error", model=model_id, code=code).warning("stream broke after output")
                            served_by = model_id
                            break
                        failures.append(f"{model_id}:{code}")
                        continue
                    if not result.function_calls and (result.unparsed_leak or not result.emitted):
                        # Nothing usable: a garbled tool call, or no speech and no tool call at all.
                        code = _mark_failed(model_id, ModelOutputError("UnparsedToolCall" if result.unparsed_leak else "EmptyResponse"))
                        logger.bind(event="llm_output_rejected", model=model_id, code=code, spoke=result.emitted).warning(
                            "model reply not usable: trying the next model"
                        )
                        failures.append(f"{model_id}:{code}")
                        continue
                    served_by = model_id
                    break
            if served_by is None:
                logger.bind(event="llm_all_failed", failures=",".join(failures)).error("every Bedrock model failed")
                await self.push_error(error_msg="LLM unavailable: " + ",".join(failures))
            else:
                self.last_model = served_by
                logger.bind(
                    event="llm_turn",
                    model=served_by,
                    fallbacks=len(failures),
                    tools=len(result.function_calls),
                    recovered=result.recovered,
                    stop=result.stop_reason,
                    tokens_in=result.prompt_tokens,
                    tokens_out=result.completion_tokens,
                ).info("llm turn")
                await self.run_function_calls(result.function_calls)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            await self.push_error(error_msg=f"LLM error: {type(e).__name__}", exception=e)
        finally:
            await self.stop_processing_metrics()
            await self.push_frame(LLMFullResponseEndFrame())
            await self._report_usage_metrics(
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                cache_read_input_tokens=result.cache_read,
                cache_creation_input_tokens=result.cache_write,
            )
