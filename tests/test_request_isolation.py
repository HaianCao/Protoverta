"""Inference-lane isolation with real sockets; these are not harness agents."""

import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from protoverta.core import Protocol
from protoverta.server import GatewayConfig, gateway_session
from protoverta.sse import encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta


def request_body(target, model):
    body = {"model": model, "stream": True}
    if target == Protocol.RESPONSES:
        body.update(input="synthetic", max_output_tokens=32)
    else:
        body.update(messages=[{"role": "user", "content": "synthetic"}], max_tokens=32)
    return body


def connect(origin, target, model, token="local"):
    endpoint = urlsplit(origin)
    connection = HTTPConnection(endpoint.hostname, endpoint.port, timeout=4)
    path = {Protocol.CHAT: "chat/completions", Protocol.MESSAGES: "messages", Protocol.RESPONSES: "responses"}[target]
    connection.request("POST", "/v1/" + path, json.dumps(request_body(target, model)), {"Authorization": "Bearer " + token})
    peer = connection.sock
    response = connection.getresponse()
    return connection, response, peer


def collect(origin, target, model, token="local"):
    connection, response, _ = connect(origin, target, model, token)
    try:
        return response.status, response.read()
    finally:
        response.close()
        connection.close()


def assert_complete(raw, target):
    terminal = {Protocol.CHAT: b"[DONE]", Protocol.MESSAGES: b"message_stop", Protocol.RESPONSES: b"response.completed"}[target]
    assert terminal in raw and b"event: error" not in raw


def through_marker(response):
    while True:
        line = response.readline()
        assert line, "stream ended before its lane marker"
        if b"_SENTINEL" in line:
            return


@contextmanager
def upstream(source, *, failure=None, parties=3):
    barrier = threading.Barrier(parties)
    release, cancelled = threading.Event(), threading.Event()
    records, active, peak = [], set(), [0]
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            model = body["model"]
            with lock:
                records.append((model, self.headers.get("Authorization"), self.path))
                active.add(model)
                peak[0] = max(peak[0], len(active))
            try:
                if model in {"a", "b", "parent"}:
                    barrier.wait(timeout=2)
                if model == "a" and failure == "429":
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":{"message":"synthetic rate limit"}}')
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                encoder = StreamEncoder(source)

                def emit(deltas):
                    self.wfile.write(b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta)))
                    self.wfile.flush()

                emit([Delta("start", model=model, input_tokens=1, output_tokens=0),
                      Delta("text", text="ROLE_" + model + "_SENTINEL"),
                      Delta("tool_start", index=0, call_id="same-call", name="work"),
                      Delta("tool_delta", index=0, text='{"lane":"')])
                if model == "a" and failure in {"timeout", "cancel"}:
                    self.connection.settimeout(3)
                    if not self.connection.recv(1):
                        cancelled.set()
                    return
                if model == "a" and failure == "invalid":
                    return  # Missing verified terminal must never become success.
                if failure == "cancel" and model in {"b", "parent"}:
                    assert release.wait(3)
                emit([Delta("tool_delta", index=0, text=model + '"}'), Delta("tool_end", index=0),
                      Delta("finish", reason="tool_calls", input_tokens=1, output_tokens=1)])
            except (OSError, threading.BrokenBarrierError):
                if model == "a" and failure in {"cancel", "timeout"}:
                    cancelled.set()
            finally:
                with lock:
                    active.discard(model)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", release, cancelled, records, active, peak
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.mark.parametrize("source,target", [(source, target) for source in Protocol for target in Protocol if source != target])
def test_three_overlapping_streams_can_reuse_call_ids_without_sharing_state(source, target):
    with upstream(source) as (base, _, _, records, active, peak), gateway_session(
        GatewayConfig(source, base, "synthetic", "local", messages_usage_policy="deferred")
    ) as origin, ThreadPoolExecutor(3) as pool:
        results = list(pool.map(lambda model: collect(origin, target, model), ["a", "b", "parent"]))
        for model, (status, raw) in zip(["a", "b", "parent"], results, strict=True):
            assert status == 200
            assert_complete(raw, target)
            assert b"same-call" in raw and ("ROLE_" + model + "_SENTINEL").encode() in raw
            assert all(("ROLE_" + other + "_SENTINEL").encode() not in raw for other in {"a", "b", "parent"} - {model})
        assert peak[0] == 3 and len(records) == 3 and not active


@pytest.mark.parametrize("failure", ["429", "invalid", "timeout"])
@pytest.mark.parametrize("target", [Protocol.RESPONSES, Protocol.MESSAGES])
def test_failed_child_lane_does_not_poison_sibling_or_refund_shared_request_quota(tmp_path, failure, target):
    with upstream(Protocol.CHAT, failure=failure) as (base, _, _, records, _, _), gateway_session(
        GatewayConfig("chat", base, "synthetic", "local", messages_usage_policy="deferred", progress_timeout=0.5,
                      shared_admission_db=str(tmp_path / "quota.sqlite"), shared_requests=4, shared_concurrency=3)
    ) as origin, ThreadPoolExecutor(3) as pool:
        results = list(pool.map(lambda model: collect(origin, target, model), ["a", "b", "parent"]))
        for status, raw in results[1:]:
            assert status == 200
            assert_complete(raw, target)
        status, raw = results[0]
        assert status == 429 if failure == "429" else status in {200, 504, 502}
        assert b"response.completed" not in raw and b"message_stop" not in raw
        assert len(records) == 3  # No hidden retry/replay of the failed generation.
        assert collect(origin, target, "probe")[0] == 200  # Concurrency leases released.
        assert collect(origin, target, "over-quota")[0] == 429
        assert len(records) == 4  # Failed work was not refunded from rate quota.


def test_cancel_one_lane_releases_its_lease_while_siblings_remain_open(tmp_path):
    observations = []
    with upstream(Protocol.CHAT, failure="cancel") as (base, release, cancelled, records, active, _), gateway_session(
        GatewayConfig("chat", base, "synthetic", "local", shared_admission_db=str(tmp_path / "quota.sqlite"),
                      shared_requests=4, shared_concurrency=3),
        observer=observations.append,
    ) as origin, ThreadPoolExecutor(3) as pool:
        clients = list(pool.map(lambda model: connect(origin, Protocol.RESPONSES, model), ["a", "b", "parent"]))
        try:
            for _, response, _ in clients:
                through_marker(response)
            connection, response, peer = clients[0]
            peer.shutdown(socket.SHUT_RDWR)
            response.close()
            connection.close()
            assert cancelled.wait(1)
            until = time.monotonic() + 1
            while not any(item.outcome == "client_disconnected" for item in observations) and time.monotonic() < until:
                threading.Event().wait(0.01)
            assert any(item.outcome == "client_disconnected" for item in observations), [item.outcome for item in observations]
            assert {"b", "parent"} <= active
            assert collect(origin, Protocol.RESPONSES, "probe")[0] == 200
            release.set()
            for _, response, _ in clients[1:]:
                assert_complete(response.read(), Protocol.RESPONSES)
            assert collect(origin, Protocol.RESPONSES, "over-quota")[0] == 429
            assert len(records) == 4
        finally:
            release.set()
            for connection, response, _ in clients:
                response.close()
                connection.close()


def test_closing_one_session_does_not_close_another_sessions_upstream_or_mix_credentials(tmp_path):
    observations_a, observations_b = [], []
    clients = []
    with upstream(Protocol.CHAT, failure="cancel", parties=2) as (base, release, cancelled, records, active, _):
        shared = {"shared_admission_db": str(tmp_path / "quota.sqlite"), "shared_requests": 2, "shared_concurrency": 1}
        with gateway_session(GatewayConfig("chat", base, "synthetic-B", "local-B", **shared), observer=observations_b.append) as origin_b:
            try:
                with gateway_session(GatewayConfig("chat", base, "synthetic-A", "local-A", **shared), observer=observations_a.append) as origin_a, ThreadPoolExecutor(2) as pool:
                    tasks = [pool.submit(connect, origin_a, Protocol.RESPONSES, "a", "local-A"),
                             pool.submit(connect, origin_b, Protocol.RESPONSES, "b", "local-B")]
                    clients = [task.result(timeout=4) for task in tasks]
                    for _, response, _ in clients:
                        through_marker(response)
                    assert collect(origin_b, Protocol.RESPONSES, "wrong-token", "local-A")[0] == 401
                assert cancelled.wait(1)
                assert "b" in active
                release.set()
                assert_complete(clients[1][1].read(), Protocol.RESPONSES)
                assert {(model, auth) for model, auth, _ in records} == {("a", "Bearer synthetic-A"), ("b", "Bearer synthetic-B")}
            finally:
                release.set()
                for connection, response, _ in clients:
                    response.close()
                    connection.close()
        assert [item.outcome for item in observations_a] == ["client_disconnected"]
        assert any(item.outcome == "success" for item in observations_b)
        assert not ({item.request_id for item in observations_a} & {item.request_id for item in observations_b})
