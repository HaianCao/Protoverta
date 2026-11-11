import copy
import io
import json
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post

from protoverta import CapabilityRecord, CapabilityRegistry, ConversionError
from protoverta.server import GatewayConfig


def document(version=2):
    return {
        "schema_version": version,
        "models": [
            {
                "base_url": "https://example.test/v1",
                "protocol": "chat",
                "model": "m",
                "features": {"text": True, "top_p": True, "reasoning_effort": True},
                "checked_at": "2026-10-24",
                "evidence": "synthetic limit fixture",
                "max_output_tokens": 64,
                "context_window_tokens": 1024,
                "reasoning_efforts": ["low", "high"],
            }
        ],
    }


def test_v2_limits_are_immutable_and_v1_does_not_accept_new_fields():
    body = document()
    original = copy.deepcopy(body)
    registry = CapabilityRegistry.from_dict(body)
    assert body == original
    assert registry.lookup("https://example.test/v1", "chat", "m").context_window_tokens == 1024
    assert registry.lookup("https://other.test/v1", "chat", "m") is None
    registry.preflight(
        "https://example.test/v1",
        "chat",
        {"model": "m", "max_completion_tokens": 64, "top_p": 0.2, "reasoning_effort": "low"},
        require_verified=True,
    )
    body["models"][0]["reasoning_efforts"].append("medium")
    with pytest.raises(ConversionError, match="reasoning effort"):
        registry.preflight("https://example.test/v1", "chat", {"model": "m", "reasoning_effort": "medium"})
    with pytest.raises(ValueError, match="record fields"):
        CapabilityRegistry.from_dict(document(1))


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_output_tokens", 0),
        ("context_window_tokens", True),
        ("max_output_tokens", -1),
        ("context_window_tokens", 1.5),
        ("reasoning_efforts", []),
        ("reasoning_efforts", ["low", "low"]),
        ("reasoning_efforts", ["unknown"]),
        ("reasoning_efforts", "low"),
    ],
)
def test_invalid_limits_and_efforts_are_rejected(field, value):
    body = document()
    body["models"][0][field] = value
    with pytest.raises(ValueError):
        CapabilityRegistry.from_dict(body)


@pytest.mark.parametrize(
    "protocol,field,effort",
    [
        ("chat", "max_completion_tokens", {"reasoning_effort": "low"}),
        ("messages", "max_tokens", {"output_config": {"effort": "low"}}),
        ("responses", "max_output_tokens", {"reasoning": {"effort": "low"}}),
    ],
)
def test_limits_are_checked_for_the_exact_model_and_do_not_clamp(protocol, field, effort):
    record = CapabilityRecord(
        "https://example.test/v1",
        protocol,
        "m",
        {"text": True},
        "2026-10-24",
        "fixture",
        max_output_tokens=64,
        context_window_tokens=1024,
        reasoning_efforts=("low",),
    )
    registry = CapabilityRegistry((record,))
    body = {"model": "m", field: 65, **effort}
    before = copy.deepcopy(body)
    with pytest.raises(ConversionError, match="max_output_tokens"):
        registry.preflight("https://example.test/v1", protocol, body)
    assert body == before
    registry.preflight("https://other.test/v1", protocol, body)
    registry.preflight("https://example.test/v1", protocol, dict(body, model="other"))


def test_gateway_rejects_recorded_limit_before_any_network_io():
    calls = []

    def upstream(request, *, timeout):
        calls.append(request)
        return io.BytesIO(b"{}")

    config = GatewayConfig(
        "chat", "https://example.test/v1", "key", "local", capabilities=CapabilityRegistry.from_dict(document())
    )
    with listener(config, upstream) as url, pytest.raises(HTTPError) as caught:
        post(url, {"model": "m", "input": "Hi", "max_output_tokens": 65})
    assert caught.value.code == 422
    assert "max_output_tokens" in json.load(caught.value)["error"]["message"]
    caught.value.close()
    assert calls == []
