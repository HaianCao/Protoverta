"""Connection-level admission: overflow gets 503, idle header readers are bounded."""

import socket
import time

from protoverta import server
from protoverta.server import GatewayConfig, gateway_session

REQUEST = b"GET /v1/models HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer t\r\n\r\n"


def _config():
    return GatewayConfig("chat", "http://127.0.0.1:9/v1", "k", "t", max_concurrent_requests=2)


def _first_line(sock):
    sock.settimeout(5)
    return sock.recv(200).split(b"\r\n")[0]


def test_connection_over_the_limit_receives_503_with_retry_after(monkeypatch):
    monkeypatch.setattr(server, "REQUEST_HEADER_TIMEOUT", 30.0)
    with gateway_session(_config()) as url:
        port = int(url.rsplit(":", 1)[1])
        idle = [socket.create_connection(("127.0.0.1", port)) for _ in range(2)]
        try:
            time.sleep(0.3)
            with socket.create_connection(("127.0.0.1", port)) as extra:
                extra.sendall(REQUEST)
                extra.settimeout(5)
                raw = extra.recv(500)
            assert raw.split(b"\r\n")[0] == b"HTTP/1.1 503 Service Unavailable"
            assert b"Retry-After: 1" in raw
        finally:
            for sock in idle:
                sock.close()


def test_silent_connections_release_their_slot_after_the_header_deadline(monkeypatch):
    monkeypatch.setattr(server, "REQUEST_HEADER_TIMEOUT", 0.5)
    with gateway_session(_config()) as url:
        port = int(url.rsplit(":", 1)[1])
        idle = [socket.create_connection(("127.0.0.1", port)) for _ in range(2)]
        try:
            time.sleep(1.5)
            with socket.create_connection(("127.0.0.1", port)) as client:
                client.sendall(REQUEST)
                # Served normally (no model snapshot configured -> 404), not 503.
                assert _first_line(client).endswith(b"404 Not Found")
        finally:
            for sock in idle:
                sock.close()


def test_header_deadline_does_not_limit_a_slow_request_body(monkeypatch):
    monkeypatch.setattr(server, "REQUEST_HEADER_TIMEOUT", 0.5)
    body = b'{"model": "m", "messages": [{"role": "user", "content": "hi"}]}'
    head = b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer t\r\nContent-Type: application/json\r\n"
    with gateway_session(_config()) as url:
        port = int(url.rsplit(":", 1)[1])
        with socket.create_connection(("127.0.0.1", port)) as client:
            client.sendall(head + b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body[:10])
            time.sleep(1.0)  # longer than the header deadline, within the I/O budget
            client.sendall(body[10:])
            # The body was read; the unreachable upstream then fails as 502, not 408.
            assert _first_line(client).endswith(b"502 Bad Gateway")
