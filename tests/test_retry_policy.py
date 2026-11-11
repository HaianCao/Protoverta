"""Error classes and the gateway's error-class retry policy; loopback only."""

import io
import json
import socket
import threading
from contextlib import contextmanager
from email.message import Message
from urllib.error import HTTPError, URLError

import pytest

from protoverta.core import Protocol
from protoverta.error_classes import DEFAULT_RETRY_CLASSES, RetryPolicy, classify
from protoverta.server import GatewayConfig, GatewayHTTPServer, make_handler

# Messages seen from real providers (2026-11-07 .. 2026-11-10 live checks).
AZURE = {
    "error": {
        "message": "The requested item was created under a different Azure OpenAI resource.",
        "type": "invalid_request_error",
    }
}
AGENTROUTER_NO_CHANNEL = {
    "error": {
        "message": "当前分组 default 下对于模型 no-such-model 无可用渠道 (request id: x)",
        "type": "new_api_error",
    }
}
OPENROUTER_BAD_MODEL = {
    "error": {"message": "nonexistent-vendor/no-such-model-xyz is not a valid model ID", "code": 400}
}
AGENTROUTER_CLIENT = {"error": {"message": "unauthorized client detected, contact support", "type": "new_api_error"}}
NIM_REQUIRED = {"error": {"message": "model field is required", "type": "invalid_request_error"}}
OPENROUTER_MESSAGES_400 = {
    "type": "error",
    "error": {"type": "invalid_request_error", "message": "Invalid Anthropic Messages API request"},
}


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (400, AZURE, "backend_mismatch"),
        (503, AGENTROUTER_NO_CHANNEL, "overloaded"),
        (400, OPENROUTER_BAD_MODEL, "model_not_found"),
        (401, AGENTROUTER_CLIENT, "auth"),
        (403, {"error": {"message": "Your input was flagged by the safety system"}}, "content_policy"),
        (400, NIM_REQUIRED, "invalid_request"),
        (400, OPENROUTER_MESSAGES_400, "invalid_request"),
        (429, {"error": {"message": "too many tokens per minute"}}, "rate_limit"),
        (402, {"error": {"message": "Payment required"}}, "billing"),
        (400, {"error": {"message": "insufficient credits for this request"}}, "billing"),
        (400, {"error": {"message": "This model's maximum context length is 128000 tokens"}}, "context_length"),
        (404, {"error": {"message": "Not found"}}, "model_not_found"),
        (500, {"type": "error", "error": {"type": "api_error", "message": "Internal server error"}}, "server"),
        (529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}, "overloaded"),
        (418, b"<html>teapot</html>", "unknown"),
        (400, {"error": {"type": "api_error", "message": "upstream said no"}}, "unknown"),
    ],
)
def test_real_provider_errors_are_classified(status, body, expected):
    raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
    assert classify(status, raw) == expected


def test_stream_and_transport_failures():
    assert classify(None, transport=True) == "network"
    assert classify(None, None, stream_before_output=True) == "stream_interrupted"
    failed = {
        "type": "response.failed",
        "response": {"status": "failed", "error": {"code": "server_error", "message": "backend busy"}},
    }
    assert classify(None, json.dumps(failed), stream_before_output=True) == "server"


def test_policy_from_config_table():
    assert RetryPolicy.from_mapping({}).retry_classes == DEFAULT_RETRY_CLASSES
    policy = RetryPolicy.from_mapping(
        {
            "max_retries": 3,
            "delay_seconds": 5,
            "retry_classes": ["auth"],
            "no_retry_classes": ["unknown"],
            "skip_status": [418],
        }
    )
    assert policy.max_retries == 3 and policy.delay_seconds == 5
    assert "auth" in policy.retry_classes and "unknown" not in policy.retry_classes
    assert not policy.retryable("server", 418)
    for bad in (
        {"max_retries": -1},
        {"delay_seconds": 4000},
        {"retry_classes": ["nope"]},
        {"enabled": 1},
        {"surprise": 1},
        {"skip_status": "429"},
    ):
        with pytest.raises(ValueError):
            RetryPolicy.from_mapping(bad)


def test_retry_hints_and_delay():
    policy = RetryPolicy(delay_seconds=60, jitter=0.2, random=lambda: 1.0)
    assert not policy.retryable("server", 500, {"x-should-retry": "false"})
    assert policy.retryable("auth", 401, {"x-should-retry": "true"})
    assert not policy.retryable("auth", 401)
    assert policy.delay() == pytest.approx(72)
    assert policy.delay(retry_after=120) == 120
    assert not RetryPolicy(enabled=False).retryable("server", 500)


# --- Gateway integration -------------------------------------------------

CHAT_OK = b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}'


def http_error(status, body, headers=None):
    message = Message()
    for name, value in (headers or {"Content-Type": "application/json"}).items():
        message[name] = value
    raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
    return HTTPError("https://example.invalid/v1", status, "error", message, io.BytesIO(raw))


class Upstream:
    """Scripted upstream: each call returns or raises the next reply (the last repeats)."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    def __call__(self, request, *, timeout):
        self.calls += 1
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        reply = reply() if callable(reply) else reply
        if isinstance(reply, BaseException):
            raise reply
        return io.BytesIO(reply)


@contextmanager
def gateway(upstream, protocol=Protocol.CHAT, logs=None, **policy):
    config = GatewayConfig(
        protocol,
        "https://example.invalid/v1",
        "upstream-key",
        "local",
        retry_policy=RetryPolicy(**{"delay_seconds": 0, "jitter": 0, **policy}),
        retry_log=logs.append if logs is not None else None,
    )
    server = GatewayHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=upstream))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def call(address, path, body):
    raw = json.dumps(body).encode()
    with socket.create_connection(address, timeout=10) as client:
        client.sendall(
            f"POST {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer local\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\nConnection: close\r\n\r\n".encode()
            + raw
        )
        chunks = []
        while chunk := client.recv(65536):
            chunks.append(chunk)
    head, _, payload = b"".join(chunks).partition(b"\r\n\r\n")
    head = head.decode("latin-1")
    return int(head.split()[1]), head.lower(), payload


CHAT_REQUEST = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}


def test_retryable_http_error_is_resent_until_success():
    logs = []
    upstream = Upstream(lambda: http_error(400, AZURE), lambda: http_error(503, AGENTROUTER_NO_CHANNEL), CHAT_OK)
    with gateway(upstream, logs=logs) as address:
        status, _, payload = call(address, "/v1/chat/completions", CHAT_REQUEST)
    assert status == 200 and json.loads(payload)["choices"][0]["message"]["content"] == "ok"
    assert upstream.calls == 3
    assert any("backend_mismatch (HTTP 400)" in line and "retry 1/10" in line for line in logs)
    assert any("succeeded after 2 retries" in line for line in logs)


def test_upstream_retry_hint_overrides_the_class():
    # An explicit upstream ``x-should-retry: true`` makes even a 401 retryable.
    upstream = Upstream(
        lambda: http_error(401, AGENTROUTER_CLIENT, {"Content-Type": "application/json", "x-should-retry": "true"}),
        CHAT_OK,
    )
    with gateway(upstream) as address:
        status, _, _ = call(address, "/v1/chat/completions", CHAT_REQUEST)
    assert (status, upstream.calls) == (200, 2)


def test_deterministic_errors_are_not_retried_and_marked_final():
    upstream = Upstream(lambda: http_error(400, OPENROUTER_BAD_MODEL), CHAT_OK)
    with gateway(upstream) as address:
        status, head, payload = call(address, "/v1/chat/completions", CHAT_REQUEST)
    assert (status, upstream.calls) == (400, 1)
    assert "x-should-retry: false" in head
    assert "not a valid model id" in payload.decode().lower()


def test_exhausted_retries_return_the_last_error_and_open_a_give_up_window():
    logs = []
    upstream = Upstream(lambda: http_error(503, AGENTROUTER_NO_CHANNEL))
    with gateway(upstream, logs=logs, max_retries=2) as address:
        status, head, _ = call(address, "/v1/chat/completions", CHAT_REQUEST)
        assert (status, upstream.calls) == (503, 3)
        assert "x-should-retry: false" in head
        # The CLI resends at once: answered without another retry cycle.
        status, _, _ = call(address, "/v1/chat/completions", CHAT_REQUEST)
        assert (status, upstream.calls) == (503, 4)
    assert any("gave up after 2 retries" in line for line in logs)
    assert any("passed through" in line for line in logs)


def test_network_failure_is_retried():
    upstream = Upstream(URLError(ConnectionRefusedError("refused")), CHAT_OK)
    with gateway(upstream) as address:
        status, _, _ = call(address, "/v1/chat/completions", CHAT_REQUEST)
    assert (status, upstream.calls) == (200, 2)


def sse(*events):
    return b"".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events)


CREATED = {
    "type": "response.created",
    "response": {"id": "r", "object": "response", "status": "in_progress", "model": "m", "output": []},
}
FAILED = {
    "type": "response.failed",
    "response": {"id": "r", "status": "failed", "error": {"code": "server_error", "message": "backend busy"}},
}


def test_stream_failing_before_output_is_retried_on_a_native_route(monkeypatch):
    from protoverta import server

    good = sse(
        CREATED,
        {"type": "response.output_text.delta", "item_id": "m", "output_index": 0, "content_index": 0, "delta": "PONG"},
    )
    upstream = Upstream(sse(CREATED, FAILED), b"", good)
    # The native validator would reject this deliberately short stream; only
    # the retry decision is under test, so let it pass bytes through.
    monkeypatch.setattr(server, "StreamValidator", _PassThrough)
    with gateway(upstream, Protocol.RESPONSES) as address:
        status, _, payload = call(address, "/v1/responses", {"model": "m", "input": "hi", "stream": True})
    assert status == 200 and upstream.calls == 3
    assert b"PONG" in payload and b"backend busy" not in payload


class _PassThrough:
    def __init__(self, *args, **kwargs):
        from protoverta.resources import StreamLimits

        self.limits = StreamLimits()
        self.has_payload = True
        self.decoder = type("Decoder", (), {"sequence": 0})()

    def feed(self, chunk):
        self.limits.progress += 1
        return chunk

    def finish(self):
        return b""
