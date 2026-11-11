"""Generated semantic invariants using independent, hand-authored wire fixtures."""

import json
from itertools import pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from wire_cases import CALLS, TEXT, source_wire

from protoverta.sse import SSEParser, encode_sse
from protoverta.stream_pipe import StreamTranscoder
from protoverta.streaming import StreamDecoder, StreamError

ROUTES = [(a, b) for a in ("chat", "messages", "responses") for b in ("chat", "messages", "responses") if a != b]
UNICODE = st.text(st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=60)
JSON = st.recursive(
    st.none() | st.booleans() | st.integers(min_value=-(2**63), max_value=2**63 - 1) | UNICODE,
    lambda child: st.lists(child, max_size=4) | st.dictionaries(UNICODE, child, max_size=4),
    max_leaves=12,
)
ARGUMENTS = st.dictionaries(UNICODE, JSON, max_size=5)
PARTITIONS = st.lists(st.integers(min_value=0, max_value=65535), max_size=80)
PROPERTY = settings(max_examples=50, deadline=None, derandomize=True, database=None)


def chunks(raw, cuts):
    positions = sorted({0, len(raw), *(value % len(raw) for value in cuts)})
    return [raw[a:b] for a, b in pairwise(positions)]


def wire(source, text, arguments):
    original = json.dumps(CALLS[0][2], ensure_ascii=False)

    def replace(value):
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if value == TEXT:
            return text
        if value == original:
            return json.dumps(arguments, ensure_ascii=False)
        return value

    return b"".join(
        encode_sse(
            event.event,
            json.dumps(replace(json.loads(event.data)), ensure_ascii=False) if event.data != "[DONE]" else event.data,
        )
        for event in SSEParser().feed(source_wire(source))
    )


def translate(raw, source, target, cuts):
    pipe = StreamTranscoder(source, target, messages_usage_policy="deferred")
    return b"".join(pipe.feed(chunk) for chunk in chunks(raw, cuts)) + pipe.finish()


def semantics(raw, source):
    parser, decoder = SSEParser(), StreamDecoder(source)
    deltas = [delta for event in parser.feed(raw) for delta in decoder.feed(event)]
    parser.finish(strict=True)
    decoder.finish()
    calls = {}
    for delta in deltas:
        if delta.kind == "tool_start":
            calls[delta.index] = [delta.call_id, delta.name, ""]
        elif delta.kind == "tool_delta":
            calls[delta.index][2] += delta.text
    return (
        "".join(delta.text for delta in deltas if delta.kind == "text"),
        [(identifier, name, json.loads(arguments)) for identifier, name, arguments in calls.values()],
        deltas[-1].reason,
        deltas[-1].input_tokens,
        deltas[-1].output_tokens,
    )


@pytest.mark.parametrize(("source", "target"), ROUTES)
@PROPERTY
@given(text=UNICODE, arguments=ARGUMENTS, cuts=PARTITIONS)
def test_arbitrary_unicode_json_and_byte_boundaries_round_trip(source, target, text, arguments, cuts):
    original = wire(source, text, arguments)
    expected = (text, [(CALLS[0][0], CALLS[0][1], arguments), CALLS[1]], "tool_calls", 19, 7)
    converted = translate(original, source, target, cuts)
    assert semantics(converted, target) == expected
    assert semantics(translate(converted, target, source, list(reversed(cuts))), source) == expected


@pytest.mark.parametrize(("source", "target"), ROUTES)
@PROPERTY
@given(cuts=PARTITIONS, text=UNICODE)
def test_missing_terminal_never_commits_success(source, target, cuts, text):
    events = SSEParser().feed(wire(source, text, {}))
    truncated = b"".join(encode_sse(event.event, event.data) for event in events[:-1])
    pipe = StreamTranscoder(source, target, messages_usage_policy="deferred")
    output = bytearray()
    with pytest.raises(StreamError):
        for chunk in chunks(truncated, cuts):
            output.extend(pipe.feed(chunk))
        output.extend(pipe.finish())
    assert not any(
        marker in output for marker in (b"event: response.completed", b"event: message_stop", b"data: [DONE]")
    )


@pytest.mark.parametrize(("source", "target"), ROUTES)
@PROPERTY
@given(cuts=PARTITIONS, text=UNICODE)
def test_length_termination_remains_incomplete(source, target, cuts, text):
    raw = source_wire(source, tools=False, length=True).replace(
        json.dumps(TEXT, ensure_ascii=False).encode(), json.dumps(text, ensure_ascii=False).encode()
    )
    assert semantics(translate(raw, source, target, cuts), target)[2] == "length"


@pytest.mark.parametrize(("source", "target"), ROUTES)
@PROPERTY
@given(cuts=PARTITIONS, text=UNICODE)
def test_invalid_tool_json_never_commits_success(source, target, cuts, text):
    def corrupt(value):
        if isinstance(value, list):
            return [corrupt(item) for item in value]
        if isinstance(value, dict):
            return {
                key: '{"x":' if key in ("arguments", "partial_json") and isinstance(item, str) else corrupt(item)
                for key, item in value.items()
            }
        return value

    raw = b"".join(
        encode_sse(event.event, json.dumps(corrupt(json.loads(event.data))) if event.data != "[DONE]" else event.data)
        for event in SSEParser().feed(wire(source, text, {}))
    )
    pipe = StreamTranscoder(source, target, messages_usage_policy="deferred")
    output = bytearray()
    with pytest.raises(StreamError):
        for chunk in chunks(raw, cuts):
            output.extend(pipe.feed(chunk))
        output.extend(pipe.finish())
    assert not any(
        marker in output for marker in (b"event: response.completed", b"event: message_stop", b"data: [DONE]")
    )
