import io
import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from protoverta.server import GatewayConfig, make_handler
from protoverta.sse import SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta


@contextmanager
def listener(config, upstream):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=upstream))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/responses"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def post(url, body):
    return urlopen(Request(url, data=json.dumps(body).encode(), headers={"Authorization": "Bearer local"}), timeout=3)


@pytest.mark.parametrize("protocol", ["chat", "messages"])
@pytest.mark.parametrize("stream", [False, True])
def test_gateway_namespace_round_trip_and_request_isolation(protocol, stream):
    seen = []

    def upstream(request, *, timeout):
        body = json.loads(request.data)
        alias = body["tools"][0]["function"]["name"] if protocol == "chat" else body["tools"][0]["name"]
        seen.append(alias)
        if stream:
            encoder = StreamEncoder(protocol)
            deltas = [Delta("start", model="m", input_tokens=1, output_tokens=0), Delta("tool_start", index=0, call_id="call_1", name=alias), Delta("tool_delta", index=0, text='{"x":1}'), Delta("tool_end", index=0), Delta("finish", reason="tool_calls", input_tokens=1, output_tokens=1)]
            return io.BytesIO(b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta)))
        if protocol == "chat":
            result = {"model": "m", "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": alias, "arguments": '{"x":1}'}}]}}]}
        else:
            result = {"model": "m", "role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": alias, "input": {"x": 1}}], "stop_reason": "tool_use", "usage": {"input_tokens": 1, "output_tokens": 1}}
        return io.BytesIO(json.dumps(result).encode())

    config = GatewayConfig(protocol, "https://example.test/v1", "key", "local", namespace_tools="flatten")
    with listener(config, upstream) as url:
        for namespace in ("crm", "mail", "crm"):
            body = {"model": "m", "input": "Hi", "max_output_tokens": 64, "stream": stream, "tools": [{"type": "namespace", "name": namespace, "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}]}]}
            with post(url, body) as response:
                wire = response.read()
            if stream:
                events = [json.loads(event.data) for event in SSEParser().feed(wire)]
                assert events[-1]["type"] == "response.completed"
                items = [event["item"] for event in events if "item" in event] + events[-1]["response"]["output"]
            else:
                items = json.loads(wire)["output"]
            calls = [item for item in items if item["type"] == "function_call"]
            assert calls
            assert all((item["namespace"], item["name"], item["call_id"]) == (namespace, "lookup", "call_1") for item in calls)
    assert seen[0] == seen[2] != seen[1]


def test_gateway_mid_system_allowlist_is_exact_and_keeps_invalid_history_rejected():
    captured = []

    def upstream(request, *, timeout):
        captured.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"model": "verified", "role": "assistant", "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}).encode())

    config = GatewayConfig("messages", "https://example.test/v1", "key", "local", client_profile="codex", default_max_output_tokens=64, messages_system_models=("verified",))
    with listener(config, upstream) as url:
        body = {"model": "verified", "input": [{"role": "user", "content": "Hi"}, {"role": "developer", "content": "New instruction"}]}
        with post(url, body) as response:
            assert response.status == 200
        assert [message["role"] for message in captured[0]["messages"]] == ["user", "system"]
        for model, roles in (("verified-other", ["user", "developer"]), ("verified", ["user", "assistant", "developer"])):
            body = {"model": model, "input": [{"role": role, "content": "text"} for role in roles]}
            with pytest.raises(HTTPError) as caught:
                post(url, body)
            assert caught.value.code == 422
            caught.value.close()
    assert len(captured) == 1
