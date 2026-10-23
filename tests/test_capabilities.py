import json

import pytest

from protoverta import CapabilityRecord, CapabilityRegistry, ConversionError, request_features
from protoverta.server import GatewayConfig


def registry(**features):
    return CapabilityRegistry((CapabilityRecord("https://example.test/api/v1/", "chat", "model-a", features, "2026-10-23", "synthetic test evidence"),))


def test_capabilities_are_exact_and_unknown_is_distinct_from_unsupported():
    records = registry(text=True, tools=False)
    report = records.assess("https://EXAMPLE.test/api/v1", "chat", "model-a", {"text", "tools", "streaming"})
    assert report.supported == ("text",)
    assert report.unsupported == ("tools",)
    assert report.unknown == ("streaming",)
    with pytest.raises(ConversionError, match="unsupported=tools"):
        report.require()
    for prefix, protocol, model in (("https://other.test/api/v1", "chat", "model-a"), ("https://example.test/api", "chat", "model-a"), ("https://example.test/api/v1", "messages", "model-a"), ("https://example.test/api/v1", "chat", "model-a:free")):
        assert records.assess(prefix, protocol, model, {"text"}).unknown == ("text",)


def test_unknown_capabilities_require_explicit_strict_policy():
    records = registry(text=True)
    body = {"model": "model-a", "stream": True}
    assert records.preflight("https://example.test/api/v1", "chat", body).unknown == ("streaming",)
    with pytest.raises(ConversionError, match="unverified=streaming"):
        records.preflight("https://example.test/api/v1", "chat", body, require_verified=True)
    with pytest.raises(ValueError, match="registry"):
        GatewayConfig("chat", "https://example.test/v1", "key", "token", require_verified_capabilities=True)


def test_record_is_immutable_and_requires_evidence():
    features = {"text": True}
    entry = CapabilityRecord("https://example.test/v1", "chat", "m", features, "2026-10-23", "probe")
    features["text"] = False
    assert entry.features["text"] is True
    with pytest.raises(TypeError):
        entry.features["text"] = False
    with pytest.raises(ValueError):
        CapabilityRecord("https://example.test/v1", "chat", "m", {"text": "yes"}, "2026-10-23", "probe")
    with pytest.raises(ValueError):
        CapabilityRecord("https://example.test/v1", "chat", "m", {}, "not-a-date", "probe")
    with pytest.raises(ValueError):
        CapabilityRecord("https://example.test/v1", "chat", "m", {}, "2026-10-23", "")
    with pytest.raises(ValueError, match="duplicate"):
        CapabilityRegistry((entry, entry))


def test_registry_load_round_trip_and_invalid_fields(tmp_path):
    body = {"schema_version": 1, "models": [{"base_url": "https://example.test/v1", "protocol": "messages", "model": "m", "features": {"text": True, "json_schema": False}, "checked_at": "2026-10-23", "evidence": "probe"}]}
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    report = CapabilityRegistry.load(path).assess("https://example.test/v1", "messages", "m", {"text", "json_schema"})
    assert report.supported == ("text",) and report.unsupported == ("json_schema",)
    body["models"][0]["features"]["unknown_feature"] = True
    with pytest.raises(ValueError):
        CapabilityRegistry.from_dict(body)


@pytest.mark.parametrize("protocol,body,expected", [
    ("chat", {"model": "m", "stream": True, "tools": [{"type": "function"}], "tool_choice": {"type": "function", "function": {"name": "add"}}, "response_format": {"type": "json_schema", "json_schema": {"strict": True}}}, {"text", "streaming", "tools", "forced_tool_choice", "json_schema", "strict_json_schema"}),
    ("responses", {"model": "m", "reasoning": {"effort": "low"}, "text": {"format": {"type": "json_object"}}}, {"text", "reasoning_effort", "json_object"}),
    ("messages", {"model": "m", "messages": [{"role": "user", "content": [{"type": "image"}]}, {"role": "system", "content": "new"}], "output_config": {"format": {"type": "json_schema"}}}, {"text", "images", "mid_conversation_system", "json_schema", "strict_json_schema"}),
])
def test_outbound_feature_detection(protocol, body, expected):
    assert request_features(body, protocol) == expected


def test_image_schema_property_is_not_a_multimodal_request():
    assert "images" not in request_features({"tools": [{"parameters": {"properties": {"type": "image"}}}]}, "chat")


@pytest.mark.parametrize("url", ["https://user:password@example.test/v1", "https://example.test/v1?key=secret", "http://example.test/v1", "https://example.test/v1/responses"])
def test_registry_rejects_credential_urls_and_endpoint_suffixes(url):
    with pytest.raises(ValueError):
        CapabilityRecord(url, "chat", "m", {}, "2026-10-23", "probe")
