import io
import json
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post
from wire_cases import event, source_wire

from protoverta import convert_request, convert_response
from protoverta.errors import ConversionError, UnsupportedFeatureError
from protoverta.server import GatewayConfig
from protoverta.sse import SSEParser
from protoverta.streaming import (
    StreamDecoder,
    StreamError,
    UnsupportedStreamFeatureError,
)


def chat_refusal(value="private-refusal", *, reason="stop"):
    return {"model": "m", "choices": [{"message": {"role": "assistant", "content": None, "refusal": value}, "finish_reason": reason}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


@pytest.mark.parametrize(("source", "body", "feature"), [
    ("chat", chat_refusal(), "refusal"),
    ("chat", chat_refusal(None, reason="content_filter"), "content_filter"),
    ("messages", {"model": "m", "content": [], "stop_reason": "refusal"}, "refusal"),
    ("messages", {"model": "m", "content": [], "stop_reason": "stop_sequence", "stop_sequence": "private-sequence"}, "stop_sequence"),
    ("responses", {"model": "m", "status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "private-refusal"}]}]}, "refusal"),
    ("responses", {"model": "m", "status": "incomplete", "incomplete_details": {"reason": "content_filter"}, "output": []}, "content_filter"),
])
def test_known_unrepresentable_output_is_not_malformed_json(source, body, feature):
    for target in ("chat", "messages", "responses"):
        if source == target:
            assert convert_response(body, source, target) == body  # Native unchanged.
            continue
        with pytest.raises(UnsupportedFeatureError) as caught:
            convert_response(body, source, target)
        assert caught.value.feature == feature
        assert "private-" not in str(caught.value)


@pytest.mark.parametrize("value", [1, {}, [], True])
def test_malformed_refusal_is_not_treated_as_supported_wire_semantics(value):
    with pytest.raises(ConversionError) as caught:
        convert_response(chat_refusal(value), "chat", "responses")
    assert not isinstance(caught.value, UnsupportedFeatureError)


@pytest.mark.parametrize("target", ["messages", "responses"])
def test_known_unrepresentable_json_returns_non_retryable_422_not_502(target):
    config = GatewayConfig("chat", "https://example.test/v1", "key", "local")
    body = {"model": "m", "input": "hi"} if target == "responses" else {"model": "m", "messages": [], "max_tokens": 8}
    with listener(config, lambda request, timeout: io.BytesIO(json.dumps(chat_refusal()).encode())) as url, pytest.raises(HTTPError) as caught:
        post(url.removesuffix("responses") + target, body)
    assert caught.value.code == 422 and caught.value.headers["x-should-retry"] == "false"
    error = json.load(caught.value)
    caught.value.close()
    assert error["error"]["code"] == "policy_refusal"
    assert "private-refusal" not in json.dumps(error)


@pytest.mark.parametrize("visible", [False, True])
@pytest.mark.parametrize("feature", ["refusal", "content_filter"])
def test_unsupported_stream_semantics_never_become_success(visible, feature):
    def chunk(delta, finish=None):
        return event("message", {"object": "chat.completion.chunk", "model": "m", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})

    class Response(io.BytesIO):
        def __init__(self):
            super().__init__()
            self.chunks = iter([chunk({"role": "assistant", "content": "visible" if visible else ""}), chunk({"refusal": "private-refusal"} if feature == "refusal" else {}, "content_filter" if feature == "content_filter" else None)])

        def read1(self, size):
            return next(self.chunks, b"")

    config = GatewayConfig("chat", "https://example.test/v1", "key", "local")
    with listener(config, lambda request, timeout: Response()) as url:
        if visible:
            with post(url, {"model": "m", "input": "hi", "stream": True}) as response:
                raw = response.read()
            assert b"response.completed" not in raw and b"private-refusal" not in raw
            error = json.loads(SSEParser().feed(raw)[-1].data)
            assert error["code"] == "policy_refusal"
        else:
            with pytest.raises(HTTPError) as caught:
                post(url, {"model": "m", "input": "hi", "stream": True})
            assert caught.value.code == 422
            assert caught.value.headers["x-should-retry"] == "false"
            assert json.load(caught.value)["error"]["code"] == "policy_refusal"
            caught.value.close()


def test_response_refusal_part_is_recognized_but_not_flattened_to_text():
    wire = source_wire("responses", tools=False)
    events = SSEParser().feed(wire)
    start = events[:3]
    body = json.loads(start[-1].data)
    body["part"] = {"type": "refusal", "refusal": "private-refusal"}
    start[-1] = type(start[-1])(start[-1].event, json.dumps(body))
    decoder = StreamDecoder("responses")
    with pytest.raises(UnsupportedStreamFeatureError):
        for item in start:
            decoder.feed(item)


@pytest.mark.parametrize("source", ["chat", "messages", "responses"])
def test_unknown_or_invalid_termination_is_not_reported_as_known_unsupported(source):
    if source == "chat":
        body = chat_refusal(None, reason="invented_reason")
    elif source == "messages":
        body = {"model": "m", "content": [], "stop_reason": "invented_reason"}
    else:
        body = {"model": "m", "output": [], "status": "invented_status"}
    with pytest.raises(ConversionError) as caught:
        convert_response(body, source, "chat" if source != "chat" else "responses")
    assert not isinstance(caught.value, UnsupportedFeatureError)


@pytest.mark.parametrize("target", ["messages", "responses"])
def test_chat_include_usage_transport_option_maps_without_silent_parameter_drop(target):
    body = {"model": "m", "messages": [], "stream": True, "max_tokens": 8, "stream_options": {"include_usage": True}}
    result = convert_request(body, "chat", target)
    assert result["stream"] is True and "stream_options" not in result
    for value in (False,):
        body["stream_options"]["include_usage"] = value
        with pytest.raises(UnsupportedFeatureError):
            convert_request(body, "chat", target)
    for value in (1, "true"):
        body["stream_options"]["include_usage"] = value
        with pytest.raises(ConversionError) as caught:
            convert_request(body, "chat", target)
        assert not isinstance(caught.value, UnsupportedFeatureError)
    body["stream_options"] = {"include_usage": True, "unknown": True}
    with pytest.raises(ConversionError):
        convert_request(body, "chat", target)


@pytest.mark.parametrize("source", ["chat", "responses"])
def test_measured_cache_writes_survive_messages_conversion_and_roundtrip(source):
    usage = {"input_tokens": 19, "output_tokens": 7, "input_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 4}}
    response = {"model": "m", "status": "completed", "output": [], "usage": usage}
    if source == "chat":
        response = {"model": "m", "choices": [{"message": {"role": "assistant", "content": None}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 19, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 4}}}
    messages = convert_response(response, source, "messages")
    assert messages["usage"] == {"input_tokens": 12, "output_tokens": 7, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4}
    result = convert_response(messages, "messages", source)
    details = result["usage"]["prompt_tokens_details" if source == "chat" else "input_tokens_details"]
    assert details == {"cached_tokens": 3, "cache_write_tokens": 4}


def test_unknown_cache_writes_are_not_fabricated_to_satisfy_new_sdk_schema():
    result = convert_response(chat_refusal(None), "chat", "responses")
    assert "cache_write_tokens" not in result["usage"].get("input_tokens_details", {})


def test_unsupported_feature_categories_cannot_contain_arbitrary_provider_text():
    with pytest.raises(ValueError, match="unknown"):
        UnsupportedFeatureError("private-provider-text")
    assert issubclass(UnsupportedStreamFeatureError, StreamError)
