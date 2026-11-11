"""Explicit null legacy Chat output fields are equivalent to omission."""

import json

import pytest

from protoverta import ConversionError, convert_response
from protoverta.stream_pipe import StreamTranscoder
from protoverta.streaming import StreamError

USAGE = {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}


def _response(message):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "m",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop", "logprobs": None}],
        "usage": USAGE,
    }


def _chunk(delta, finish=None, **extra):
    return {
        "id": "c",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        **extra,
    }


def _stream(target, chunks):
    transcoder = StreamTranscoder("chat", target, messages_usage_policy="deferred")
    out = b""
    for chunk in chunks:
        out += transcoder.feed(f"data: {json.dumps(chunk)}\n\n".encode())
    out += transcoder.feed(b"data: [DONE]\n\n")
    return out + transcoder.finish()


@pytest.mark.parametrize("target", ["messages", "responses"])
def test_json_null_function_call_and_audio_are_omitted(target):
    message = {
        "role": "assistant",
        "content": "hi",
        "function_call": None,
        "audio": None,
        "tool_calls": None,
        "refusal": None,
    }
    converted = convert_response(_response(message), "chat", target)
    assert "hi" in json.dumps(converted)


@pytest.mark.parametrize("field,value", [("function_call", {"name": "f", "arguments": "{}"}), ("audio", {"id": "a"})])
def test_json_non_null_legacy_fields_still_rejected(field, value):
    with pytest.raises(ConversionError, match=field):
        convert_response(_response({"role": "assistant", "content": "hi", field: value}), "chat", "messages")


@pytest.mark.parametrize("target", ["messages", "responses"])
def test_stream_null_function_call_and_audio_are_omitted(target):
    out = _stream(
        target,
        [
            _chunk({"role": "assistant", "content": "hi", "function_call": None, "audio": None}),
            _chunk({}, "stop", usage=USAGE),
        ],
    )
    assert b"hi" in out


@pytest.mark.parametrize("field,value", [("function_call", {"name": "f"}), ("audio", {"id": "a"})])
def test_stream_non_null_legacy_fields_still_rejected(field, value):
    with pytest.raises(StreamError, match=field):
        _stream(
            "messages", [_chunk({"role": "assistant", "content": "hi", field: value}), _chunk({}, "stop", usage=USAGE)]
        )
