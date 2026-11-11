"""Native routes forward upstream-reported stream failures verbatim."""

import io
import json

import pytest
from test_gateway_capabilities import listener, post

from protoverta.core import Protocol
from protoverta.server import GatewayConfig
from protoverta.sse import SSEParser
from protoverta.stream_pipe import StreamValidator
from protoverta.streaming import StreamError


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


MESSAGE_START = sse(
    "message_start",
    {
        "type": "message_start",
        "message": {
            "id": "m",
            "type": "message",
            "role": "assistant",
            "model": "x",
            "content": [],
            "usage": {"input_tokens": 1, "output_tokens": 0},
        },
    },
)
OVERLOADED = sse("error", {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})
BASE = {"id": "r", "object": "response", "created_at": 1, "status": "in_progress", "model": "m", "output": []}
CREATED = sse("response.created", {"type": "response.created", "response": BASE, "sequence_number": 0})
FAILED_RESPONSE = dict(
    BASE,
    status="failed",
    error={"code": "context_length_exceeded", "message": "Your input exceeds the context window."},
)


def _messages_url(url):
    return url.replace("/responses", "/messages")


def _run(config, wire, url_transform, body):
    upstream = lambda request, *, timeout: io.BytesIO(wire)
    seen = []
    with listener(config, upstream, observer=seen.append) as url, post(url_transform(url), body) as response:
        assert response.status == 200
        raw = response.read()
    return SSEParser().feed(raw), seen


@pytest.mark.parametrize("wire", [OVERLOADED, MESSAGE_START + OVERLOADED], ids=["first-event", "after-start"])
def test_native_messages_error_event_reaches_client_unchanged(wire):
    config = GatewayConfig(Protocol.MESSAGES, "https://example.invalid/v1", "fake", "local")
    body = {"model": "x", "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}], "stream": True}
    events, traces = _run(config, wire, _messages_url, body)
    assert events[-1].event == "error"
    assert json.loads(events[-1].data) == {
        "type": "error",
        "error": {"type": "overloaded_error", "message": "Overloaded"},
    }
    assert [event.event for event in events].count("error") == 1
    assert traces[-1].outcome == "upstream_http" and traces[-1].phase == "upstream"


@pytest.mark.parametrize("with_error_event", [False, True])
def test_native_responses_failure_reaches_client_unchanged(with_error_event):
    error = sse(
        "error",
        {
            "type": "error",
            "code": "context_length_exceeded",
            "message": "too long",
            "param": None,
            "sequence_number": 1,
        },
    )
    failed = sse(
        "response.failed",
        {"type": "response.failed", "response": FAILED_RESPONSE, "sequence_number": 2 if with_error_event else 1},
    )
    wire = CREATED + (error if with_error_event else b"") + failed
    config = GatewayConfig(Protocol.RESPONSES, "https://example.invalid/v1", "fake", "local")
    events, traces = _run(config, wire, lambda url: url, {"model": "m", "input": "hi", "stream": True})
    assert [event.event for event in events] == [
        "response.created",
        *(["error"] if with_error_event else []),
        "response.failed",
    ]
    assert json.loads(events[-1].data)["response"]["error"]["code"] == "context_length_exceeded"
    assert traces[-1].outcome == "upstream_http"


def test_native_chat_error_chunk_and_done_marker_reach_client():
    chunk = {
        "id": "c",
        "object": "chat.completion.chunk",
        "error": {"code": 502, "message": "provider failed"},
        "choices": [],
    }
    wire = f"data: {json.dumps(chunk)}\n\n".encode() + b"data: [DONE]\n\n"
    config = GatewayConfig(Protocol.CHAT, "https://example.invalid/v1", "fake", "local")
    events, traces = _run(
        config,
        wire,
        lambda url: url.replace("/responses", "/chat/completions"),
        {"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": True},
    )
    assert json.loads(events[0].data)["error"]["message"] == "provider failed"
    assert events[-1].data == "[DONE]"
    assert traces[-1].outcome == "upstream_http"


@pytest.mark.parametrize(
    "protocol,wire",
    [
        ("messages", MESSAGE_START + OVERLOADED + sse("ping", {"type": "ping"})),
        (
            "responses",
            CREATED + sse("error", {"type": "error", "code": "x", "message": "x", "sequence_number": 1}) + CREATED,
        ),
        ("chat", b'data: {"error": {"message": "x"}, "choices": []}\n\ndata: {"choices": []}\n\n'),
    ],
)
def test_events_after_an_upstream_error_are_rejected(protocol, wire):
    validator = StreamValidator(protocol)
    with pytest.raises(StreamError):
        validator.feed(wire)
        validator.finish()


@pytest.mark.parametrize("protocol", ["messages", "chat"])
def test_error_without_an_error_object_is_still_invalid(protocol):
    wire = sse("error", {"type": "error"}) if protocol == "messages" else b'data: {"error": "plain", "choices": []}\n\n'
    with pytest.raises(StreamError):
        StreamValidator(protocol).feed(wire)


def test_responses_failed_before_created_is_rejected():
    wire = sse("response.failed", {"type": "response.failed", "response": FAILED_RESPONSE, "sequence_number": 0})
    with pytest.raises(StreamError):
        StreamValidator("responses").feed(wire)


def test_stream_ending_after_error_event_without_epilogue_finishes():
    validator = StreamValidator("responses")
    validator.feed(CREATED + sse("error", {"type": "error", "code": "x", "message": "x", "sequence_number": 1}))
    validator.finish()


def test_default_stream_budgets_are_not_stricter_than_codex_and_claude_code():
    config = GatewayConfig(Protocol.RESPONSES, "https://example.invalid/v1", "fake", "local")
    # Both clients use a five-minute streaming idle timeout and no total cap.
    assert config.progress_timeout >= 300 and config.timeout >= 300
    assert config.total_timeout >= 3600
