import copy
import io
import json

import pytest
from test_gateway_capabilities import listener, post

from protoverta import ConversionError, convert_request
from protoverta.capabilities import (
    CapabilityRecord,
    CapabilityRegistry,
    request_features,
)
from protoverta.server import GatewayConfig

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"], "additionalProperties": False}


def body(protocol, strict):
    fn = {"name": "fn", "description": "demo", "parameters": copy.deepcopy(SCHEMA), "strict": strict}
    if protocol == "chat":
        return {"model": "m", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 64, "tools": [{"type": "function", "function": fn}]}
    if protocol == "responses":
        return {"model": "m", "input": "hi", "max_output_tokens": 64, "tools": [{"type": "function", **fn}]}
    return {"model": "m", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 64, "tools": [{"name": "fn", "description": "demo", "input_schema": copy.deepcopy(SCHEMA), "strict": strict}]}


@pytest.mark.parametrize("source,target", [("messages", "chat"), ("messages", "responses"), ("chat", "messages"), ("responses", "messages")])
@pytest.mark.parametrize("strict", [True, False])
@pytest.mark.parametrize("stream", [False, True])
def test_strict_messages_request_round_trip(source, target, strict, stream):
    request = body(source, strict)
    request["stream"] = stream
    original = copy.deepcopy(request)
    converted = convert_request(request, source, target)
    tool = converted["tools"][0]
    if target == "chat":
        tool = tool["function"]
    assert tool["strict"] is strict
    assert tool["input_schema" if target == "messages" else "parameters"] == SCHEMA
    assert ("strict_tools" in request_features(converted, target)) == strict
    restored = convert_request(converted, target, source)["tools"][0]
    assert restored == request["tools"][0]
    assert request == original


@pytest.mark.parametrize("schema", [
    {"type": "object", "properties": {"x": {"type": "integer"}}, "additionalProperties": False},
    {"type": "object", "properties": {}, "additionalProperties": True},
    {"type": "object", "properties": {"x": {"$ref": "#/$defs/X"}}, "required": ["x"], "additionalProperties": False},
    {"type": "object", "properties": {"x": {"type": "integer", "minimum": 0}}, "required": ["x"], "additionalProperties": False},
])
def test_unverified_schema_is_rejected_not_sanitized(schema):
    request = body("messages", True)
    request["tools"][0]["input_schema"] = schema
    with pytest.raises(ConversionError):
        convert_request(request, "messages", "responses")


def test_gateway_sends_strict_to_messages_and_checks_capability():
    captured = []

    def upstream(request, *, timeout):
        captured.append(json.loads(request.data))
        return io.BytesIO(b'{"model":"m","role":"assistant","content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}}')

    registry = CapabilityRegistry((CapabilityRecord("https://example.test/v1", "messages", "m", {"text": True, "tools": True, "strict_tools": True}, "2026-10-24", "synthetic"),))
    config = GatewayConfig("messages", "https://example.test/v1", "key", "local", capabilities=registry, require_verified_capabilities=True)
    with listener(config, upstream) as url, post(url, body("responses", True)) as response:
        assert response.status == 200
    assert captured[0]["tools"][0]["strict"] is True
    assert captured[0]["tools"][0]["input_schema"] == SCHEMA
