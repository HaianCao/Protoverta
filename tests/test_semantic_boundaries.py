import io
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from protoverta.certification import check_route, evidence_report
from protoverta.core import ConversionError, convert_request, convert_response
from protoverta.server import GatewayConfig, make_handler
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta, StreamError, _valid_arguments


def chat_result(arguments="{}", call_id="call_fixture"):
    return {
        "model": "m",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": call_id, "type": "function", "function": {"name": "fixture_nonce", "arguments": arguments}}],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("call_id", ["", "opaque:/+=" + "x" * 256])
def test_doctor_requires_nonempty_call_identity_without_normalizing_it(protocol, call_id):
    result = convert_response(chat_result(), "chat", protocol)
    if protocol == "chat":
        result["choices"][0]["message"]["tool_calls"][0]["id"] = call_id
    elif protocol == "messages":
        result["content"][0]["id"] = call_id
    else:
        result["output"][0]["call_id"] = call_id
    config = GatewayConfig(protocol, "https://example.invalid/v1", "fixture", "local")
    report = check_route(config, "m", protocol, cases=("tools",), upstream_open=lambda request, timeout: io.BytesIO(json.dumps(result).encode()))
    assert report[0]["passed"] is bool(call_id)
    evidence = evidence_report(config, "m", protocol, report, verified_with="synthetic fixture")
    assert bool(evidence["routes"]) is bool(call_id)


@pytest.mark.parametrize("details", [None, 0, 2])
@pytest.mark.parametrize("streaming", [False, True])
def test_responses_usage_preserves_unknown_and_measured_details(details, streaming):
    if streaming:
        encoder = StreamEncoder("responses")
        encoder.feed(Delta("start", model="m"))
        events = encoder.feed(Delta("finish", reason="stop", input_tokens=10, output_tokens=5, cached_input_tokens=details, reasoning_output_tokens=details))
        usage = json.loads(events[-1].data)["response"]["usage"]
    else:
        result = {
            "model": "m",
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        if details is not None:
            result["usage"].update(prompt_tokens_details={"cached_tokens": details}, completion_tokens_details={"reasoning_tokens": details})
        usage = convert_response(result, "chat", "responses")["usage"]
    assert usage["total_tokens"] == 15
    if details is None:
        assert "input_tokens_details" not in usage
        assert "output_tokens_details" not in usage
    else:
        assert usage["input_tokens_details"] == {"cached_tokens": details}
        assert usage["output_tokens_details"] == {"reasoning_tokens": details}


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e309", "-1e309"])
@pytest.mark.parametrize("source", ["chat", "messages", "responses"])
def test_nonfinite_function_arguments_rejected_on_every_translated_edge(number, source):
    response = convert_response(chat_result(), "chat", source)
    raw = '{"nested":[{"value":' + number + "}]} "
    if source == "chat":
        response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = raw
    elif source == "messages":
        response["content"][0]["input"] = json.loads(raw)
    else:
        response["output"][0]["arguments"] = raw
    for target in {"chat", "messages", "responses"} - {source}:
        with pytest.raises(ConversionError, match="valid JSON"):
            convert_response(response, source, target)
    with pytest.raises(StreamError, match="valid JSON"):
        _valid_arguments(raw, "tool arguments")
    for target in ("chat", "messages", "responses"):
        encoder = StreamEncoder(target)
        encoder.feed(Delta("start", model="m", input_tokens=1, output_tokens=0))
        encoder.feed(Delta("tool_start", index=0, call_id="c", name="f"))
        encoder.feed(Delta("tool_delta", index=0, text=raw))
        with pytest.raises(StreamError, match="valid JSON"):
            encoder.feed(Delta("tool_end", index=0))


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e309", "-1e309"])
def test_nonfinite_request_arguments_fail_locally(number):
    raw = '{"value":' + number + "}"
    body = {
        "model": "m",
        "max_tokens": 20,
        "messages": [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function", "function": {"name": "f", "arguments": raw}}]},
            {"role": "tool", "tool_call_id": "c", "content": "synthetic result"},
        ],
    }
    for target in ("messages", "responses"):
        with pytest.raises(ConversionError, match="valid JSON"):
            convert_request(body, "chat", target)


def test_finite_arguments_keep_original_spelling():
    raw = '{ "value": -1.25e-12, "text": "NaN", "large": 123456789012345678901234567890 }'
    converted = convert_response(chat_result(raw), "chat", "responses")
    assert converted["output"][0]["arguments"] == raw


@pytest.mark.parametrize("source,target", [(a, b) for a in ("chat", "messages", "responses") for b in ("chat", "messages", "responses") if a != b])
def test_invalid_arguments_never_dispatch_or_retry_upstream(source, target):
    dispatched = []

    def upstream(request, *, timeout):
        dispatched.append(request)
        raise AssertionError("invalid tool arguments reached upstream")

    config = GatewayConfig(target, "https://example.invalid/v1", "fixture", "local")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=upstream))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    body = {
        "model": "m",
        "max_tokens": 20,
        "messages": [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c", "content": "synthetic result"},
        ],
    }
    body = convert_request(body, "chat", source)
    endpoint = {"chat": "/v1/chat/completions", "messages": "/v1/messages", "responses": "/v1/responses"}[source]
    try:
        for number in ("NaN", "Infinity", "-Infinity", "1e309", "-1e309"):
            raw = '{"nested":[{"value":' + number + "}]}"
            if source == "chat":
                body["messages"][0]["tool_calls"][0]["function"]["arguments"] = raw
            elif source == "messages":
                body["messages"][0]["content"][0]["input"] = json.loads(raw)
            else:
                body["input"][0]["arguments"] = raw
            request = Request(
                f"http://127.0.0.1:{server.server_port}" + endpoint,
                data=json.dumps(body).encode(),
                headers={"Authorization": "Bearer local", "Content-Type": "application/json"},
            )
            with pytest.raises(HTTPError) as caught:
                urlopen(request, timeout=3)
            assert caught.value.code in (400, 422)
            assert caught.value.headers["x-should-retry"] == "false"
            caught.value.close()
        assert dispatched == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
