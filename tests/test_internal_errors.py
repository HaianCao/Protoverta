"""Unexpected gateway faults are answered, never reset or printed to the terminal."""

import io
import json
import socket
import threading
from contextlib import contextmanager

import pytest

from protoverta.core import Protocol
from protoverta.server import GatewayConfig, GatewayHTTPServer, make_handler

CHAT_OK = b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}'


@contextmanager
def gateway(upstream_open, protocol=Protocol.CHAT):
    config = GatewayConfig(protocol, "https://example.invalid/v1", "upstream-key", "local")
    server = GatewayHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=upstream_open))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def exchange(address, raw):
    with socket.create_connection(address, timeout=5) as client:
        client.sendall(raw)
        chunks = []
        while chunk := client.recv(65536):
            chunks.append(chunk)
    head, _, body = b"".join(chunks).partition(b"\r\n\r\n")
    return head.decode("latin-1"), body


def post(path, body, token="local"):
    return (
        f"POST {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {token}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )


def test_unexpected_fault_is_a_structured_500_without_traceback(capsys):
    calls = []

    def broken(request, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("simulated defect mentioning upstream-key")
        return io.BytesIO(CHAT_OK)

    body = b'{"model":"m","messages":[{"role":"user","content":"hi"}]}'
    with gateway(broken) as address:
        head, raw = exchange(address, post("/v1/chat/completions", body))
        assert head.startswith(("HTTP/1.0 500", "HTTP/1.1 500"))
        assert "x-should-retry: false" in head.lower()
        error = json.loads(raw)["error"]
        assert error["code"] == "internal_error"
        assert "upstream-key" not in raw.decode() and "simulated" not in raw.decode()
        # The gateway keeps serving after the fault.
        head, raw = exchange(address, post("/v1/chat/completions", body))
        assert " 200 " in head.split("\r\n", 1)[0] + " "
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err and "simulated" not in captured.err


def test_messages_clients_get_the_messages_error_envelope():
    def broken(request, timeout):
        raise RuntimeError("defect")

    body = b'{"model":"m","max_tokens":5,"messages":[{"role":"user","content":"hi"}]}'
    with gateway(broken, Protocol.MESSAGES) as address:
        _, raw = exchange(address, post("/v1/messages", body))
    payload = json.loads(raw)
    assert payload["type"] == "error" and payload["error"]["code"] == "internal_error"


@pytest.mark.parametrize("target", ["//[bad/v1/chat/completions", "/[bad"])
def test_malformed_request_target_is_rejected(target):
    with gateway(lambda request, timeout: io.BytesIO(CHAT_OK)) as address:
        head, _ = exchange(address, post(target, b"{}"))
    status = head.split("\r\n", 1)[0]
    assert " 400 " in status + " " or " 404 " in status + " "
