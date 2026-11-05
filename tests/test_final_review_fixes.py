import io
import json
from urllib.error import HTTPError

import pytest
from test_json_wire_boundaries import RESULT, encode_json, listener, request

from protoverta.core import ConversionError, convert_request, convert_response
from protoverta.sse import SSEEvent, SSEParser, encode_sse
from protoverta.stream_pipe import StreamTranscoder, StreamValidator
from protoverta.streaming import StreamDecoder, StreamError

PROTOCOLS = ("chat", "messages", "responses")
BASE = {"model": "m", "max_tokens": 20, "messages": [{"role": "user", "content": "synthetic"}]}


@pytest.mark.parametrize("source,target", [(a, b) for a in PROTOCOLS for b in PROTOCOLS if a != b])
@pytest.mark.parametrize("temperature", [0, 0.5, 1])
def test_shared_temperature_subset_preserves_value(source, target, temperature):
    body = convert_request(BASE, "chat", source) | {"temperature": temperature}
    assert convert_request(body, source, target)["temperature"] == temperature


@pytest.mark.parametrize("source,target", [("chat", "messages"), ("responses", "messages"), ("messages", "chat"), ("messages", "responses")])
@pytest.mark.parametrize("temperature", [1.01, 2])
def test_messages_temperature_bounds_reject_without_clamping(source, target, temperature):
    body = convert_request(BASE, "chat", source) | {"temperature": temperature}
    with pytest.raises(ConversionError, match="Messages temperature"):
        convert_request(body, source, target)
    assert convert_request(body, source, source) == body


@pytest.mark.parametrize("source,target", [("chat", "responses"), ("responses", "chat")])
def test_other_protocols_keep_temperature_two(source, target):
    body = convert_request(BASE, "chat", source) | {"temperature": 2}
    assert convert_request(body, source, target)["temperature"] == 2


@pytest.mark.parametrize("source", ["chat", "responses"])
def test_temperature_local_http_rejection_does_not_dispatch(source):
    calls = []

    def upstream(req, *, timeout):
        calls.append(req)
        return io.BytesIO(encode_json(convert_response(RESULT, "chat", "messages")))

    body = convert_request(BASE, "chat", source) | {"temperature": 1.5}
    with listener("messages", upstream) as origin:
        with pytest.raises(HTTPError) as caught:
            request(origin, source, encode_json(body))
        assert caught.value.code == 422
        assert caught.value.headers["x-should-retry"] == "false"
        caught.value.close()
        assert calls == []
        body["temperature"] = 0.5
        with request(origin, source, encode_json(body)) as response:
            assert response.status == 200
        assert len(calls) == 1


def finish_chunk(reason="stop"):
    delta = {"role": "assistant", "content": "synthetic"}
    if reason == "tool_calls":
        delta["tool_calls"] = [{"index": 0, "id": "c", "type": "function", "function": {"name": "fixture", "arguments": "{}"}}]
    return {
        "object": "chat.completion.chunk",
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }


@pytest.mark.parametrize("target", PROTOCOLS)
@pytest.mark.parametrize("reason", ["stop", "tool_calls", "length"])
def test_measured_usage_in_finishing_choice_survives_every_target(target, reason):
    raw = encode_sse("message", json.dumps(finish_chunk(reason))) + encode_sse("message", "[DONE]")
    pipe = StreamValidator("chat") if target == "chat" else StreamTranscoder("chat", target, messages_usage_policy="deferred")
    output = pipe.feed(raw) + pipe.finish()
    decoder = StreamDecoder(target)
    deltas = [delta for event in SSEParser().feed(output) for delta in decoder.feed(event)]
    decoder.finish()
    assert (deltas[-1].input_tokens, deltas[-1].output_tokens) == (3, 2)
    assert deltas[-1].reason == reason


@pytest.mark.parametrize("repeated_choice", [False, True])
@pytest.mark.parametrize("counts", [(3, 2), (4, 3)])
def test_duplicate_or_conflicting_usage_after_same_chunk_measurement_is_rejected(repeated_choice, counts):
    decoder = StreamDecoder("chat")
    decoder.feed(SSEEvent("message", json.dumps(finish_chunk())))
    body = finish_chunk()
    body["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}] if repeated_choice else []
    body["usage"] = {"prompt_tokens": counts[0], "completion_tokens": counts[1]}
    with pytest.raises(StreamError, match="after|duplicated|order"):
        decoder.feed(SSEEvent("message", json.dumps(body)))


def test_nonterminal_nonnull_usage_is_explicitly_rejected():
    body = finish_chunk()
    body["choices"][0]["finish_reason"] = None
    with pytest.raises(StreamError, match="usage requires"):
        StreamDecoder("chat").feed(SSEEvent("message", json.dumps(body)))


def test_null_usage_placeholder_does_not_prevent_final_measurements():
    body = finish_chunk()
    body["choices"][0]["finish_reason"] = None
    body["usage"] = None
    decoder = StreamDecoder("chat")
    decoder.feed(SSEEvent("message", json.dumps(body)))
    decoder.feed(SSEEvent("message", json.dumps(finish_chunk())))
    assert decoder.feed(SSEEvent("message", "[DONE]"))[-1].input_tokens == 3
