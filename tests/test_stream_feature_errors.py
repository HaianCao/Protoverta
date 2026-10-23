import io
import json
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post

from protoverta.server import GatewayConfig
from protoverta.sse import SSEParser, encode_sse


class SegmentedResponse(io.BytesIO):
    def __init__(self, chunks):
        super().__init__()
        self.chunks = iter(chunks)

    def read1(self, size):
        return next(self.chunks, b"")


@pytest.mark.parametrize("target", ["messages", "responses"])
@pytest.mark.parametrize("mode", ["combined", "split_start", "visible"])
@pytest.mark.parametrize("field", ["reasoning_content", "reasoning", "reasoning_details"])
def test_unsupported_reasoning_is_non_retryable_and_contains_no_upstream_text(target, mode, field):
    def chunk(delta):
        return encode_sse("message", json.dumps({"object": "chat.completion.chunk", "model": "m", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}))

    start = chunk({"role": "assistant", "content": "already-visible" if mode == "visible" else ""})
    reasoning = chunk({field: "private-upstream-sentinel"})

    def upstream(request, *, timeout):
        return io.BytesIO(start + reasoning) if mode == "combined" else SegmentedResponse([start, reasoning])

    config = GatewayConfig("chat", "https://example.test/v1", "upstream-key", "local", messages_usage_policy="deferred" if target == "messages" else "strict")
    body = {"model": "m", "stream": True, "max_tokens": 32, "messages": [{"role": "user", "content": "Hi"}]} if target == "messages" else {"model": "m", "input": "Hi", "stream": True}
    with listener(config, upstream) as origin:
        url = origin.removesuffix("responses") + target
        if mode == "visible":
            with post(url, body) as response:
                wire = response.read()
            events = SSEParser().feed(wire)
            assert events[-1].event == "error"
            error = json.loads(events[-1].data)
            assert b"message_stop" not in wire and b"response.completed" not in wire and b"[DONE]" not in wire
        else:
            with pytest.raises(HTTPError) as caught:
                post(url, body)
            assert caught.value.code == 422
            assert caught.value.headers["x-should-retry"] == "false"
            error = json.load(caught.value)
            caught.value.close()
    assert error["error"]["type"] == "invalid_request_error"
    assert error["error"]["code"] == "unsupported_reasoning"
    assert "private-upstream-sentinel" not in json.dumps(error)
    assert "upstream-key" not in json.dumps(error)


@pytest.mark.parametrize("target", ["messages", "responses"])
def test_empty_completed_stream_releases_initial_metadata_and_terminal(target):
    def chunk(delta, reason=None):
        return encode_sse("message", json.dumps({"object": "chat.completion.chunk", "model": "m", "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}))

    usage = encode_sse("message", json.dumps({"object": "chat.completion.chunk", "model": "m", "choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2}}))
    chunks = [chunk({"role": "assistant"}), chunk({}, "stop"), usage, encode_sse("message", "[DONE]")]
    config = GatewayConfig("chat", "https://example.test/v1", "key", "local", messages_usage_policy="deferred" if target == "messages" else "strict")
    body = {"model": "m", "stream": True, "max_tokens": 32, "messages": []} if target == "messages" else {"model": "m", "input": "Hi", "stream": True}
    with listener(config, lambda request, timeout: SegmentedResponse(chunks)) as origin, post(origin.removesuffix("responses") + target, body) as response:
        wire = response.read()
    events = SSEParser().feed(wire)
    expected_start = "message_start" if target == "messages" else "response.created"
    expected_end = "message_stop" if target == "messages" else "response.completed"
    assert events[0].event == expected_start
    assert events[-1].event == expected_end
    assert all(event.event != "error" for event in events)
