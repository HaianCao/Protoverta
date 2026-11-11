import copy
import io
import json
from urllib.error import HTTPError

import pytest
from test_json_wire_boundaries import RESULT, encode_json, listener, request
from wire_cases import source_wire

from protoverta.core import ConversionError, convert_request, convert_response
from protoverta.sse import SSEEvent, SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.stream_pipe import StreamTranscoder, StreamValidator
from protoverta.streaming import Delta, StreamDecoder, StreamError

PROTOCOLS = ("chat", "messages", "responses")
EDGES = [(a, b) for a in PROTOCOLS for b in PROTOCOLS if a != b]


def history(source, *, ids=("opaque:/+=",), duplicate=False):
    calls, results = [], []
    for identifier in ids:
        if source == "chat":
            calls.append({"id": identifier, "type": "function", "function": {"name": "fixture", "arguments": "{}"}})
            results.append({"role": "tool", "tool_call_id": identifier, "content": "synthetic result"})
        elif source == "messages":
            calls.append({"type": "tool_use", "id": identifier, "name": "fixture", "input": {}})
            results.append({"type": "tool_result", "tool_use_id": identifier, "content": "synthetic result"})
        else:
            calls.append({"type": "function_call", "call_id": identifier, "name": "fixture", "arguments": "{}"})
            results.append({"type": "function_call_output", "call_id": identifier, "output": "synthetic result"})
    results.reverse()  # Parallel results need not follow call order.
    if duplicate:
        results.append(copy.deepcopy(results[-1]))
    if source == "chat":
        return {
            "model": "m",
            "max_tokens": 20,
            "messages": [{"role": "assistant", "content": None, "tool_calls": calls}, *results],
        }
    if source == "messages":
        return {
            "model": "m",
            "max_tokens": 20,
            "messages": [{"role": "assistant", "content": calls}, {"role": "user", "content": results}],
        }
    return {"model": "m", "max_output_tokens": 20, "input": calls + results}


@pytest.mark.parametrize("source,target", EDGES)
@pytest.mark.parametrize("bad", ["empty", "duplicate"])
def test_request_history_rejects_empty_identity_and_repeated_result(source, target, bad):
    body = history(source, ids=("",) if bad == "empty" else ("opaque:/+=",), duplicate=bad == "duplicate")
    with pytest.raises(ConversionError, match="empty tool|duplicate tool result"):
        convert_request(body, source, target)
    assert convert_request(body, source, source) == body  # Native helpers stay passthrough.


@pytest.mark.parametrize("source,target", EDGES)
def test_parallel_result_order_and_opaque_history_ids_remain_portable(source, target):
    body = history(source, ids=("opaque:/+=", "long_" + "x" * 200))
    result = convert_request(body, source, target)
    for identifier in ("opaque:/+=", "long_" + "x" * 200):
        assert identifier in json.dumps(result)


@pytest.mark.parametrize("target", ["messages", "responses"])
@pytest.mark.parametrize("bad", ["empty", "duplicate"])
def test_bad_history_never_dispatches_and_next_valid_request_recovers(target, bad):
    calls = []

    def upstream(req, *, timeout):
        calls.append(req)
        return io.BytesIO(encode_json(convert_response(RESULT, "chat", target)))

    with listener(target, upstream) as origin:
        body = history("chat", ids=("",) if bad == "empty" else ("c",), duplicate=bad == "duplicate")
        with pytest.raises(HTTPError) as caught:
            request(origin, "chat", encode_json(body))
        assert caught.value.code == 422
        assert caught.value.headers["x-should-retry"] == "false"
        caught.value.close()
        assert calls == []
        with request(origin, "chat", encode_json(history("chat"))) as response:
            assert response.status == 200
        assert len(calls) == 1


def messages_wire(fragments=None, trailing_text=None):
    events = [
        ("message_start", {"message": {"model": "m", "content": [], "usage": {"input_tokens": 2, "output_tokens": 0}}}),
        (
            "content_block_start",
            {"index": 0, "content_block": {"type": "tool_use", "id": "c", "name": "fixture", "input": {}}},
        ),
    ]
    for fragment in fragments or []:
        events.append(
            ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": fragment}})
        )
    events.append(("content_block_stop", {"index": 0}))
    if trailing_text is not None:
        events.extend(
            [
                ("content_block_start", {"index": 1, "content_block": {"type": "text", "text": trailing_text}}),
                ("content_block_stop", {"index": 1}),
            ]
        )
    events.extend(
        [("message_delta", {"delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 1}}), ("message_stop", {})]
    )
    return b"".join(encode_sse(kind, json.dumps({"type": kind, **body})) for kind, body in events)


@pytest.mark.parametrize("target", ["chat", "responses"])
@pytest.mark.parametrize("fragments", [None, [""], ["{", "}"], ["{}"]])
def test_messages_explicit_empty_object_is_preserved_without_inventing_arguments(target, fragments):
    raw = messages_wire(fragments)
    pipe = StreamTranscoder("messages", target)
    output = pipe.feed(raw) + pipe.finish()
    decoder = StreamDecoder(target)
    parts = [part for event in SSEParser().feed(output) for part in decoder.feed(event)]
    decoder.finish()
    assert "".join(part.text or "" for part in parts if part.kind == "tool_delta") == "{}"
    assert parts[-1].reason == "tool_calls"
    native = StreamValidator("messages")
    assert native.feed(raw) + native.finish() == raw


@pytest.mark.parametrize("target", ["chat", "responses"])
@pytest.mark.parametrize("fragments", [[" "], ["{"], ["NaN"]])
def test_invalid_messages_arguments_are_never_replaced_with_empty_object(target, fragments):
    with pytest.raises(StreamError, match="JSON"):
        StreamTranscoder("messages", target).feed(messages_wire(fragments))


@pytest.mark.parametrize("target", ["chat", "responses"])
def test_sse_text_after_tool_uses_same_strict_subset_as_json(target):
    body = {
        "model": "m",
        "content": [
            {"type": "tool_use", "id": "c", "name": "fixture", "input": {}},
            {"type": "text", "text": "after tool"},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 2, "output_tokens": 1},
    }
    with pytest.raises(ConversionError, match="text after a tool"):
        convert_response(body, "messages", target)
    with pytest.raises(StreamError, match="text after a tool"):
        StreamTranscoder("messages", target).feed(messages_wire(["{}"], "after tool"))


@pytest.mark.parametrize("target", ["chat", "responses"])
def test_empty_post_tool_delta_is_noop_and_valid_text_before_tools_still_works(target):
    encoder = StreamEncoder(target)
    encoder.feed(Delta("start", model="m", input_tokens=2, output_tokens=0))
    encoder.feed(Delta("tool_start", index=0, call_id="c", name="fixture"))
    encoder.feed(Delta("tool_delta", index=0, text="{}"))
    encoder.feed(Delta("tool_end", index=0))
    assert encoder.feed(Delta("text", text="")) == []
    encoder.feed(Delta("finish", reason="tool_calls", input_tokens=2, output_tokens=1))
    encoder.finish()
    pipe = StreamTranscoder("messages", target)
    assert pipe.feed(source_wire("messages")) + pipe.finish()


@pytest.mark.parametrize("index", [False, True, 0.0, "0", None, -1, 1])
@pytest.mark.parametrize("postlude", [False, True])
def test_chat_choice_index_requires_exact_integer_in_initial_and_usage_chunks(index, postlude):
    decoder = StreamDecoder("chat")
    if postlude:
        decoder.feed(
            SSEEvent(
                "message",
                json.dumps(
                    {
                        "object": "chat.completion.chunk",
                        "model": "m",
                        "choices": [{"index": 0, "delta": {"content": "synthetic"}, "finish_reason": "stop"}],
                    }
                ),
            )
        )
    body = {
        "object": "chat.completion.chunk",
        "model": "m",
        "choices": [{"index": index, "delta": {}, "finish_reason": "stop" if postlude else None}],
    }
    if postlude:
        body["usage"] = {"prompt_tokens": 2, "completion_tokens": 1}
    with pytest.raises(StreamError, match="index"):
        decoder.feed(SSEEvent("message", json.dumps(body)))


def test_chat_empty_argument_string_is_not_normalized_like_messages_input():
    decoder = StreamDecoder("chat")
    body = {
        "object": "chat.completion.chunk",
        "model": "m",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {"index": 0, "id": "c", "type": "function", "function": {"name": "f", "arguments": ""}}
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    decoder.feed(SSEEvent("message", json.dumps(body)))
    with pytest.raises(StreamError, match="valid JSON"):
        decoder.feed(SSEEvent("message", "[DONE]"))


@pytest.mark.parametrize("value", [None, [], "", False, "missing"])
def test_messages_empty_argument_normalization_requires_explicit_object_input(value):
    decoder = StreamDecoder("messages")
    decoder.feed(SSEEvent("message_start", json.dumps({"type": "message_start", "message": {"model": "m"}})))
    block = {"type": "tool_use", "id": "c", "name": "fixture"}
    if value != "missing":
        block["input"] = value
    with pytest.raises(StreamError, match="tool_use.input"):
        decoder.feed(
            SSEEvent(
                "content_block_start", json.dumps({"type": "content_block_start", "index": 0, "content_block": block})
            )
        )
