"""Loopback-only slow client/framing tests; no credentials or provider calls."""

import io
import socket
import threading
import time
from contextlib import contextmanager

import pytest

from protoverta.server import GatewayConfig, GatewayHTTPServer, make_handler


@contextmanager
def boundary_server(**options):
    options = {"timeout": 0.5, "total_timeout": 0.2, "progress_timeout": 0.5, **options}
    config = GatewayConfig("chat", "https://example.invalid/v1", "fixture", "local", **options)
    calls = []

    def upstream(request, timeout):
        calls.append(request)
        return io.BytesIO(b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}')

    server = GatewayHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=upstream))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_trickling_body_expires_before_upstream_call():
    body = b'{"model":"m","messages":[{"role":"user","content":"synthetic"}]}'
    with boundary_server() as (address, calls), socket.create_connection(address, timeout=2) as client:
        client.sendall(b"POST /v1/chat/completions HTTP/1.0\r\nAuthorization: Bearer local\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n")
        started = time.monotonic()
        for chunk in (body[:15], body[15:30], body[30:45]):
            client.sendall(chunk)
            time.sleep(0.085)
        response = client.recv(4096)
        assert b"408" in response.split(b"\r\n", 1)[0]
        assert time.monotonic() - started < 1
        assert not calls


def test_header_deadline_and_connection_capacity_apply_before_authentication():
    # The stalled first connection must hold its slot (header deadline 2 s)
    # longer than the 0.5 s grace a waiting connection gets before its 503.
    with (
        boundary_server(max_concurrent_requests=1, timeout=2.0, total_timeout=2.0, progress_timeout=2.0) as (address, calls),
        socket.create_connection(address, timeout=5) as first,
    ):
        first.sendall(b"POST /v1/chat/completions HTTP/1.0\r\nX-Fixture:")
        # Wait for the accepted connection to own the sole handler slot.
        time.sleep(0.04)
        with socket.create_connection(address, timeout=2) as second:
            second.sendall(b"GET / HTTP/1.0\r\n\r\n")
            # Over capacity: an immediate retryable 503, never a silent reset.
            response = second.recv(4096)
            assert response.startswith(b"HTTP/1.1 503") and b"Retry-After: 1" in response
        assert first.recv(4096) == b""
        assert not calls
        with socket.create_connection(address, timeout=2) as third:
            third.sendall(b"POST /v1/chat/completions HTTP/1.0\r\nContent-Length: 0\r\n\r\n")
            assert b"401" in third.recv(4096).split(b"\r\n", 1)[0]


@pytest.mark.parametrize("framing", [b"Content-Length: 2\r\nContent-Length: 2", b"Content-Length: 2\r\nTransfer-Encoding: chunked"])
def test_ambiguous_framing_rejected_without_upstream(framing):
    with boundary_server() as (address, calls), socket.create_connection(address, timeout=2) as client:
        client.sendall(b"POST /v1/chat/completions HTTP/1.0\r\nAuthorization: Bearer local\r\n" + framing + b"\r\n\r\n{}")
        assert b"400" in client.recv(4096).split(b"\r\n", 1)[0]
        assert not calls


def test_non_ascii_bearer_is_unauthorized_and_listener_recovers():
    with boundary_server() as (address, calls):
        with socket.create_connection(address, timeout=2) as client:
            client.sendall(b"POST /v1/responses HTTP/1.0\r\nAuthorization: Bearer \xe9\r\nContent-Length: 0\r\n\r\n")
            assert b"401" in client.recv(4096).split(b"\r\n", 1)[0]
        with socket.create_connection(address, timeout=2) as client:
            body = b'{"model":"m","input":"synthetic"}'
            client.sendall(b"POST /v1/responses HTTP/1.0\r\nAuthorization: Bearer local\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            assert b"200" in client.recv(4096).split(b"\r\n", 1)[0]
        assert len(calls) == 1


def test_non_ascii_configured_inbound_token_is_rejected_early():
    with pytest.raises(ValueError, match="ASCII"):
        GatewayConfig("chat", "https://example.invalid/v1", "fixture", "é")


def test_connection_freed_within_the_grace_window_is_served_not_rejected():
    with boundary_server(max_concurrent_requests=1) as (address, calls), socket.create_connection(address, timeout=2) as first:
        first.sendall(b"POST /v1/chat/completions HTTP/1.0\r\nX-Fixture:")
        time.sleep(0.04)  # The first connection owns the sole slot.
        with socket.create_connection(address, timeout=2) as second:
            second.sendall(b"POST /v1/chat/completions HTTP/1.0\r\nContent-Length: 0\r\n\r\n")
            time.sleep(0.05)
            first.close()  # Frees the slot while the second waits for it.
            assert b"401" in second.recv(4096).split(b"\r\n", 1)[0]
        assert not calls
