"""Real sockets, synthetic upstreams; no provider credentials or CLI state."""

import json
import threading
import time
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from protoverta import server as gateway
from protoverta.core import Protocol
from protoverta.server import GatewayConfig, gateway_session
from protoverta.sse import SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta


def wire(source, *, tool=False):
    encoder = StreamEncoder(source)

    def emit(deltas):
        return b"".join(encode_sse(event.event, event.data, id=event.id) for delta in deltas for event in encoder.feed(delta))

    first = emit([Delta("start", model="fixture", input_tokens=2, output_tokens=0), Delta("text", text="seed")])
    middle = b""
    tail = []
    if tool:
        middle = emit([Delta("tool_start", index=0, name="work", call_id="call-1"), Delta("tool_delta", index=0, text='{"x":')])
        tail = [Delta("tool_delta", index=0, text="1}"), Delta("tool_end", index=0)]
    last = emit([*tail, Delta("finish", reason="tool_calls" if tool else "stop", input_tokens=2, output_tokens=2)])
    return first, middle, last


@contextmanager
def slow_upstream(source, *, tool=False, terminal_before_pause=False, invalid_tail=False, unsupported_first=False, detect_disconnect=False):
    release, paused, closed = threading.Event(), threading.Event(), threading.Event()
    first, middle, last = wire(source, tool=tool)
    if unsupported_first:
        first = encode_sse("message", json.dumps({"object": "chat.completion.chunk", "model": "fixture", "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}))
        last = encode_sse("message", json.dumps({"object": "chat.completion.chunk", "model": "fixture", "choices": [{"index": 0, "delta": {"reasoning_content": "synthetic-private"}, "finish_reason": None}]}))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                self.wfile.write(first + middle + (last if terminal_before_pause else b""))
                self.wfile.flush()
                paused.set()
                if detect_disconnect:
                    self.connection.settimeout(2)
                    assert not self.connection.recv(1)
                    return
                if not release.wait(3):
                    return
                self.wfile.write(b"event: invalid\ndata: {\"invalid\":true}\n\n" if invalid_tail else b"" if terminal_before_pause else last)
                self.wfile.flush()
            except (ConnectionError, OSError):
                pass
            finally:
                closed.set()

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    upstream.daemon_threads = True
    worker = threading.Thread(target=upstream.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{upstream.server_port}/v1", release, paused, closed, first + middle + last
    finally:
        release.set()
        upstream.shutdown()
        upstream.server_close()
        worker.join(timeout=2)


def connect(origin, target):
    location = urlsplit(origin)
    connection = HTTPConnection(location.hostname, location.port, timeout=1)
    payload = {"model": "fixture", "stream": True}
    if target == "responses":
        payload.update(input="hi", max_output_tokens=32)
    elif target == "messages":
        payload.update(messages=[{"role": "user", "content": "hi"}], max_tokens=32)
    else:
        payload.update(messages=[{"role": "user", "content": "hi"}], max_completion_tokens=32)
    path = {"chat": "chat/completions", "messages": "messages", "responses": "responses"}[target]
    connection.request("POST", "/v1/" + path, json.dumps(payload), {"Authorization": "Bearer local", "Content-Type": "application/json"})
    connection.fixture_socket = connection.sock  # HTTP/1.0 transfers ownership to response.fp.
    response = connection.getresponse()
    assert response.status == 200
    return connection, response


def through_seed(response):
    chunks = []
    while True:
        line = response.readline()
        assert line, "stream closed before first text"
        chunks.append(line)
        if b"seed" in line:
            chunks.append(response.readline())
            return b"".join(chunks)


@pytest.mark.parametrize("source,target", [(a.value, b.value) for a in Protocol for b in Protocol if a != b])
def test_translated_stream_stays_readable_during_upstream_silence(monkeypatch, source, target):
    # Test-only interval; production is automatic at 15 seconds after payload.
    monkeypatch.setattr(gateway, "STREAM_HEARTBEAT_INTERVAL", 0.03, raising=False)
    with slow_upstream(source) as (base, release, paused, closed, _):
        config = GatewayConfig(source, base, "synthetic", "local", timeout=1, total_timeout=3, progress_timeout=2, messages_usage_policy="deferred")
        with gateway_session(config) as origin:
            connection, response = connect(origin, target)
            try:
                raw = through_seed(response)
                assert paused.wait(1)
                connection.fixture_socket.settimeout(0.2)
                # No upstream bytes arrive until after this assertion.
                line = response.readline()
                assert line.startswith(b"event: ping" if target == "messages" else b": protoverta keepalive")
                raw += line
                release.set()
                connection.fixture_socket.settimeout(2)
                raw += response.read()
                events = SSEParser().feed(raw)
                assert not any(event.event == "error" for event in events)
                assert closed.wait(1)
            finally:
                release.set()
                response.close()
                connection.close()


@pytest.mark.parametrize("terminal", [False, True])
def test_native_forwarding_adds_no_keepalive(monkeypatch, terminal):
    monkeypatch.setattr(gateway, "STREAM_HEARTBEAT_INTERVAL", 0.02, raising=False)
    with (
        slow_upstream("chat", terminal_before_pause=terminal) as (base, release, paused, _, expected),
        gateway_session(GatewayConfig("chat", base, "synthetic", "local")) as origin,
    ):
        connection, response = connect(origin, "chat")
        try:
            raw = through_seed(response)
            assert paused.wait(1)
            # Release after several potential ticks and compare exact bytes.
            timer = threading.Timer(0.15, release.set)
            timer.start()
            raw += response.read()
            timer.join()
            assert raw == expected
        finally:
            release.set()
            response.close()
            connection.close()


@pytest.mark.parametrize("deadline", ["progress", "total"])
def test_heartbeats_do_not_renew_deadlines_or_produce_completion(monkeypatch, deadline):
    monkeypatch.setattr(gateway, "STREAM_HEARTBEAT_INTERVAL", 0.03, raising=False)
    with slow_upstream("chat") as (base, release, _, _, _):
        config = GatewayConfig("chat", base, "synthetic", "local", timeout=1,
                               total_timeout=0.35 if deadline == "total" else 2,
                               progress_timeout=0.35 if deadline == "progress" else 2)
        with gateway_session(config) as origin:
            connection, response = connect(origin, "responses")
            try:
                started = time.monotonic()
                raw = response.read()
                assert b": protoverta keepalive" in raw
                assert (b"progress_timeout" if deadline == "progress" else b"request_deadline") in raw
                assert b"response.completed" not in raw
                assert time.monotonic() - started < 2
            finally:
                release.set()
                response.close()
                connection.close()


@pytest.mark.parametrize("mode", ["tool", "terminal", "bad_terminal"])
def test_keepalive_during_arguments_and_unverified_eof(monkeypatch, mode):
    monkeypatch.setattr(gateway, "STREAM_HEARTBEAT_INTERVAL", 0.03, raising=False)
    with (
        slow_upstream("chat", tool=mode == "tool", terminal_before_pause=mode != "tool", invalid_tail=mode == "bad_terminal") as (base, release, _, _, _),
        gateway_session(GatewayConfig("chat", base, "synthetic", "local", messages_usage_policy="deferred")) as origin,
    ):
        connection, response = connect(origin, "messages")
        try:
            raw = through_seed(response)
            while b"event: ping" not in raw:
                line = response.readline()
                assert line, "stream ended before a heartbeat"
                raw += line
            assert b"message_stop" not in raw
            release.set()
            raw += response.read()
            if mode == "bad_terminal":
                assert b"event: error" in raw
                assert b"message_stop" not in raw
            else:
                assert b"message_stop" in raw
        finally:
            release.set()
            response.close()
            connection.close()


def test_first_unsupported_block_still_returns_http_422(monkeypatch):
    monkeypatch.setattr(gateway, "STREAM_HEARTBEAT_INTERVAL", 0.02)
    with (
        slow_upstream("chat", unsupported_first=True) as (base, release, _, _, _),
        gateway_session(GatewayConfig("chat", base, "synthetic", "local")) as origin,
    ):
        endpoint = urlsplit(origin)
        connection = HTTPConnection(endpoint.hostname, endpoint.port, timeout=2)
        timer = threading.Timer(0.15, release.set)
        timer.start()
        try:
            connection.request("POST", "/v1/responses", json.dumps({"model": "fixture", "input": "hi", "stream": True}), {"Authorization": "Bearer local"})
            response = connection.getresponse()
            assert response.status == 422
            assert response.getheader("x-should-retry") == "false"
            raw = response.read()
            assert b"unsupported_reasoning" in raw and b"synthetic-private" not in raw
            response.close()
        finally:
            release.set()
            timer.join()
            connection.close()


def test_client_disconnect_during_silence_closes_upstream_and_monitor(monkeypatch):
    monkeypatch.setattr(gateway, "STREAM_HEARTBEAT_INTERVAL", 0.02)
    baseline = {thread.ident for thread in threading.enumerate() if thread.name == "protoverta-sse-heartbeat"}
    with (
        slow_upstream("chat", detect_disconnect=True) as (base, release, _, closed, _),
        gateway_session(GatewayConfig("chat", base, "synthetic", "local")) as origin,
    ):
        connection, response = connect(origin, "responses")
        through_seed(response)
        assert b"keepalive" in response.readline()
        response.close()
        connection.close()
        assert closed.wait(2)
        release.set()
    until = time.monotonic() + 1
    while time.monotonic() < until:
        current = {thread.ident for thread in threading.enumerate() if thread.name == "protoverta-sse-heartbeat"}
        if current <= baseline:
            break
        threading.Event().wait(0.01)
    assert current <= baseline
