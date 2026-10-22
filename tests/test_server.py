import json
import io
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from protoverta.core import Protocol
from protoverta.server import GatewayConfig, make_handler, serve


def test_base_url_joining_and_validation():
    config = GatewayConfig(Protocol.MESSAGES, "https://example.com/v1", "key", "local", "x-api-key")
    assert config.upstream_url == "https://example.com/v1/messages"
    assert GatewayConfig(Protocol.CHAT, "https://example.com", "key", "local").upstream_url == "https://example.com/v1/chat/completions"
    with pytest.raises(ValueError):
        GatewayConfig(Protocol.CHAT, "http://example.com/v1", "key", "local")
    with pytest.raises(ValueError, match="line breaks"):
        GatewayConfig(Protocol.CHAT, "https://example.com/v1", "bad\nkey", "local")


def test_local_auth_and_unsupported_stream_route():
    server = serve(GatewayConfig(Protocol.CHAT, "https://example.com/v1", "key", "local"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    try:
        with pytest.raises(HTTPError) as unauth:
            urlopen(Request(url, data=b"{}", method="POST"))
        assert unauth.value.code == 401
        body = json.dumps({"model": "m", "messages": [], "max_tokens": 8, "stream": True}).encode()
        with pytest.raises(HTTPError) as unsupported:
            urlopen(Request(url.replace("/chat/completions", "/messages"), data=body, headers={"Authorization": "Bearer local"}, method="POST"))
        assert unsupported.value.code == 422
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_gateway_transcodes_messages_sse_to_chat_without_network():
    captured = {}

    def upstream_open(request, *, timeout):
        captured["body"] = json.loads(request.data)
        events = [
            ("message_start", {"type": "message_start", "message": {"model": "m", "content": [], "usage": {"input_tokens": 3}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "pong"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        from protoverta.sse import encode_sse

        return io.BytesIO(b"".join(encode_sse(kind, json.dumps(body)) for kind, body in events))

    config = GatewayConfig(Protocol.MESSAGES, "https://example.com/v1", "upstream-secret", "local-token", "x-api-key")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=upstream_open))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
        body = {"model": "m", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 8, "stream": True}
        request = Request(url, data=json.dumps(body).encode(), headers={"Authorization": "Bearer local-token"}, method="POST")
        with urlopen(request) as response:
            wire = response.read()
            assert response.headers["Content-Type"].startswith("text/event-stream")
        assert b'"content":"pong"' in wire
        assert b"[DONE]" in wire
        assert captured["body"]["stream"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_gateway_reports_interrupted_sse_without_done_marker():
    from protoverta.sse import encode_sse

    def upstream_open(request, *, timeout):
        return io.BytesIO(encode_sse("message_start", json.dumps({
            "type": "message_start", "message": {"model": "m", "content": [], "usage": {"input_tokens": 1}},
        })))

    config = GatewayConfig(Protocol.MESSAGES, "https://example.com/v1", "key", "local", "x-api-key")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=upstream_open))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
        body = {"model": "m", "messages": [], "max_tokens": 8, "stream": True}
        request = Request(url, data=json.dumps(body).encode(), headers={"Authorization": "Bearer local"}, method="POST")
        with urlopen(request) as response:
            wire = response.read()
        assert b"event: error" in wire
        assert b"[DONE]" not in wire
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_gateway_converts_request_and_response_without_network():
    captured = {}

    def upstream_open(request, *, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data)
        return io.BytesIO(json.dumps({
            "model": "m",
            "choices": [{"message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        }).encode())

    config = GatewayConfig(Protocol.CHAT, "https://example.com/v1", "upstream-secret", "local-token")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=upstream_open))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/messages"
        body = {"model": "m", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 32}
        request = Request(url, data=json.dumps(body).encode(), headers={"Authorization": "Bearer local-token"}, method="POST")
        with urlopen(request) as response:
            result = json.loads(response.read())
        assert result["content"] == [{"type": "text", "text": "pong"}]
        assert result["usage"] == {"input_tokens": 3, "output_tokens": 1}
        assert captured["url"] == "https://example.com/v1/chat/completions"
        assert captured["body"]["messages"][0]["content"] == "ping"
        assert captured["headers"]["Authorization"] == "Bearer upstream-secret"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
