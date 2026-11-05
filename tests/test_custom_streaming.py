import json

import pytest

from protoverta.resources import StreamLimitError, StreamLimits
from protoverta.sse import SSEEvent, SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.stream_pipe import StreamTranscoder, StreamValidator
from protoverta.streaming import Delta, StreamDecoder, StreamError


def events():
    encoder = StreamEncoder("responses")
    result = []
    for delta in [
        Delta("start", model="m"),
        Delta("tool_start", index=0, call_id="c1", name="execute", tool_type="custom"),
        Delta("tool_delta", index=0, text="not JSON: "),
        Delta("tool_start", index=1, call_id="c2", name="lookup"),
        Delta("tool_delta", index=1, text='{"q":"'),
        Delta("tool_delta", index=0, text="xin chào\n"),
        Delta("tool_delta", index=1, text='x"}'),
        Delta("tool_end", index=1),
        Delta("tool_end", index=0),
        Delta("finish", reason="tool_calls", input_tokens=4, output_tokens=7),
    ]:
        result.extend(encoder.feed(delta))
    encoder.finish()
    return result


def wire(stream):
    return b"".join(encode_sse(event.event, event.data) for event in stream)


def decode(data):
    parser = SSEParser()
    decoder = StreamDecoder("responses")
    result = []
    for event in parser.feed(data) + parser.finish():
        result.extend(decoder.feed(event))
    decoder.finish()
    return result


@pytest.mark.parametrize("chunk_size", [1, 7, 65536])
def test_responses_native_custom_streams_preserve_raw_inputs_and_identity(chunk_size):
    data = wire(events())
    validator = StreamValidator("responses")
    out = b"".join(validator.feed(data[offset : offset + chunk_size]) for offset in range(0, len(data), chunk_size)) + validator.finish()
    assert out == data
    deltas = decode(out)
    assert [(d.call_id, d.tool_type) for d in deltas if d.kind == "tool_start"] == [("c1", "custom"), ("c2", "function")]
    assert "".join(d.text for d in deltas if d.kind == "tool_delta" and d.call_id == "c1") == "not JSON: xin chào\n"
    assert deltas[-1].kind == "finish"


@pytest.mark.parametrize("target", ["chat", "messages"])
def test_cross_protocol_streams_reject_unverified_custom_mapping(target):
    transcoder = StreamTranscoder("responses", target, messages_usage_policy="deferred")
    with pytest.raises(StreamError, match="custom-tool"):
        transcoder.feed(wire(events()))


@pytest.mark.parametrize("change", ["snapshot", "event", "item", "initial"])
def test_responses_custom_done_snapshot_and_event_type_are_checked(change):
    stream = events()
    for i, event in enumerate(stream):
        body = json.loads(event.data)
        if change == "snapshot" and body["type"] == "response.custom_tool_call_input.done":
            body["input"] += "changed"
        elif change == "event" and body["type"] == "response.custom_tool_call_input.delta":
            body["type"] = "response.function_call_arguments.delta"
        elif change == "item" and body["type"] == "response.output_item.done" and body["item"]["type"] == "custom_tool_call":
            body["item"]["type"] = "function_call"
        elif change == "initial" and body["type"] == "response.output_item.added" and body["item"]["type"] == "custom_tool_call":
            body["item"]["input"] = "not emitted as a delta"
        else:
            continue
        stream[i] = type(event)(event=body["type"], data=json.dumps(body))
        break
    with pytest.raises(StreamError):
        decode(wire(stream))


def test_custom_input_is_counted_in_argument_budget_even_without_type_on_delta():
    limits = StreamLimits(max_argument_bytes=4)
    event = SSEEvent("message", json.dumps({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "custom": {"input": "12345"}}]}}]}))
    with pytest.raises(StreamLimitError):
        limits.observe(event)


def test_native_custom_truncation_is_not_completed():
    with pytest.raises(StreamError, match="terminal"):
        decode(wire(events()[:-1]))


def test_duplicate_done_and_late_input_are_rejected():
    for late_delta in (False, True):
        stream = events()
        for index, event in enumerate(stream):
            body = json.loads(event.data)
            if body["type"] == "response.custom_tool_call_input.done":
                body["sequence_number"] += 1
                if late_delta:
                    body["type"] = "response.custom_tool_call_input.delta"
                    body.pop("input")
                    body["delta"] = "late input"
                stream.insert(index + 1, SSEEvent(body["type"], json.dumps(body)))
                break
        with pytest.raises(StreamError, match="duplicate|after tool completion"):
            decode(wire(stream))
