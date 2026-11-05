"""Operator-declared defaults are scoped, actually sent, and never fabricated."""

import copy
import io
import json

import pytest
from test_json_wire_boundaries import request
from test_response_snapshot_consistency import listener, snapshots

from protoverta import CapabilityRecord, CapabilityRegistry
from protoverta.capabilities import request_features
from protoverta.provider_profiles import ProviderProfile, ProviderProfiles
from protoverta.server import GatewayConfig


def profile(defaults, **extra):
    return ProviderProfile(
        "https://example.invalid/v1",
        "chat",
        "m",
        "2026-11-04",
        "synthetic default support fixture",
        "max_completion_tokens",
        "completion_total",
        sampling_defaults=defaults,
        **extra,
    )


@pytest.mark.parametrize(
    "defaults",
    [
        {},
        {"temperature": None},
        {"temperature": True},
        {"temperature": float("nan")},
        {"temperature": float("inf")},
        {"temperature": -1},
        {"temperature": 3},
        {"top_p": -1},
        {"top_p": 2},
        {"top_p": "1"},
        {"unknown": 0},
    ],
)
def test_invalid_defaults_fail_at_configuration_time(defaults):
    with pytest.raises(ValueError):
        profile(defaults)


def test_exact_scope_copy_isolation_explicit_zero_and_evidence_identity():
    values = {"temperature": 0.7, "top_p": 0.9}
    record = profile(values)
    values["temperature"] = 1.9
    registry = ProviderProfiles((record,))
    request_body = {"model": "m", "temperature": 0, "top_p": 0}
    before = copy.deepcopy(request_body)
    assert registry.apply(record.base_url, "chat", request_body) == before
    assert request_body == before
    assert registry.apply(record.base_url, "chat", {"model": "m"})["temperature"] == 0.7
    for base, proto, model in ((record.base_url, "chat", "other"), ("https://other.invalid/v1", "chat", "m"), (record.base_url, "messages", "m")):
        body = {"model": model}
        assert registry.apply(base, proto, body) == body
    with pytest.raises(TypeError):
        record.sampling_defaults["temperature"] = 0.5
    assert registry.fingerprint() != ProviderProfiles((profile({"temperature": 0.8}),)).fingerprint()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_sampling_snapshot_matches_actual_wire_and_caller_settings_win(stream, explicit):
    from test_effective_tool_policy import reply

    registry = ProviderProfiles((profile({"temperature": 0.7, "top_p": 0.9}),))
    cfg = GatewayConfig("chat", "https://example.invalid/v1", "synthetic", "local", provider_profiles=registry)
    body = {"model": "m", "input": "synthetic", "stream": stream}
    if explicit:
        body.update(temperature=0, top_p=0)
    before = copy.deepcopy(body)
    calls = []

    def upstream(req, *, timeout):
        calls.append(json.loads(req.data))
        return io.BytesIO(reply("chat", stream=stream))

    with listener(cfg, upstream) as url, request(url, "responses", json.dumps(body).encode()) as response:
        raw = response.read()
    expected = {"temperature": 0, "top_p": 0} if explicit else {"temperature": 0.7, "top_p": 0.9}
    assert len(calls) == 1 and {k: calls[0][k] for k in expected} == expected
    for snapshot in snapshots(raw) if stream else [json.loads(raw)]:
        assert {k: snapshot[k] for k in expected} == expected
        assert "service_tier" not in snapshot
    assert body == before


def test_configured_default_respects_a_model_record_explicitly_rejecting_temperature():
    from urllib.error import HTTPError

    assert "temperature" in request_features({"temperature": 0}, "chat")
    caps = CapabilityRegistry(
        (CapabilityRecord("https://example.invalid/v1", "chat", "m", {"temperature": False}, "2026-11-04", "synthetic unsupported fixture"),)
    )
    cfg = GatewayConfig(
        "chat", "https://example.invalid/v1", "synthetic", "local", provider_profiles=ProviderProfiles((profile({"temperature": 0.7}),)), capabilities=caps
    )
    seen = []
    with listener(cfg, lambda *a, **k: seen.append(1)) as url:
        with pytest.raises(HTTPError) as caught:
            request(url, "responses", json.dumps({"model": "m", "input": "synthetic"}).encode())
        assert caught.value.code == 422
        caught.value.close()
    assert seen == []
