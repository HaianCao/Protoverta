"""Explicit controls are forwarded only to targets with equivalent semantics."""

import copy
import io
import json
from urllib.error import HTTPError

import pytest
from test_effective_tool_policy import reply
from test_json_wire_boundaries import request
from test_response_snapshot_consistency import listener, snapshots

from protoverta import (
    CapabilityRecord,
    CapabilityRegistry,
    ConversionError,
    convert_request,
)
from protoverta.capabilities import request_features
from protoverta.server import GatewayConfig


def body(source, **controls):
    request_body = {"model": "m", "input": "synthetic", "max_output_tokens": 20}
    return {**convert_request(request_body, "responses", source), **controls}


@pytest.mark.parametrize("key", ["frequency_penalty", "presence_penalty"])
@pytest.mark.parametrize("value", [-2, -0.7, 0, 0.4, 2, None])
def test_openresponses_penalties_reach_chat_without_mutation(key, value):
    original = body("responses", **{key: value})
    before = copy.deepcopy(original)
    output = convert_request(original, "responses", "chat")
    assert output.get(key) == value
    assert original == before


@pytest.mark.parametrize("key", ["frequency_penalty", "presence_penalty"])
@pytest.mark.parametrize("value", [True, "0", float("nan"), float("inf"), -2.01, 2.01, {}])
def test_invalid_penalties_reject_before_conversion(key, value):
    with pytest.raises(ConversionError, match=key):
        convert_request(body("responses", **{key: value}), "responses", "chat")


@pytest.mark.parametrize("source,target", [("chat", "responses"), ("chat", "messages"), ("responses", "messages")])
@pytest.mark.parametrize("key", ["frequency_penalty", "presence_penalty"])
@pytest.mark.parametrize("value", [0, 0.5])
def test_penalties_cannot_be_silently_dropped_or_sent_as_unverified_response_extensions(source, target, key, value):
    with pytest.raises(ConversionError, match="penalties require a Chat target"):
        convert_request(body(source, **{key: value}), source, target)


@pytest.mark.parametrize(
    "source,target,controls",
    [
        ("chat", "responses", {"logprobs": False}),
        ("chat", "responses", {"top_logprobs": 0}),
        ("chat", "messages", {"logprobs": False, "top_logprobs": 0}),
        ("responses", "chat", {"top_logprobs": 0}),
        ("responses", "messages", {"top_logprobs": 0}),
    ],
)
def test_explicitly_disabled_logprobs_is_portable_without_enabling_probabilities(source, target, controls):
    output = convert_request(body(source, **controls), source, target)
    if target == "responses":
        assert output["top_logprobs"] == 0
    elif target == "chat":
        assert output["logprobs"] is False and "top_logprobs" not in output
    else:
        assert "logprobs" not in output and "top_logprobs" not in output


@pytest.mark.parametrize(
    "source,target", [("chat", "responses"), ("chat", "messages"), ("responses", "chat"), ("responses", "messages")]
)
@pytest.mark.parametrize(
    "controls",
    [
        {"top_logprobs": 1},
        {"top_logprobs": 20},
        {"top_logprobs": 21},
        {"top_logprobs": -1},
        {"top_logprobs": True},
        {"top_logprobs": 0.5},
    ],
)
def test_enabled_or_malformed_logprobs_stays_rejected(source, target, controls):
    with pytest.raises(ConversionError):
        convert_request(body(source, **controls), source, target)


@pytest.mark.parametrize(
    "controls", [{"logprobs": True}, {"logprobs": True, "top_logprobs": 0}, {"logprobs": "false"}, {"logprobs": 0}]
)
def test_chat_token_logprobs_cannot_be_hidden_by_zero_top_k(controls):
    with pytest.raises(ConversionError):
        convert_request(body("chat", **controls), "chat", "responses")


@pytest.mark.parametrize("target", ["chat", "responses"])
@pytest.mark.parametrize("key", ["frequency_penalty", "presence_penalty", "top_logprobs", "logprobs"])
def test_messages_has_no_such_input_controls_even_if_zero(target, key):
    with pytest.raises(ConversionError):
        convert_request(body("messages", **{key: 0}), "messages", target)


@pytest.mark.parametrize("stream", [False, True])
def test_gateway_wire_and_response_snapshot_share_the_explicit_controls(stream):
    seen = []

    def upstream(req, *, timeout):
        seen.append(json.loads(req.data))
        return io.BytesIO(reply("chat", stream=stream))

    controls = {"frequency_penalty": 0, "presence_penalty": -0.5, "top_logprobs": 0}
    cfg = GatewayConfig("chat", "https://example.invalid/v1", "synthetic", "local")
    with (
        listener(cfg, upstream) as url,
        request(url, "responses", json.dumps(body("responses", stream=stream, **controls)).encode()) as response,
    ):
        raw = response.read()
    assert len(seen) == 1 and seen[0]["logprobs"] is False and "top_logprobs" not in seen[0]
    for key in ("frequency_penalty", "presence_penalty"):
        assert seen[0][key] == controls[key]
    for snapshot in snapshots(raw) if stream else [json.loads(raw)]:
        assert {key: snapshot[key] for key in controls} == controls


@pytest.mark.parametrize("key", ["frequency_penalty", "presence_penalty"])
def test_explicitly_unsupported_penalty_blocks_zero_value_before_io(key):
    assert key in request_features({key: 0}, "chat")
    caps = CapabilityRegistry(
        (
            CapabilityRecord(
                "https://example.invalid/v1", "chat", "m", {key: False}, "2026-11-04", "synthetic unsupported control"
            ),
        )
    )
    cfg = GatewayConfig("chat", "https://example.invalid/v1", "synthetic", "local", capabilities=caps)
    seen = []
    with listener(cfg, lambda *a, **k: seen.append(1)) as url:
        with pytest.raises(HTTPError) as caught:
            request(url, "responses", json.dumps(body("responses", **{key: 0})).encode())
        assert caught.value.code == 422
        caught.value.close()
    assert seen == []
