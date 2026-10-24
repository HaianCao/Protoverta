import json
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from protoverta.resources import DeadlineExceeded, RequestBudget, StreamLimits
from protoverta.server import GatewayConfig, gateway_session
from protoverta.sse import SSEEvent, encode_sse
from protoverta.stream_pipe import StreamTranscoder, StreamValidator
from protoverta.streaming import StreamError
from protoverta.transport import (
    DeadlineHTTPHandler,
    DeadlineHTTPSHandler,
    _TimedResponse,
)


def test_deadline_https_transport_keeps_certificate_and_hostname_verification():
    handler = DeadlineHTTPSHandler()
    with patch.object(handler, "do_open") as opening:
        handler.https_open(Request("https://example.test/v1/responses"))
    factory = opening.call_args.args[0]
    connection = factory("example.test", timeout=1, **opening.call_args.kwargs)
    assert connection._context.verify_mode == ssl.CERT_REQUIRED
    assert connection._context.check_hostname
    connection.close()


def test_completed_content_length_body_allows_followup_eof_read():
    reader, writer = socket.socketpair()
    try:
        writer.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
        with _TimedResponse(reader) as response:
            response.begin()
            budget = RequestBudget(1, 2, 1)
            assert budget.read(response, 4096, streaming=True) == b"OK"
            assert budget.read(response, 4096, streaming=True) == b""
    finally:
        reader.close()
        writer.close()


def test_progress_does_not_reset_total_deadline():
    now = [0]
    budget = RequestBudget(10, 5, 2, clock=lambda: now[0])
    for moment in (1, 2, 3, 4):
        now[0] = moment
        budget.progress()
    now[0] = 5
    with pytest.raises(DeadlineExceeded) as caught:
        budget.remaining()
    assert caught.value.kind == "request_deadline"


def test_os_timer_rounding_does_not_make_deadlines_retryable_io_timeouts():
    now = [0]
    budget = RequestBudget(2, 0.2, 3, clock=lambda: now[0])
    limit = budget.remaining()
    now[0] = 0.1999
    with pytest.raises(DeadlineExceeded) as caught:
        budget.on_timeout(limit)
    assert caught.value.kind == "request_deadline"
    budget = RequestBudget(0.1, 5, 5, clock=lambda: now[0])
    # A genuine shorter I/O timeout retains its transport classification.
    assert budget.on_timeout(budget.remaining()) is None


@pytest.mark.parametrize("framing", ["chunk_header", "chunk_terminator", "trailer", "body"])
@pytest.mark.parametrize("deadline", ["total", "progress"])
def test_deadlines_bound_chunked_framing_and_body(framing, deadline):
    from urllib.request import build_opener

    started_drip = threading.Event()
    release = threading.Event()

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            prefixes = {"chunk_header": b"", "chunk_terminator": b"1\r\nX", "trailer": b"0\r\n", "body": b"100\r\n"}
            drip = b"1;extension=" + b"a" * 60 if framing == "chunk_header" else b"X-Trailer: " + b"a" * 60 if framing == "trailer" else b"\r\n" if framing == "chunk_terminator" else b"x" * 60
            try:
                self.wfile.write(prefixes[framing])
                self.wfile.flush()
                started_drip.set()
                if framing == "chunk_terminator":
                    release.wait(2)
                    return
                for char in drip:
                    self.wfile.write(bytes([char]))
                    self.wfile.flush()
                    if release.wait(0.02):
                        break
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        budget = RequestBudget(2, 0.2 if deadline == "total" else 3, 0.2 if deadline == "progress" else 3)
        request = Request(f"http://127.0.0.1:{server.server_port}")
        request.protoverta_budget = budget
        start = time.monotonic()
        with build_opener(DeadlineHTTPHandler()).open(request, timeout=budget.remaining()) as response, pytest.raises(DeadlineExceeded) as caught:
            assert started_drip.wait(1)
            while budget.read(response, 4096, streaming=framing != "body"):
                pass
        assert caught.value.kind == ("request_deadline" if deadline == "total" else "progress_timeout")
        assert time.monotonic() - start < 0.75
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_heartbeats_and_repeated_function_metadata_are_not_progress():
    limits = StreamLimits()
    for _ in range(3):
        limits.observe(SSEEvent("ping", '{"type":"ping"}'))
    assert limits.progress == 0
    event = SSEEvent("message", json.dumps({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"name": "fn", "arguments": ""}}]}}]}))
    limits.observe(event)
    before = limits.progress
    limits.observe(event)
    assert limits.progress == before


@pytest.mark.parametrize("block", [
    {"type": "tool_use", "input": {"value": "long input"}},
    {"type": "custom_tool_call", "input": "long input"},
])
def test_native_initial_tool_inputs_also_count_toward_argument_budget(block):
    limits = StreamLimits(max_argument_bytes=4)
    event = SSEEvent("content_block_start", json.dumps({"type": "content_block_start", "content_block": block}))
    with pytest.raises(StreamError, match="arguments exceed"):
        limits.observe(event)


@pytest.mark.parametrize("factory", [lambda **kw: StreamTranscoder("chat", "responses", **kw), lambda **kw: StreamValidator("chat", **kw)])
def test_native_and_translated_total_stream_limits(factory):
    pipe = factory(max_stream_bytes=10)
    with pytest.raises(StreamError, match="total byte"):
        pipe.feed(b"x" * 11)
    pipe = factory(max_tool_argument_bytes=4)
    body = {"object": "chat.completion.chunk", "model": "m", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "a", "type": "function", "function": {"name": "f", "arguments": "12345"}}]}, "finish_reason": None}]}
    with pytest.raises(StreamError, match="arguments exceed"):
        pipe.feed(encode_sse("message", json.dumps(body)))
    pipe = factory(max_stream_items=1)
    body["choices"][0]["delta"]["tool_calls"] = [{"index": i, "id": str(i), "function": {"name": "f", "arguments": ""}} for i in (0, 1)]
    with pytest.raises(StreamError, match="item limit"):
        pipe.feed(encode_sse("message", json.dumps(body)))


@pytest.mark.parametrize("options", [{"total_timeout": float("inf")}, {"progress_timeout": True}, {"max_stream_bytes": 0}, {"max_stream_items": 1.5}, {"max_tool_argument_bytes": False}])
def test_config_rejects_invalid_resource_budgets(options):
    with pytest.raises(ValueError):
        GatewayConfig("chat", "https://example.test/v1", "key", "local", **options)


@pytest.mark.parametrize("mode", ["heartbeat", "body", "headers"])
def test_default_transport_stops_heartbeat_or_slow_drip_body(mode):
    streaming = mode == "heartbeat"
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if mode != "headers":
                self.send_response(200)
                self.end_headers()
            try:
                if mode == "headers":
                    self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                    self.wfile.flush()
                if streaming:
                    self.wfile.write(encode_sse("message_start", json.dumps({"type": "message_start", "message": {"id": "m", "model": "m", "type": "message", "role": "assistant", "content": [], "usage": {"input_tokens": 1, "output_tokens": 0}, "stop_reason": None, "stop_sequence": None}})))
                    self.wfile.flush()
                for _ in range(30):
                    self.wfile.write(encode_sse("ping", '{"type":"ping"}') if streaming else b" ")
                    self.wfile.flush()
                    time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError):
                pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    upstream.daemon_threads = True
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        config = GatewayConfig("messages", f"http://127.0.0.1:{upstream.server_port}/v1", "fixture", "local", progress_timeout=0.15 if streaming else 2, total_timeout=2 if streaming else 0.15)
        with gateway_session(config) as url:
            request = Request(url + "/v1/responses", data=json.dumps({"model": "m", "input": "hi", "stream": streaming, "max_output_tokens": 64}).encode(), headers={"Authorization": "Bearer local"})
            with pytest.raises(HTTPError) as caught:
                urlopen(request, timeout=3)
            assert caught.value.code == 504
            assert json.load(caught.value)["error"]["code"] == ("progress_timeout" if streaming else "request_deadline")
            caught.value.close()
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=2)
