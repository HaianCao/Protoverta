import json

import pytest
from wire_cases import CALLS, TEXT, fragments, source_wire

from protoverta.sse import SSEParser, encode_sse
from protoverta.stream_pipe import StreamTranscoder
from protoverta.streaming import StreamDecoder, StreamError

PROTOCOLS = ("chat", "messages", "responses")
ROUTES = [(source, target) for source in PROTOCOLS for target in PROTOCOLS if source != target]


def transcode(raw, source, target, seed):
    pipe = StreamTranscoder(source, target, messages_usage_policy="deferred")
    return b"".join(pipe.feed(chunk) for chunk in fragments(raw, seed)) + pipe.finish()


def semantic_output(raw, protocol):
    parser, decoder = SSEParser(), StreamDecoder(protocol)
    deltas = [delta for chunk in fragments(raw, 543) for item in parser.feed(chunk) for delta in decoder.feed(item)]
    parser.finish(strict=True)
    decoder.finish()
    calls = {}
    for delta in deltas:
        if delta.kind == "tool_start":
            calls[delta.index] = [delta.call_id, delta.name, ""]
        elif delta.kind == "tool_delta":
            calls[delta.index][2] += delta.text
    return {
        "text": "".join(delta.text for delta in deltas if delta.kind == "text"),
        "calls": [(identifier, name, json.loads(arguments)) for identifier, name, arguments in calls.values()],
        "reason": deltas[-1].reason,
        "usage": (deltas[-1].input_tokens, deltas[-1].output_tokens),
    }


@pytest.mark.parametrize(("source", "target"), ROUTES)
@pytest.mark.parametrize("seed", [0, 1, 2, 17, 97])
def test_random_byte_partition_and_roundtrip_keep_text_tools_ids_usage(source, target, seed):
    wire = source_wire(source)
    translated = transcode(wire, source, target, seed)
    expected = {"text": TEXT, "calls": CALLS, "reason": "tool_calls", "usage": (19, 7)}
    assert semantic_output(translated, target) == expected
    roundtrip = transcode(translated, target, source, seed + 100)
    assert semantic_output(roundtrip, source) == expected


@pytest.mark.parametrize(("source", "target"), ROUTES)
def test_truncated_stream_does_not_emit_target_success_marker(source, target):
    events = SSEParser().feed(source_wire(source))
    # Remove the upstream terminal marker, retaining valid preceding events.
    raw = b"".join(encode_sse(item.event, item.data) for item in events[:-1])
    pipe = StreamTranscoder(source, target, messages_usage_policy="deferred")
    output = pipe.feed(raw)
    assert b"response.completed" not in output and b"message_stop" not in output and b"[DONE]" not in output
    with pytest.raises(StreamError):
        pipe.finish()


@pytest.mark.parametrize(("source", "target"), ROUTES)
def test_max_tokens_retains_incomplete_semantics_after_roundtrip(source, target):
    output = transcode(source_wire(source, tools=False, length=True), source, target, 4)
    assert semantic_output(output, target)["reason"] == "length"
    assert semantic_output(transcode(output, target, source, 7), source)["reason"] == "length"


@pytest.mark.parametrize(("source", "target"), ROUTES)
@pytest.mark.parametrize("missing", ["name", "id"])
def test_empty_tool_identity_never_becomes_completed_history(source, target, missing):
    events = SSEParser().feed(source_wire(source))
    for index, item in enumerate(events):
        body = json.loads(item.data) if item.data != "[DONE]" else {}
        if source == "chat" and body.get("choices") and body["choices"][0]["delta"].get("tool_calls"):
            tool = body["choices"][0]["delta"]["tool_calls"][0]
            (tool["function"] if missing == "name" else tool)[missing] = ""
        elif source == "messages" and body.get("type") == "content_block_start" and body["content_block"].get("type") == "tool_use":
            body["content_block"][missing] = ""
        elif source == "responses" and body.get("type") == "response.output_item.added" and body["item"].get("type") == "function_call":
            body["item"]["name" if missing == "name" else "call_id"] = ""
        else:
            continue
        events[index] = type(item)(item.event, json.dumps(body))
        break
    raw = b"".join(encode_sse(item.event, item.data) for item in events)
    pipe = StreamTranscoder(source, target, messages_usage_policy="deferred")
    output = bytearray()
    with pytest.raises(StreamError):
        for chunk in fragments(raw, 91):
            output.extend(pipe.feed(chunk))
        output.extend(pipe.finish())
    assert b"response.completed" not in output and b"message_stop" not in output and b"[DONE]" not in output
