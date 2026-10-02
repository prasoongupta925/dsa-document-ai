# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""MumbaiBedrockLLMService against a scripted Converse stream: fallback chain, tool calls, reasoning."""

import pytest
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import ErrorFrame, FunctionCallsStartedFrame, LLMContextFrame, LLMTextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import run_test

import llm as llm_module
from fakes import FakeBedrock, _Stream, client_error, text_events, tool_events
from llm import MumbaiBedrockLLMService, ThinkTagStripper, normalize_messages
from tools import tool_schemas

CHAIN = ["moonshotai.kimi-k2.5", "openai.gpt-oss-120b-1:0"]  # in-Region, ap-south-1 (config.DEFAULT_MODEL_CHAIN)


def make_llm(script, **kwargs):
    bedrock = FakeBedrock(script)
    service = MumbaiBedrockLLMService(
        model_chain=CHAIN, region="ap-south-1", system_instruction="You are a test.", client_factory=bedrock.factory,
        first_event_timeout_s=kwargs.pop("timeout", 2.0), **kwargs,
    )
    return service, bedrock


def context(*user_texts):
    messages = [{"role": "user", "content": t} for t in user_texts] or [{"role": "user", "content": "hello"}]
    return LLMContext(messages=messages, tools=ToolsSchema(standard_tools=tool_schemas("browser")))


def spoken(frames):
    return "".join(f.text for f in frames if isinstance(f, LLMTextFrame))


async def run(service, ctx):
    down, up = await run_test(service, frames_to_send=[LLMContextFrame(ctx)])
    return down, up


async def test_kimi_first_with_mumbai_request_shape():
    service, bedrock = make_llm(lambda req, model, turn: text_events("नमस्ते", " जी।"), max_tokens=300, temperature=0.2)
    down, _ = await run(service, context("hi"))
    assert spoken(down) == "नमस्ते जी।"
    request = bedrock.requests[0]
    assert request["modelId"] == "moonshotai.kimi-k2.5" and service.last_model == "moonshotai.kimi-k2.5"
    assert request["system"] == [{"text": "You are a test."}]
    assert request["inferenceConfig"] == {"maxTokens": 300, "temperature": 0.2}
    assert "additionalModelRequestFields" not in request
    assert {t["toolSpec"]["name"] for t in request["toolConfig"]["tools"]} == {"file_status", "eligibility", "reminder", "switch_language", "end_call"}
    assert "toolChoice" not in request["toolConfig"]


async def test_access_denied_falls_back_and_skips_the_model_for_a_while():
    def script(req, model, turn):
        if model == "moonshotai.kimi-k2.5":
            return client_error("AccessDeniedException", "explicit deny")
        return text_events("ok")

    service, bedrock = make_llm(script)
    await run(service, context("a"))
    await run(service, context("b"))
    assert [r["modelId"] for r in bedrock.requests] == ["moonshotai.kimi-k2.5", "openai.gpt-oss-120b-1:0", "openai.gpt-oss-120b-1:0"]
    assert llm_module._SKIP_UNTIL["moonshotai.kimi-k2.5"] > 0


async def test_slow_first_event_falls_back():
    def script(req, model, turn):
        if model == "moonshotai.kimi-k2.5":
            return _Stream(text_events("late"), delay_s=1.0)
        return text_events("fast")

    service, bedrock = make_llm(script, timeout=0.2)
    down, _ = await run(service, context("x"))
    assert spoken(down) == "fast" and service.last_model == "openai.gpt-oss-120b-1:0"


async def test_stream_error_before_output_falls_back_but_not_after():
    def early(req, model, turn):
        if model == "moonshotai.kimi-k2.5":
            return _Stream(text_events("never"), fail_after=0)
        return text_events("from gpt-oss")

    service, _ = make_llm(early)
    down, _ = await run(service, context("x"))
    assert spoken(down) == "from gpt-oss"

    llm_module.reset_model_health()

    def late(req, model, turn):
        return _Stream(text_events("half ", "sentence", " more"), fail_after=4)

    service, bedrock = make_llm(late)
    down, _ = await run(service, context("x"))
    assert spoken(down) == "half sentence" and len(bedrock.requests) == 1  # no second voice mid-sentence


async def test_every_tool_call_runs_even_with_end_turn():
    called = []

    async def handler(params):
        called.append((params.function_name, dict(params.arguments)))
        await params.result_callback({"ok": True})

    service, _ = make_llm(lambda req, model, turn: tool_events(
        ("file_status", {"applicant": "Sneha Kulkarni"}), ("eligibility", {"applicant": "Sneha Kulkarni"}), stop="end_turn"))
    service.register_function("file_status", handler)
    service.register_function("eligibility", handler)
    down, _ = await run(service, context("status and eligibility"))
    assert sorted(called) == [("eligibility", {"applicant": "Sneha Kulkarni"}), ("file_status", {"applicant": "Sneha Kulkarni"})]
    started = [f for f in down if isinstance(f, FunctionCallsStartedFrame)]
    assert [fc.function_name for fc in started[0].function_calls] == ["file_status", "eligibility"]


async def test_bad_tool_arguments_become_empty_and_no_operation_is_ignored():
    called = []

    async def handler(params):
        called.append(dict(params.arguments))
        await params.result_callback({"ok": True})

    events = tool_events(("file_status", {"applicant": "x"}), ("no_operation", {}))
    for e in events:
        delta = e.get("contentBlockDelta", {})
        if delta.get("contentBlockIndex") == 0 and "toolUse" in delta.get("delta", {}):
            delta["delta"]["toolUse"]["input"] = "{not json"
    service, _ = make_llm(lambda req, model, turn: events)
    service.register_function("file_status", handler)
    await run(service, context("x"))
    assert called == [{}]


async def test_reasoning_and_think_tags_are_never_spoken():
    events = tool_events(reasoning="secret plan") + []
    events = [e for e in events if "messageStop" not in e and "metadata" not in e]
    events += text_events("<think>hidden", " still hidden</think>", "आपकी file ", "<thinking>x</thinking>तैयार है।")[1:]
    service, _ = make_llm(lambda req, model, turn: events)
    down, _ = await run(service, context("x"))
    assert spoken(down) == "आपकी file तैयार है।"
    assert service.spoke_this_response


async def test_all_models_failing_reports_an_error_upstream():
    service, bedrock = make_llm(lambda req, model, turn: client_error("ThrottlingException"))
    down, up = await run(service, context("x"))
    assert [r["modelId"] for r in bedrock.requests] == CHAIN  # each model once, nothing outside the chain
    errors = [f for f in up if isinstance(f, ErrorFrame)]
    assert errors and "ThrottlingException" in errors[0].error
    assert spoken(down) == ""


async def test_model_params_are_sent_only_to_their_model():
    def script(req, model, turn):
        if model == "moonshotai.kimi-k2.5":
            return client_error("ServiceUnavailableException")
        return text_events("ok")

    service, bedrock = make_llm(script, model_params={"openai.gpt-oss-120b-1:0": {"reasoning_effort": "low"}})
    await run(service, context("x"))
    assert "additionalModelRequestFields" not in bedrock.requests[0]
    assert bedrock.requests[1]["additionalModelRequestFields"] == {"reasoning_effort": "low"}


# ------------------------------------------------------------------ request normalization
def test_tool_results_become_text_blocks_and_order_is_fixed():
    messages = [
        {"role": "assistant", "content": [{"text": "greeting"}]},
        {"role": "user", "content": [{"text": "status?"}]},
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": "t1", "name": "file_status", "input": {"applicant": "A"}}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "t1", "content": [{"json": {"verdict": "NOT READY", "नाम": "स्नेहा"}}]}}]},
        {"role": "assistant", "content": [{"text": "एक मिनट।"}]},
    ]
    out = normalize_messages(messages)
    assert out[0] == {"role": "user", "content": [{"text": "(call connected)"}]}
    assert out[-1]["role"] == "user"
    result_block = out[-1]["content"][0]["toolResult"]["content"][0]
    assert result_block == {"text": '{"verdict": "NOT READY", "नाम": "स्नेहा"}'}
    assert out[-2]["content"][0] == {"text": "एक मिनट।"} and "toolUse" in out[-2]["content"][1]
    assert messages[-1]["content"] == [{"text": "एक मिनट।"}]  # input untouched


def test_answer_after_a_tool_result_stays_where_it_is():
    messages = [
        {"role": "user", "content": [{"text": "status?"}]},
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": "t1", "name": "file_status", "input": {}}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "t1", "content": [{"text": "NOT READY"}]}}]},
        {"role": "assistant", "content": [{"text": "Two documents are missing."}]},
        {"role": "user", "content": [{"text": "send a reminder"}]},
    ]
    assert normalize_messages(messages) == messages


def test_trailing_assistant_without_tools_is_dropped_and_neighbours_merge():
    out = normalize_messages([
        {"role": "user", "content": "a"},
        {"role": "user", "content": [{"text": "b"}]},
        {"role": "assistant", "content": [{"text": "c"}]},
    ])
    assert out == [{"role": "user", "content": [{"text": "a"}, {"text": "b"}]}]


def test_think_stripper_handles_tags_split_across_chunks():
    s = ThinkTagStripper()
    pieces = ["Hello <th", "ink>secret", " plan</thi", "nk> world", " <", "b>bold"]
    out = "".join(s.feed(p) for p in pieces) + s.flush()
    assert out == "Hello  world <b>bold"
    s2 = ThinkTagStripper()
    assert s2.feed("leftover</think>answer") == "leftoveranswer"


async def test_stream_error_after_a_tool_block_but_before_speech_falls_back():
    def script(req, model, turn):
        if model == "moonshotai.kimi-k2.5":
            return _Stream(tool_events(("file_status", {"applicant": "A"})), fail_after=3)
        return text_events("from gpt-oss")

    service, bedrock = make_llm(script)
    down, _ = await run(service, context("x"))
    assert spoken(down) == "from gpt-oss" and [r["modelId"] for r in bedrock.requests] == CHAIN[:2]


# ------------------------------------------------------------------ Kimi on ConverseStream: leaked tool calls
# Public reports (opencode #13807/#14221, vercel/ai #11409, Feb-Aug 2026): Kimi on Bedrock sometimes writes the
# tool call as text or inside its reasoning, with stopReason "end_turn"; and an Apr 2026 regression returned only
# "!!!!" padding. None of that may be spoken; the call is recovered when unambiguous, else the next model answers.
def reasoning_then_text(reasoning: str, *texts: str, stop: str = "end_turn") -> list[dict]:
    events = [{"messageStart": {"role": "assistant"}},
              {"contentBlockDelta": {"delta": {"reasoningContent": {"text": reasoning}}, "contentBlockIndex": 0}},
              {"contentBlockStop": {"contentBlockIndex": 0}}]
    events += [{"contentBlockDelta": {"delta": {"text": t}, "contentBlockIndex": 1}} for t in texts]
    return events + [{"messageStop": {"stopReason": stop}}, {"metadata": {"usage": {"inputTokens": 50, "outputTokens": 9}}}]


def recorder():
    called = []

    async def handler(params):
        called.append((params.function_name, dict(params.arguments)))
        await params.result_callback({"ok": True})

    return called, handler


def register_all(service, handler):
    for name in ("file_status", "eligibility", "reminder", "switch_language", "end_call"):
        service.register_function(name, handler)


@pytest.mark.parametrize(
    "chunks",
    [
        # native markers, split at awkward places
        ["एक मिनट। <|tool_calls_sec", "tion_begin|> <|tool_call_begin|> functions.file_status:0 <|tool_call_argument_begin|>",
         ' {"applicant": "Sneha Kulkarni"} <|tool_call_end|> <|tool_calls_section_end|>'],
        # the id instead of the name (seen in opencode #14221): inferred from the arguments when unique
        ["एक मिनट। <|tool_call_begin|> tooluse_Vbs57l9NT6-Lu5tgQRwYjA", '{"applicant": "Sneha Kulkarni", "language": "hi"}',
         " <|tool_call_end|>"],
        # <function=name> with a JSON body, and with <parameter=k> pairs
        ["एक मिनट। <func", 'tion=file_status>{"applicant": "Sneha Kulkarni"}</function>'],
        ["एक मिनट। <function=file_status><parameter=applicant>Sneha Kulkarni</parameter></function>"],
        # <tool_call>{"name": ..., "arguments": ...}</tool_call>
        ['एक मिनट। <tool_call>{"name": "file_status", "arguments": {"applicant": "Sneha Kulkarni"}}</tool_call>'],
        # bare JSON naming the tool
        ['एक मिनट। {"name": "file_status", "arguments": "{\\"applicant\\": \\"Sneha Kulkarni\\"}"}'],
    ],
)
async def test_tool_calls_written_as_text_are_recovered_and_never_spoken(chunks):
    called, handler = recorder()
    service, bedrock = make_llm(lambda req, model, turn: text_events(*chunks))
    register_all(service, handler)
    down, _ = await run(service, context("मेरी file?"))
    assert spoken(down).strip() == "एक मिनट।"
    assert len(called) == 1 and called[0][1]["applicant"] == "Sneha Kulkarni"
    assert called[0][0] in ("file_status", "reminder")
    assert len(bedrock.requests) == 1  # served by Kimi: no fallback needed


async def test_an_ambiguous_bare_json_call_goes_to_the_next_model():
    def script(req, model, turn):
        if model == "moonshotai.kimi-k2.5":
            return text_events('{"applicant": "Sneha Kulkarni"}')  # file_status or eligibility?
        return tool_events(("eligibility", {"applicant": "Sneha Kulkarni"}))

    called, handler = recorder()
    service, bedrock = make_llm(script)
    register_all(service, handler)
    down, _ = await run(service, context("eligibility?"))
    assert spoken(down) == "" and called == [("eligibility", {"applicant": "Sneha Kulkarni"})]
    assert [r["modelId"] for r in bedrock.requests] == CHAIN[:2]


async def test_a_tool_call_hidden_in_the_reasoning_is_recovered():
    leaked = ' Let me check: <|tool_calls_section_begin|> <|tool_call_begin|> functions.eligibility:0 ' \
             '<|tool_call_argument_begin|> {"applicant": "Sneha Kulkarni"} <|tool_call_end|> <|tool_calls_section_end|>'
    called, handler = recorder()
    service, bedrock = make_llm(lambda req, model, turn: reasoning_then_text(leaked))
    register_all(service, handler)
    down, _ = await run(service, context("eligibility?"))
    assert spoken(down) == "" and called == [("eligibility", {"applicant": "Sneha Kulkarni"})]
    assert len(bedrock.requests) == 1


async def test_plain_json_in_the_reasoning_is_not_taken_as_a_call():
    def script(req, model, turn):
        if model == "moonshotai.kimi-k2.5":
            return reasoning_then_text('I could call file_status with {"applicant": "Sneha Kulkarni"} later.')
        return text_events("जी, बताइए।")

    called, handler = recorder()
    service, bedrock = make_llm(script)
    register_all(service, handler)
    down, _ = await run(service, context("x"))
    assert called == [] and spoken(down) == "जी, बताइए।"  # Kimi said nothing usable: gpt-oss answered


@pytest.mark.parametrize("kimi", [["!" * 30, "!" * 30], ["", "   "], ["..."]])
async def test_padding_or_empty_replies_go_to_the_next_model(kimi):
    def script(req, model, turn):
        if model == "moonshotai.kimi-k2.5":
            return text_events(*kimi, stop="max_tokens")
        return text_events("जी, बताइए।")

    service, bedrock = make_llm(script)
    down, _ = await run(service, context("x"))
    assert spoken(down) == "जी, बताइए।" and service.last_model == "openai.gpt-oss-120b-1:0"
    assert llm_module._SKIP_UNTIL["moonshotai.kimi-k2.5"] > 0  # skipped for a while


async def test_a_real_tool_call_with_its_arguments_echoed_as_text_runs_once_silently():
    events = tool_events(("file_status", {"applicant": "Sneha Kulkarni"}), stop="tool_use")
    events = events[:-2] + [{"contentBlockDelta": {"delta": {"text": '{"applicant": "Sneha Kulkarni"} '}, "contentBlockIndex": 9}},
                            {"contentBlockDelta": {"delta": {"text": "<|tool_calls_section_end|>"}, "contentBlockIndex": 9}}] + events[-2:]
    called, handler = recorder()
    service, _ = make_llm(lambda req, model, turn: events)
    register_all(service, handler)
    down, _ = await run(service, context("x"))
    assert spoken(down) == "" and called == [("file_status", {"applicant": "Sneha Kulkarni"})]


async def test_long_hidden_reasoning_falls_back_to_the_next_model():
    def script(req, model, turn):
        if model == "moonshotai.kimi-k2.5":
            return _Stream(reasoning_then_text("thinking " * 50, "late answer"), delay_s=0.15)
        return text_events("fast answer")

    service, bedrock = make_llm(script, timeout=0.2, first_output_timeout_s=0.4)
    down, _ = await run(service, context("x"))
    assert spoken(down) == "fast answer" and service.last_model == "openai.gpt-oss-120b-1:0"


async def test_speech_then_a_garbled_call_lets_the_next_model_finish_the_turn():
    def script(req, model, turn):
        if model == "moonshotai.kimi-k2.5":
            return text_events("एक मिनट। ", "<|tool_call_begin|> garbage <|tool_call_end|>")
        return tool_events(("file_status", {"applicant": "Sneha Kulkarni"}))

    called, handler = recorder()
    service, bedrock = make_llm(script)
    register_all(service, handler)
    down, _ = await run(service, context("x"))
    assert spoken(down) == "एक मिनट। " and called == [("file_status", {"applicant": "Sneha Kulkarni"})]
    assert [r["modelId"] for r in bedrock.requests] == CHAIN[:2]


def test_speech_guard_releases_ordinary_text_and_caps_the_length():
    from llm import SpeechGuard

    g = SpeechGuard(max_chars=40)
    out = g.feed("These functions help. ") + g.feed("Fun f") + g.feed("act, half.") + g.flush()
    assert out == "These functions help. Fun fact, half." and not g.leaked
    g2 = SpeechGuard(max_chars=20)
    out2 = g2.feed("एक दो तीन चार पाँच छह सात आठ नौ दस") + g2.flush()
    assert len(out2) <= 20 and g2.truncated and out2 == "एक दो तीन चार पाँच"
    g3 = SpeechGuard()
    assert g3.feed("See functions.file_st") == "See " and g3.feed("atus:0 {}") == "" and g3.leaked


def test_recover_tool_calls_only_returns_offered_tools():
    from llm import recover_tool_calls

    specs = {"file_status": ({"applicant"}, {"applicant"}), "switch_language": ({"language"}, {"language"})}
    assert recover_tool_calls('<function=delete_everything>{"x": 1}</function>', specs) == []
    assert recover_tool_calls('{"language": "mr"}', specs) == [("switch_language", {"language": "mr"})]
    assert recover_tool_calls('{"language": "mr"}', specs, markers_only=True) == []
    assert recover_tool_calls("{not json", specs) == []
