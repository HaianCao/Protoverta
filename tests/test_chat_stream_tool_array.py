import io
import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from protoverta.core import convert_request
from protoverta.server import GatewayConfig, make_handler
from protoverta.sse import SSEEvent, SSEParser, encode_sse
from protoverta.streaming import StreamDecoder, StreamError

INVALID = [True, False, 1, 0, 0.5, "", "fixture", {}, {"index": 0}]


def chunk(delta, reason=None):
    return json.dumps(
        {
            "object": "chat.completion.chunk",
            "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
        }
    )


@pytest.mark.parametrize("value", INVALID)
def test_non_array_tool_calls_raises_declared_stream_error(value):
    decoder = StreamDecoder("chat")
    with pytest.raises(StreamError, match="tool_calls must be an array or null"):
        decoder.feed(SSEEvent("message", chunk({"tool_calls": value})))


@pytest.mark.parametrize("delta", [{}, {"tool_calls": None}, {"tool_calls": []}])
def test_optional_tool_array_preserves_later_fragmented_function_call(delta):
    decoder = StreamDecoder("chat")
    frames = [
        chunk({"role": "assistant", **delta}),
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "opaque:/+=",
                        "type": "function",
                        "function": {"name": "fixture", "arguments": '{"x":'},
                    }
                ]
            }
        ),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]}),
        chunk({}, "tool_calls"),
        "[DONE]",
    ]
    deltas = [part for frame in frames for part in decoder.feed(SSEEvent("message", frame))]
    decoder.finish()
    assert [part.call_id for part in deltas if part.kind == "tool_start"] == ["opaque:/+="]
    assert "".join(part.text for part in deltas if part.kind == "tool_delta") == '{"x":1}'
    assert deltas[-1].reason == "tool_calls"


@contextmanager
def listener(chunks):
    errors, calls = [], []

    class CheckedServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            errors.append("uncaught handler exception")

    class Fragmented(io.BytesIO):
        def __init__(self, parts):
            super().__init__()
            self.parts, self.pending = iter(parts), b""

        def read1(self, size):
            if not self.pending:
                self.pending = next(self.parts, b"")
            result, self.pending = self.pending[:size], self.pending[size:]
            return result

    def upstream(req, *, timeout):
        calls.append(req)
        if len(calls) == 1:
            return Fragmented(chunks)
        usage = {
            "object": "chat.completion.chunk",
            "model": "m",
            "choices": [],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        }
        return Fragmented(
            [
                encode_sse("message", chunk({"role": "assistant", "content": "recovered"})),
                encode_sse("message", chunk({}, "stop")),
                encode_sse("message", json.dumps(usage)),
                encode_sse("message", "[DONE]"),
            ]
        )

    server = CheckedServer(
        ("127.0.0.1", 0),
        make_handler(
            GatewayConfig("chat", "https://example.invalid/v1", "fixture", "local", messages_usage_policy="deferred"),
            upstream_open=upstream,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert errors == []


@pytest.mark.parametrize("value", INVALID)
@pytest.mark.parametrize("target", ["messages", "responses"])
@pytest.mark.parametrize("after_payload", [False, True])
def test_bad_tool_array_returns_http_or_sse_error_without_handler_crash(value, target, after_payload):
    chunks = (
        [encode_sse("message", chunk({"role": "assistant", "content": "synthetic partial"}))] if after_payload else []
    )
    chunks.append(encode_sse("message", chunk({"tool_calls": value})))
    body = convert_request(
        {"model": "m", "max_tokens": 20, "stream": True, "messages": [{"role": "user", "content": "synthetic"}]},
        "chat",
        target,
    )
    with listener(chunks) as (origin, calls):
        request = Request(
            origin + "/v1/" + target,
            data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer local", "Content-Type": "application/json"},
        )
        if after_payload:
            with urlopen(request, timeout=3) as response:
                assert response.status == 200
                raw = response.read()
            events = SSEParser().feed(raw)
            assert events[-1].event == "error"
            assert b"synthetic partial" in raw
            assert b"response.completed" not in raw and b"message_stop" not in raw
        else:
            with pytest.raises(HTTPError) as caught:
                urlopen(request, timeout=3)
            assert caught.value.code == 502
            assert isinstance(json.load(caught.value)["error"], dict)
            caught.value.close()
        assert len(calls) == 1
        with urlopen(request, timeout=3) as response:
            assert response.status == 200
            recovered = response.read()
        events = SSEParser().feed(recovered)
        assert b"recovered" in recovered
        assert events[-1].event == {"messages": "message_stop", "responses": "response.completed"}[target]
        assert len(calls) == 2
