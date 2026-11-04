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
from protoverta.provider_profiles import (
    GenerationProfile,
    ProviderProfile,
    ProviderProfiles,
)
from protoverta.server import GatewayConfig

BASE = "https://example.invalid/v1"


def record(protocol, defaults=None, **extra):
    return GenerationProfile(BASE, protocol, "m", "2026-11-04", "synthetic generation contract", defaults, **extra)


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("defaults", [{}, {"temperature": None}, {"temperature": True}, {"temperature": float("nan")},
                                      {"temperature": float("inf")}, {"top_p": 2}, {"top_p": "1"}, {"frequency_penalty": 0}])
def test_invalid_default_contracts_fail_before_use(protocol, defaults):
    with pytest.raises(ValueError):
        record(protocol, defaults)


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
def test_sampling_contract_is_immutable_exact_and_cannot_override_explicit_zero(protocol):
    defaults = {"temperature": 0.6, "top_p": 0.9}
    profile = record(protocol, defaults)
    defaults["temperature"] = 1.8
    registry = ProviderProfiles((profile,))
    body = {"model": "m", "temperature": 0, "top_p": 0}
    before = copy.deepcopy(body)
    assert registry.apply(BASE, protocol, body) == before and body == before
    assert registry.apply(BASE, protocol, {"model": "m"})["temperature"] == 0.6
    assert registry.apply(BASE, protocol, {"model": "other"}) == {"model": "other"}
    assert registry.apply("https://other.invalid/v1", protocol, {"model": "m"}) == {"model": "m"}
    assert not registry.supports_prompt_cache(BASE, protocol, "m")
    assert registry.restore(BASE, protocol, "m", body) is body
    assert registry.stream_delta_restorer(BASE, protocol, "m") is None
    with pytest.raises(TypeError):
        profile.sampling_defaults["temperature"] = 1


def test_messages_range_and_declared_contract_requirement():
    with pytest.raises(ValueError, match="Messages temperature"):
        record("messages", {"temperature": 1.1})
    assert record("responses", {"temperature": 1.1}).sampling_defaults["temperature"] == 1.1
    with pytest.raises(ValueError, match="explicit contract"):
        record("chat")


@pytest.mark.parametrize("flag", [None, 1, "true"])
def test_penalty_dialect_requires_boolean_response_contract(flag):
    with pytest.raises(ValueError):
        record("responses", responses_penalties=flag)


@pytest.mark.parametrize("protocol", ["chat", "messages"])
def test_response_dialect_cannot_apply_to_other_protocols(protocol):
    with pytest.raises(ValueError):
        record(protocol, {"temperature": 0.7}, responses_penalties=True)


def test_file_schema_preserves_legacy_records_and_fingerprints_new_contract(tmp_path):
    legacy = {"base_url": BASE, "protocol": "chat", "model": "old", "checked_at": "2026-11-04", "evidence": "synthetic legacy",
              "chat_token_field": "max_tokens", "token_budget_semantics": "completion_total"}
    new = {"kind": "generation", "base_url": BASE, "protocol": "responses", "model": "m", "checked_at": "2026-11-04",
           "evidence": "synthetic response dialect", "responses_penalties": True, "sampling_defaults": {"temperature": 0.7}}
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"schema_version": 1, "profiles": [legacy, new]}))
    loaded = ProviderProfiles.load(path)
    expected = ProviderProfiles((ProviderProfile(**legacy), record("responses", {"temperature": 0.7}, responses_penalties=True)))
    # Evidence text participates in identity, so match it before comparing.
    new["evidence"] = "synthetic generation contract"
    path.write_text(json.dumps({"schema_version": 1, "profiles": [legacy, new]}))
    assert ProviderProfiles.load(path).fingerprint() == expected.fingerprint()
    assert loaded.supports_responses_penalties(BASE, "responses", "m")
    assert not loaded.supports_responses_penalties(BASE, "responses", [])
    assert not loaded.supports_responses_penalties(BASE, "responses", "other")
    assert loaded.fingerprint() != ProviderProfiles((record("responses", {"temperature": 0.7}),)).fingerprint()
    with pytest.raises(ValueError, match="duplicate"):
        ProviderProfiles((record("chat", {"temperature": 0.7}), ProviderProfile(**{**legacy, "model": "m"})))


@pytest.mark.parametrize("source", ["chat", "messages", "responses"])
@pytest.mark.parametrize("streaming", [False, True])
def test_defaults_are_actually_sent_and_metadata_reports_them(source, streaming):
    target = "chat" if source == "responses" else "responses"
    registry = ProviderProfiles((record(source, {"temperature": 0.6, "top_p": 0.9}),))
    cfg = GatewayConfig(source, BASE, "synthetic", "local", provider_profiles=registry)
    seen = []

    def upstream(req, *, timeout):
        seen.append(json.loads(req.data))
        return io.BytesIO(reply(source, stream=streaming))

    body = convert_request({"model": "m", "input": "synthetic", "max_output_tokens": 20}, "responses", target)
    body["stream"] = streaming
    with listener(cfg, upstream) as url, request(url, target, json.dumps(body).encode()) as response:
        raw = response.read()
    assert len(seen) == 1 and seen[0]["temperature"] == 0.6 and seen[0]["top_p"] == 0.9
    if target == "responses":
        for snapshot in snapshots(raw) if streaming else [json.loads(raw)]:
            assert snapshot["temperature"] == 0.6 and snapshot["top_p"] == 0.9


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("declared", [False, True])
def test_response_penalties_require_an_exact_profile_before_any_io(streaming, declared):
    registry = ProviderProfiles((record("responses", responses_penalties=True),)) if declared else None
    cfg = GatewayConfig("responses", BASE, "synthetic", "local", provider_profiles=registry)
    seen = []

    def upstream(req, *, timeout):
        seen.append(json.loads(req.data))
        return io.BytesIO(reply("responses", stream=streaming))

    body = {"model": "m", "messages": [{"role": "user", "content": "synthetic"}], "frequency_penalty": 0, "presence_penalty": -0.5, "stream": streaming}
    with listener(cfg, upstream) as url:
        if declared:
            with request(url, "chat", json.dumps(body).encode()) as response:
                assert response.status == 200 and b"invalid_request" not in response.read()
            assert seen[0]["frequency_penalty"] == 0 and seen[0]["presence_penalty"] == -0.5
        else:
            with pytest.raises(HTTPError) as caught:
                request(url, "chat", json.dumps(body).encode())
            assert caught.value.code == 422 and seen == []
            caught.value.close()


@pytest.mark.parametrize("protocol", ["messages", "responses"])
def test_negative_sampling_capability_wins_over_declared_defaults(protocol):
    registry = ProviderProfiles((record(protocol, {"temperature": 0.6}),))
    caps = CapabilityRegistry((CapabilityRecord(BASE, protocol, "m", {"temperature": False}, "2026-11-04", "synthetic negative"),))
    cfg = GatewayConfig(protocol, BASE, "synthetic", "local", provider_profiles=registry, capabilities=caps)
    target = "chat" if protocol == "responses" else "responses"
    body = convert_request({"model": "m", "input": "synthetic", "max_output_tokens": 20}, "responses", target)
    seen = []
    with listener(cfg, lambda *a, **k: seen.append(1)) as url:
        with pytest.raises(HTTPError) as caught:
            request(url, target, json.dumps(body).encode())
        assert caught.value.code == 422 and seen == []
        caught.value.close()


def test_bare_penalty_mapping_remains_explicit_and_messages_is_never_enabled():
    body = {"model": "m", "messages": [{"role": "user", "content": "synthetic"}], "frequency_penalty": 0.5}
    assert convert_request(body, "chat", "responses", responses_penalties=True)["frequency_penalty"] == 0.5
    with pytest.raises(ConversionError):
        convert_request(body, "chat", "responses")
    with pytest.raises(ConversionError):
        convert_request(body, "chat", "messages", responses_penalties=True)
