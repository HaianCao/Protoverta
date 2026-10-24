import io
import json
import threading
from http.client import IncompleteRead
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from protoverta import ConversionError, convert_request, convert_response
from protoverta.server import GatewayConfig, make_handler
from protoverta.sse import SSEEvent
from protoverta.streaming import StreamDecoder, StreamError


@pytest.mark.parametrize("invalid", [[], {}, ["completed"], {"value": "completed"}])
def test_invalid_json_enums_raise_conversion_error_not_python_type_error(invalid):
    requests = [
        ("messages", {"model": "m", "max_tokens": 8, "messages": [{"role": invalid, "content": "hi"}]}),
        ("responses", {"model": "m", "input": [{"role": invalid, "content": "hi"}]}),
        ("responses", {"model": "m", "input": [{"role": "user", "content": "hi", "status": invalid}]}),
        ("responses", {"model": "m", "input": [{"role": "user", "content": [{"type": invalid, "text": "hi"}]}]}),
        ("chat", {"model": "m", "messages": [], "tool_choice": {"type": invalid}})]
    for source, body in requests:
        with pytest.raises(ConversionError):
            convert_request(body, source, "chat" if source == "responses" else "responses")
    results = [
        ("chat", {"model": "m", "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": invalid}]}),
        ("messages", {"model": "m", "role": invalid, "content": [], "stop_reason": "end_turn"}),
        ("messages", {"model": "m", "content": [], "stop_reason": invalid}),
        ("responses", {"model": "m", "status": invalid, "output": []}),
        ("responses", {"model": "m", "status": "completed", "output": [{"type": "message", "role": "assistant", "status": invalid, "content": []}]})]
    for source, body in results:
        with pytest.raises(ConversionError):
            convert_response(body, source, "chat" if source != "chat" else "responses")


@pytest.mark.parametrize("invalid", [[], {}])
def test_malformed_stream_enums_are_reported_as_stream_errors(invalid):
    decoder = StreamDecoder("chat")
    event = SSEEvent("message", json.dumps({"object": "chat.completion.chunk", "model": "m", "choices": [{"index": 0, "delta": {"role": invalid}, "finish_reason": None}]}))
    with pytest.raises(StreamError):
        decoder.feed(event)
    decoder = StreamDecoder("messages")
    decoder.feed(SSEEvent("message_start", json.dumps({"type": "message_start", "message": {"model": "m", "content": [], "usage": {"input_tokens": 1, "output_tokens": 0}}})))
    with pytest.raises(StreamError):
        decoder.feed(SSEEvent("message_delta", json.dumps({"type": "message_delta", "delta": {"stop_reason": invalid}})))


@pytest.mark.parametrize("malformed_upstream", [False, True])
def test_gateway_returns_structured_errors_and_remains_usable(malformed_upstream):
    calls = []

    def upstream(request, *, timeout):
        calls.append(request)
        body = {"model": "m", "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": [] if malformed_upstream and len(calls) == 1 else "stop"}]}
        return io.BytesIO(json.dumps(body).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(GatewayConfig("chat", "https://example.test/v1", "fixture", "local"), upstream_open=upstream))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/responses"
        bad = {"model": "m", "input": "hi"} if malformed_upstream else {"model": "m", "input": [{"role": "user", "content": "hi", "status": []}]}
        request = Request(url, data=json.dumps(bad).encode(), headers={"Authorization": "Bearer local"})
        with pytest.raises(HTTPError) as caught:
            urlopen(request, timeout=3)
        assert caught.value.code == (502 if malformed_upstream else 422)
        assert isinstance(json.load(caught.value)["error"], dict)
        caught.value.close()
        if not malformed_upstream:
            assert not calls
            nested = b'{"model":"m","input":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}'
            deep = Request(url, data=nested, headers={"Authorization": "Bearer local"})
            with pytest.raises(HTTPError) as too_deep:
                urlopen(deep, timeout=3)
            assert too_deep.value.code == 422
            assert isinstance(json.load(too_deep.value)["error"]["message"], str)
            too_deep.value.close()
            assert not calls
        good = Request(url, data=b'{"model":"m","input":"hi"}', headers={"Authorization": "Bearer local"})
        with urlopen(good, timeout=3) as response:
            assert json.load(response)["output"][0]["content"][0]["text"] == "ok"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("streaming", [False, True])
def test_truncated_http_body_is_a_structured_failure_not_a_handler_crash(streaming):
    class Truncated(io.BytesIO):
        def read1(self, _size):
            raise IncompleteRead(b"private-partial-payload", 30)

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(GatewayConfig("chat", "https://example.test/v1", "fixture", "local"), upstream_open=lambda *_args, **_kwargs: Truncated()))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        request = Request(f"http://127.0.0.1:{server.server_port}/v1/responses", data=json.dumps({"model": "m", "input": "hi", "stream": streaming}).encode(), headers={"Authorization": "Bearer local"})
        with pytest.raises(HTTPError) as caught:
            urlopen(request, timeout=3)
        assert caught.value.code == 502
        payload = caught.value.read()
        caught.value.close()
        assert isinstance(json.loads(payload)["error"], dict)
        assert b"private-partial-payload" not in payload
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
