import json
import socket
import threading
import time
from contextlib import contextmanager, nullcontext
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
from test_stream_heartbeat import through_seed, wire

from protoverta import server as gateway
from protoverta.resources import (
    ClientDisconnected,
    DeadlineExceeded,
    DeadlineInput,
    RequestBudget,
)
from protoverta.server import GatewayConfig, gateway_session


@contextmanager
def waiting_upstream():
    entered, disconnected = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(wire("chat")[0])
            self.wfile.flush()
            entered.set()
            self.connection.settimeout(4)
            try:
                if not self.connection.recv(1):
                    disconnected.set()
            except ConnectionError:
                disconnected.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", entered, disconnected
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.mark.parametrize("interrupt", [False, True])
def test_session_exit_cancels_open_upstream_and_unauthenticated_partial_client(interrupt, monkeypatch):
    servers = []
    original = gateway.serve

    def capture(*args, **kwargs):
        server = original(*args, **kwargs)
        servers.append(server)
        return server

    monkeypatch.setattr(gateway, "serve", capture)
    monitors = {"protoverta-gateway", "protoverta-client-lifetime", "protoverta-sse-heartbeat"}
    baseline = {(thread.name, thread.ident) for thread in threading.enumerate() if thread.name in monitors}
    connection, response, partial = None, None, None
    with waiting_upstream() as (base, entered, disconnected):
        try:
            with (
                pytest.raises(KeyboardInterrupt) if interrupt else nullcontext(),
                gateway_session(GatewayConfig("chat", base, "fake", "local", timeout=15)) as origin,
            ):
                location = urlsplit(origin)
                connection = HTTPConnection(location.hostname, location.port, timeout=2)
                connection.request(
                    "POST",
                    "/v1/responses",
                    json.dumps({"model": "fixture", "input": "hi", "stream": True}),
                    {"Authorization": "Bearer local"},
                )
                response = connection.getresponse()
                through_seed(response)
                assert entered.wait(1)
                partial = socket.create_connection((location.hostname, location.port), timeout=2)
                partial.sendall(b"POST /v1/responses HTTP/1.1\r\nHost: localhost\r\n")
                until = time.monotonic() + 1
                while len(servers[0]._request_budgets) != 2 and time.monotonic() < until:
                    threading.Event().wait(0.01)
                assert len(servers[0]._request_budgets) == 2
                if interrupt:
                    raise KeyboardInterrupt
            assert disconnected.wait(0.7), "Session exit left its upstream inference alive"
            try:
                assert partial.recv(1) == b""
            except ConnectionResetError:
                pass  # Listener shutdown may reject an unaccepted connection.
            with pytest.raises(OSError):
                socket.create_connection((location.hostname, location.port), timeout=0.3)
            until = time.monotonic() + 1
            while time.monotonic() < until and any(
                thread.name in monitors and (thread.name, thread.ident) not in baseline
                for thread in threading.enumerate()
            ):
                threading.Event().wait(0.01)
            assert not any(
                thread.name in monitors and (thread.name, thread.ident) not in baseline
                for thread in threading.enumerate()
            )
            assert not servers[0]._active_clients and not servers[0]._request_budgets
        finally:
            if response:
                response.close()
            if connection:
                connection.close()
            if partial:
                partial.close()


@pytest.mark.parametrize("cancelled", [False, True])
def test_read_classifies_cancelled_socket_error_without_hiding_real_transport_failure(cancelled):
    budget = RequestBudget(1, 2, 1)

    class Response:
        def read1(self, size):
            if cancelled:
                budget.cancel()
            raise OSError("synthetic transport failure")

    with pytest.raises(ClientDisconnected if cancelled else OSError) as error:
        budget.read(Response(), 1, streaming=True)
    if cancelled:
        assert "synthetic transport" not in str(error.value)
    else:
        assert type(error.value) is OSError


def test_registering_budget_after_shutdown_cancels_it_immediately():
    server = gateway.serve(GatewayConfig("chat", "http://127.0.0.1:1/v1", "fake", "local"))
    server.server_close()
    budget = RequestBudget(1, 2, 1)
    with socket.socket() as client:
        server.register_request_budget(client, budget)
        with pytest.raises(ClientDisconnected):
            budget.remaining()
        assert not server._request_budgets


def test_worker_start_failure_does_not_retain_connection_slot(monkeypatch):
    server = gateway.serve(GatewayConfig("chat", "http://127.0.0.1:1/v1", "fake", "local", max_concurrent_requests=1))

    def failed_start(_):
        raise RuntimeError("synthetic thread failure")

    monkeypatch.setattr(threading.Thread, "start", failed_start)
    try:
        client, peer = socket.socketpair()
        with client, peer, pytest.raises(RuntimeError, match="synthetic thread"):
            server.process_request(client, ("127.0.0.1", 0))
        assert not server._active_clients
        assert server._connections.acquire(blocking=False)
        server._connections.release()
    finally:
        server.server_close()


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_gateway_thread_start_failure_closes_listener_before_yield(monkeypatch, error):
    servers = []
    original = gateway.serve

    def capture(*args, **kwargs):
        server = original(*args, **kwargs)
        servers.append(server)
        return server

    def failed_start(_):
        raise error("synthetic gateway start failure")

    monkeypatch.setattr(gateway, "serve", capture)
    monkeypatch.setattr(threading.Thread, "start", failed_start)
    with pytest.raises(error), gateway_session(GatewayConfig("chat", "http://127.0.0.1:1/v1", "fake", "local")):
        pytest.fail("Failed start must not yield an open gateway")
    assert servers[0].socket.fileno() == -1


def test_inbound_cancellation_poll_does_not_reset_original_io_timeout():
    budget = RequestBudget(0.15, 3, 2)
    progress = budget.last_progress
    client, peer = socket.socketpair()
    with client, peer, DeadlineInput(client.makefile("rb", buffering=0), client, budget) as reader:
        started = time.monotonic()
        with pytest.raises(TimeoutError) as error:
            reader.readinto(bytearray(16))
        assert not isinstance(error.value, DeadlineExceeded)
        assert 0.1 <= time.monotonic() - started < 1
        assert budget.last_progress == progress and not budget._cancelled.is_set()


def test_inbound_poll_observes_budget_cancellation_without_waiting_for_peer():
    budget = RequestBudget(15, 30, 20)
    client, peer = socket.socketpair()
    with client, peer, DeadlineInput(client.makefile("rb", buffering=0), client, budget) as reader:
        timer = threading.Timer(0.03, budget.cancel)
        timer.start()
        try:
            started = time.monotonic()
            with pytest.raises(ClientDisconnected):
                reader.readinto(bytearray(16))
            assert time.monotonic() - started < 1
        finally:
            timer.join()
