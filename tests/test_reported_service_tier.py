"""Serving observations come from upstream output, never requested defaults."""

import copy
import io
import json
from urllib.error import HTTPError

import pytest
from test_effective_tool_policy import reply
from test_json_wire_boundaries import request
from test_response_snapshot_consistency import listener, snapshots

from protoverta import ConversionError, StreamTranscoder, convert_response
from protoverta.server import GatewayConfig
from protoverta.sse import SSEParser, encode_sse
from protoverta.streaming import StreamError

TIERS = ("auto", "default", "flex", "scale", "priority", "fast")


def result(source, tier=None):
    body = json.loads(reply(source))
    if tier is not None:
        body["service_tier"] = tier
    return body


def wire(source, tier, *, early=False):
    events = SSEParser().feed(reply(source, stream=True))
    output = []
    for index, event in enumerate(events):
        if event.data == "[DONE]":
            output.append(encode_sse(event.event, event.data))
            continue
        body = json.loads(event.data)
        if source == "chat" and index == (0 if early else len(events) - 2):
            body["service_tier"] = tier
        elif source == "responses" and body.get("type") in {"response.created", "response.completed"}:
            body["response"]["service_tier"] = "auto" if body["type"] == "response.created" else tier
        output.append(encode_sse(event.event, json.dumps(body)))
    return b"".join(output)


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("source,target", [("chat", "responses"), ("responses", "chat")])
def test_json_preserves_reported_tier_without_mutating_input(source, target, tier):
    body = result(source, tier)
    before = copy.deepcopy(body)
    assert convert_response(body, source, target)["service_tier"] == tier
    assert body == before


@pytest.mark.parametrize("source,target", [(a, b) for a in ("chat", "responses", "messages") for b in ("chat", "responses", "messages") if a != b])
def test_missing_tier_is_never_filled_from_request_context_or_protocol(source, target):
    context = {"service_tier": "priority"} if target == "responses" else None
    assert "service_tier" not in convert_response(result(source), source, target, request_context=context)


@pytest.mark.parametrize("source", ["chat", "responses"])
@pytest.mark.parametrize("tier", [True, 4, "", {}, "private-vendor-value", "ultrafast"])
def test_invalid_or_nonportable_reported_tiers_fail_with_static_errors(source, tier):
    body = result(source)
    body["service_tier"] = tier
    with pytest.raises(ConversionError, match="upstream service_tier is not supported"):
        convert_response(body, source, "responses" if source == "chat" else "chat")


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("source,target", [("chat", "responses"), ("responses", "chat")])
@pytest.mark.parametrize("early", [False, True])
def test_stream_terminal_tier_is_retained_without_claiming_it_at_creation(source, target, tier, early):
    pipe = StreamTranscoder(source, target, request_context={"service_tier": "priority"} if target == "responses" else None)
    raw = wire(source, tier, early=early)
    converted = b"".join(pipe.feed(raw[i : i + 7]) for i in range(0, len(raw), 7)) + pipe.finish()
    if target == "responses":
        created, completed = snapshots(converted)
        assert "service_tier" not in created
        assert completed["service_tier"] == tier
    else:
        chunks = [json.loads(e.data) for e in SSEParser().feed(converted) if e.data != "[DONE]"]
        assert "service_tier" not in chunks[0]
        assert chunks[-1]["service_tier"] == tier


def test_chat_tier_conflict_is_rejected_before_success():
    events = SSEParser().feed(wire("chat", "default", early=True))
    raw = []
    for index, event in enumerate(events):
        if event.data != "[DONE]":
            body = json.loads(event.data)
            if index == len(events) - 2:
                body["service_tier"] = "priority"
            raw.append(encode_sse(event.event, json.dumps(body)))
        else:
            raw.append(encode_sse(event.event, event.data))
    with pytest.raises(StreamError, match="service_tier changed"):
        StreamTranscoder("chat", "responses").feed(b"".join(raw))


@pytest.mark.parametrize("stream", [False, True])
def test_gateway_reports_actual_tier_but_does_not_add_request_tier(stream):
    seen = []

    def upstream(req, *, timeout):
        seen.append(json.loads(req.data))
        return io.BytesIO(wire("chat", "flex") if stream else json.dumps(result("chat", "flex")).encode())

    cfg = GatewayConfig("chat", "https://example.invalid/v1", "synthetic", "local")
    with listener(cfg, upstream) as url, request(url, "responses", json.dumps({"model": "m", "input": "synthetic", "stream": stream}).encode()) as response:
        raw = response.read()
    final = snapshots(raw)[-1] if stream else json.loads(raw)
    assert final["service_tier"] == "flex"
    assert len(seen) == 1 and "service_tier" not in seen[0]


@pytest.mark.parametrize("stream", [False, True])
def test_gateway_rejects_invalid_tier_without_leaking_value_or_completing(stream):
    secret = "synthetic-untrusted-value"
    cfg = GatewayConfig("chat", "https://example.invalid/v1", "synthetic", "local")
    raw = wire("chat", secret) if stream else json.dumps(result("chat", secret)).encode()
    with listener(cfg, lambda *a, **k: io.BytesIO(raw)) as url:
        with pytest.raises(HTTPError) as caught:
            request(url, "responses", json.dumps({"model": "m", "input": "synthetic", "stream": stream}).encode())
        assert caught.value.code == 502
        assert secret.encode() not in caught.value.read()
        caught.value.close()


@pytest.mark.parametrize("source", ["chat", "responses"])
def test_messages_does_not_advertise_an_openai_serving_tier(source):
    assert "service_tier" not in convert_response(result(source, "priority"), source, "messages")
