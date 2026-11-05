import copy
import io
import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from wire_cases import source_wire

from protoverta.certification import check_route, evidence_report
from protoverta.core import ConversionError, convert_request, convert_response
from protoverta.json_wire import decode_json, encode_json
from protoverta.server import GatewayConfig, make_handler
from protoverta.sse import SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta, StreamDecoder, StreamError

PROTOCOLS = ("chat", "messages", "responses")
PATHS = {"chat": "/v1/chat/completions", "messages": "/v1/messages", "responses": "/v1/responses"}
VALID = {
    "model": "m",
    "max_tokens": 20,
    "messages": [{"role": "user", "content": "valid 🌏"}],
    "tools": [{"type": "function", "function": {"name": "f", "parameters": {"type": "object", "properties": {"x": {"type": "number", "enum": [17]}}}}}],
}
RESULT = {
    "model": "m",
    "choices": [{"message": {"role": "assistant", "content": "valid 🌏"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 2, "completion_tokens": 1},
}


@contextmanager
def listener(protocol, upstream):
    errors = []

    class CheckedServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            errors.append("uncaught handler exception")

    server = CheckedServer(("127.0.0.1", 0), make_handler(GatewayConfig(protocol, "https://example.invalid/v1", "fixture", "local"), upstream_open=upstream))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not errors


def request(origin, source, raw):
    return urlopen(Request(origin + PATHS[source], data=raw, headers={"Authorization": "Bearer local", "Content-Type": "application/json"}), timeout=3)


@pytest.mark.parametrize("source", PROTOCOLS)
@pytest.mark.parametrize("target", PROTOCOLS)
def test_invalid_request_json_rejects_without_dispatch_and_recovers(source, target):
    calls = []

    def upstream(req, *, timeout):
        calls.append(req)
        return io.BytesIO(encode_json(convert_response(RESULT, "chat", target)))

    good = encode_json(convert_request(VALID, "chat", source))
    invalid = [b'{"x":' + b"1" * 5000 + b"}", b'{"x":"\\ud800"}', b'{"\\udfff":"x"}']
    for number in (b"NaN", b"Infinity", b"-Infinity", b"1e309", b"-1e309"):
        raw = good.replace(b'"enum": [17]', b'"enum": [' + number + b"]")
        assert raw != good
        invalid.append(raw)
    with listener(target, upstream) as origin:
        for raw in invalid:
            with pytest.raises(HTTPError) as caught:
                request(origin, source, raw)
            assert caught.value.code == 422
            assert caught.value.headers["x-should-retry"] == "false"
            assert isinstance(json.load(caught.value)["error"], dict)
            caught.value.close()
        assert calls == []
        with request(origin, source, good) as response:
            assert response.status == 200
            assert "valid 🌏" in json.dumps(json.load(response), ensure_ascii=False)
    assert len(calls) == 1


@pytest.mark.parametrize("bad", [b'{"x":' + b"1" * 5000 + b"}", b'{"x":NaN}', b'{"x":1e309}', b'{"x":"\\ud800"}'])
@pytest.mark.parametrize("target", PROTOCOLS)
def test_invalid_upstream_json_returns_structured_failure_and_recovers(bad, target):
    responses = iter([bad, encode_json(RESULT)])
    with listener("chat", lambda req, timeout: io.BytesIO(next(responses))) as origin:
        raw = encode_json(convert_request(VALID, "chat", target))
        with pytest.raises(HTTPError) as caught:
            request(origin, target, raw)
        assert caught.value.code == 502
        assert isinstance(json.load(caught.value)["error"], dict)
        caught.value.close()
        with request(origin, target, raw) as response:
            assert response.status == 200


@pytest.mark.parametrize("source,target", [(a, b) for a in PROTOCOLS for b in PROTOCOLS if a != b])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "\ud800"])
def test_library_rejects_invalid_schema_values_in_requests_and_responses(source, target, bad):
    body = convert_request(copy.deepcopy(VALID), "chat", source)
    tool = body["tools"][0]
    schema = tool["function"]["parameters"] if source == "chat" else tool["input_schema" if source == "messages" else "parameters"]
    schema["properties"]["x"]["enum"] = [bad]
    with pytest.raises(ConversionError, match="valid JSON"):
        convert_request(body, source, target)
    result = convert_response(RESULT, "chat", source)
    result["model"] = bad
    with pytest.raises(ConversionError, match="valid JSON"):
        convert_response(result, source, target)


@pytest.mark.parametrize("source", ["chat", "messages"])
def test_doctor_does_not_certify_inconsistent_stream_tool_finish(source):
    encoder = StreamEncoder(source)
    deltas = [
        Delta("start", model="m", input_tokens=1, output_tokens=0),
        Delta("tool_start", index=0, call_id="fixture_call", name="fixture_nonce"),
        Delta("tool_delta", index=0, text="{}"),
        Delta("tool_end", index=0),
        Delta("finish", reason="tool_calls", input_tokens=1, output_tokens=1),
    ]
    raw = b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta))
    if source == "chat":
        raw = raw.replace(b'"finish_reason":"tool_calls"', b'"finish_reason":"stop"')
    else:
        raw = raw.replace(b'"stop_reason":"tool_use"', b'"stop_reason":"end_turn"')
    parser, decoder = SSEParser(), StreamDecoder(source)
    with pytest.raises(StreamError, match="finish reason"):
        for event in parser.feed(raw):
            decoder.feed(event)
    config = GatewayConfig(source, "https://example.invalid/v1", "fixture", "local")
    report = check_route(config, "m", source, cases=("streaming_tools",), upstream_open=lambda req, timeout: io.BytesIO(raw))
    assert not report[0]["passed"]
    assert evidence_report(config, "m", source, report, verified_with="synthetic")["routes"] == []


@pytest.mark.parametrize("source", PROTOCOLS)
def test_length_termination_with_tool_calls_remains_valid(source):
    parser, decoder = SSEParser(), StreamDecoder(source)
    deltas = [delta for event in parser.feed(source_wire(source, length=True)) for delta in decoder.feed(event)]
    decoder.finish()
    assert deltas[-1].reason == "length"


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("after_payload", [False, True])
def test_bad_sse_json_never_produces_success(protocol, after_payload):
    raw = source_wire(protocol, tools=False)
    bad = b'event: message\ndata: {"x":' + b"1" * 5000 + b"}\n\n"
    # Force a separate read after valid payload has been forwarded. Remove the
    # original terminal event so this exercises a parser error mid-stream,
    # rather than just rejecting an event after an already completed stream.
    chunks = [raw[: raw.rfind(b"event: ")], bad] if after_payload else [bad]

    class Fragmented(io.BytesIO):
        def __init__(self):
            super().__init__()
            self.parts = iter(chunks)
            self.pending = b""

        def read1(self, size):
            if not self.pending:
                self.pending = next(self.parts, b"")
            part, self.pending = self.pending[:size], self.pending[size:]
            return part

    with listener(protocol, lambda req, timeout: Fragmented()) as origin:
        target = "responses" if protocol != "responses" else "chat"
        body = convert_request(VALID, "chat", target) | {"stream": True}
        try:
            with request(origin, target, encode_json(body)) as response:
                assert after_payload and response.status == 200
                output = response.read()
            assert b"event: error" in output
            assert b"response.completed" not in output
            assert b"[DONE]" not in output
        except HTTPError as exc:
            assert not after_payload and exc.code == 502
            assert isinstance(json.load(exc)["error"], dict)
            exc.close()


def test_strict_json_keeps_valid_unicode_and_opaque_argument_text():
    value = {"🌏": "valid 𝄞", "number": -1.25e-12, "text": "NaN and Infinity"}
    assert decode_json(encode_json(value)) == value
    assert decode_json(b'{"x":"\\ud83c\\udf0f"}') == {"x": "🌏"}
