"""Adversarial wire checks: corruption must never become successful completion."""

import copy
import json

import pytest
from wire_cases import event, source_wire

from protoverta.sse import SSEParser
from protoverta.stream_pipe import StreamTranscoder, StreamValidator
from protoverta.streaming import StreamError


def frames(protocol):
    return [
        (frame.event, json.loads(frame.data) if frame.data != "[DONE]" else frame.data)
        for frame in SSEParser().feed(source_wire(protocol))
    ]


def corrupt(protocol, case):
    values = copy.deepcopy(frames(protocol))
    if protocol == "responses":
        kind, field, value = {
            "text_identity": ("response.output_text.delta", "item_id", "other"),
            "text_snapshot": ("response.output_text.done", "text", "changed"),
            "part_identity": ("response.content_part.done", "item_id", "other"),
            "argument_identity": ("response.function_call_arguments.done", "item_id", "other"),
        }.get(case, (None, None, None))
        if kind:
            next(body for name, body in values if name == kind)[field] = value
        elif case == "tool_identity":
            next(body for name, body in values if name == "response.output_item.done" and body["output_index"] == 1)[
                "item"
            ]["call_id"] = "other"
        elif case == "tool_snapshot":
            next(body for name, body in values if name == "response.output_item.done" and body["output_index"] == 1)[
                "item"
            ]["arguments"] = '{"changed":true}'
        elif case == "final_model":
            values[-1][1]["response"]["model"] = "other"
        elif case == "final_output":
            values[-1][1]["response"]["output"] = []
        elif case == "open_item":
            values = [(name, body) for name, body in values if name != "response.output_item.done"]
    elif protocol == "messages":
        if case == "wrong_event":
            values[1] = ("message_stop", values[1][1])
        elif case == "initial_content":
            values[0][1]["message"]["content"] = [{"type": "text", "text": "lost"}]
        elif case == "citations":
            values[1][1]["content_block"]["citations"] = [{"type": "char_location"}]
        elif case == "delta_kind":
            values[2][1]["delta"] = {"type": "input_json_delta", "partial_json": "{}"}
        elif case == "unknown_stop":
            values[-2][1]["delta"]["stop_reason"] = "future_reason"
        elif case == "open_block":
            values = [(name, body) for name, body in values if name != "content_block_stop"]
        elif case == "missing_stop":
            values.pop(-2)
    else:
        first = values[0][1]
        if case == "model_change":
            values[1][1]["model"] = "other"
        elif case == "multiple_choices":
            first["choices"].append(copy.deepcopy(first["choices"][0]))
        elif case == "tool_type":
            first["choices"][0]["delta"]["tool_calls"][0]["type"] = "future_tool"
        elif case == "unknown_delta":
            first["choices"][0]["delta"]["future_payload"] = "must-not-disappear"
        elif case == "unknown_stop":
            values[1][1]["choices"][0]["finish_reason"] = "future_reason"
        elif case == "duplicate_finish":
            values.insert(2, copy.deepcopy(values[1]))
        elif case == "incomplete_tool":
            del first["choices"][0]["delta"]["tool_calls"][0]["id"]
    return values


CASES = {
    "responses": [
        "text_identity",
        "text_snapshot",
        "part_identity",
        "argument_identity",
        "tool_identity",
        "tool_snapshot",
        "final_model",
        "final_output",
        "open_item",
    ],
    "messages": [
        "wrong_event",
        "initial_content",
        "citations",
        "delta_kind",
        "unknown_stop",
        "open_block",
        "missing_stop",
    ],
    "chat": [
        "model_change",
        "multiple_choices",
        "tool_type",
        "unknown_delta",
        "unknown_stop",
        "duplicate_finish",
        "incomplete_tool",
    ],
}


@pytest.mark.parametrize(
    "source,target,case", [(s, t, c) for s, cases in CASES.items() for t in CASES if t != s for c in cases]
)
def test_corrupt_wire_never_commits_target_success(source, target, case):
    pipe = StreamTranscoder(source, target, messages_usage_policy="deferred")
    delivered = []
    with pytest.raises(StreamError):
        for kind, body in corrupt(source, case):
            delivered.append(pipe.feed(event(kind, body)))
        delivered.append(pipe.finish())
    output = b"".join(delivered)
    assert b"event: response.completed\n" not in output
    assert b"event: message_stop\n" not in output
    assert b"data: [DONE]\n" not in output


@pytest.mark.parametrize("protocol", ["messages", "responses"])
def test_native_closed_item_index_cannot_be_reused(protocol):
    values = frames(protocol)
    start_name, end_name = (
        ("content_block_start", "content_block_stop")
        if protocol == "messages"
        else ("response.output_item.added", "response.output_item.done")
    )
    start = next(i for i, (name, _) in enumerate(values) if name == start_name)
    end = next(i for i, (name, _) in enumerate(values) if name == end_name)
    values[end + 1 : end + 1] = copy.deepcopy(values[start : end + 1])
    if protocol == "responses":
        for sequence, (_, body) in enumerate(values):
            body["sequence_number"] = sequence
    pipe = StreamValidator(protocol)
    with pytest.raises(StreamError, match="duplicate"):
        for kind, body in values:
            pipe.feed(event(kind, body))
        pipe.finish()
