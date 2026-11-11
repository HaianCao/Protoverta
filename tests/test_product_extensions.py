import io
import json
from dataclasses import replace
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post
from test_route_capabilities import assess, record

from protoverta import ConversionError, convert_request
from protoverta.capabilities import CapabilityRecord, CapabilityRegistry
from protoverta.evidence_identity import route_identity
from protoverta.observability import RequestObservation
from protoverta.prompt_cache import CacheCheckpoint
from protoverta.provider_profiles import ProviderProfile, ProviderProfiles
from protoverta.server import GatewayConfig
from protoverta.session_diagnostics import diagnostic_journal, latest_diagnostics


def test_evidence_identity_and_expiry_never_reuse_stale_positive_claims():
    registry = CapabilityRegistry(routes=(record(identity="a" * 64),))
    assert registry.assess_route(
        "https://example.test/v1",
        "chat",
        "m",
        {"text"},
        source_protocol="responses",
        streaming=False,
        client_profile="strict",
        policies=dict(record().policies),
        current_identity="b" * 64,
    ).unknown == ("text",)
    assert assess(CapabilityRegistry(routes=(record(checked_at="2000-01-01"),)), max_age_days=1).unknown == ("text",)
    first = GatewayConfig("chat", "https://example.test/v1", "fixture", "local")
    second = replace(first, provider_profiles=ProviderProfiles())
    assert route_identity(first) != route_identity(second)
    assert route_identity(first) == route_identity(replace(first, upstream_api_key="different"))


def test_diagnostic_journal_fixed_fields_retention_and_tamper_filter(tmp_path):
    observation = RequestObservation("a" * 32, "responses", "chat", 200, "invalid_stream", 12, True)
    with diagnostic_journal(tmp_path) as observe:
        for _ in range(1100):
            observe(observation)
    result = latest_diagnostics(tmp_path)
    assert result["limited"] and len(result["records"]) == 100
    assert result["records"][0]["outcome"] == "invalid_stream"
    path = next(tmp_path.glob("session-*.jsonl"))
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"private": "must not surface"}) + "\n")
    assert "private" not in json.dumps(latest_diagnostics(tmp_path))


def test_explicit_cache_contract_preserves_only_certified_boundary():
    profile = ProviderProfile(
        "https://example.test/v1",
        "chat",
        "m",
        "2026-10-25",
        "synthetic equivalent extension",
        "max_tokens",
        "completion_total",
        prompt_cache_semantics="anthropic_ephemeral_system_end",
    )
    config = replace(
        GatewayConfig("chat", "https://example.test/v1", "fixture", "local", client_profile="claude"),
        provider_profiles=ProviderProfiles((profile,)),
        prompt_caching="ephemeral",
    )
    body = {
        "model": "m",
        "max_tokens": 32,
        "system": [{"type": "text", "text": "prefix", "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
        "messages": [{"role": "user", "content": "hello"}],
    }
    captured = []

    def upstream(request, timeout):
        captured.append(json.loads(request.data))
        return io.BytesIO(
            b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}'
        )

    with listener(config, upstream) as url, post(url.replace("/responses", "/messages"), body) as response:
        assert response.status == 200
    assert captured[0]["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    with pytest.raises(ConversionError):
        CacheCheckpoint().prepare(body, "messages", replace(config, provider_profiles=ProviderProfiles()))


def test_native_compaction_is_forwarded_and_cross_protocol_compaction_rejected():
    result = {
        "object": "response.compaction",
        "output": [{"type": "compaction", "encrypted_content": "opaque-fixture"}],
    }
    requests = []

    def upstream(request, timeout):
        requests.append(request.full_url)
        return io.BytesIO(json.dumps(result).encode())

    for protocol in ("responses", "chat"):
        config = GatewayConfig(protocol, "https://example.test/v1", "fixture", "local")
        with listener(config, upstream) as url:
            if protocol == "responses":
                with post(url + "/compact", {"model": "m", "input": "synthetic"}) as response:
                    assert json.load(response) == result
            else:
                with pytest.raises(HTTPError) as error:
                    post(url + "/compact", {"model": "m", "input": "synthetic"})
                assert error.value.code == 422
                error.value.close()
    assert requests == ["https://example.test/v1/responses/compact"]


def test_audio_and_hosted_state_are_not_relabelled_as_text_or_functions():
    for content in ([{"type": "input_audio", "input_audio": {"data": "synthetic", "format": "wav"}}],):
        with pytest.raises(ConversionError):
            convert_request(
                {"model": "m", "max_tokens": 32, "messages": [{"role": "user", "content": content}]}, "chat", "messages"
            )
    with pytest.raises(ConversionError):
        convert_request({"model": "m", "input": "hello", "tools": [{"type": "web_search"}]}, "responses", "chat")


def test_current_evidence_policy_rejects_stale_route_before_io():
    route = record(identity="a" * 64)
    config = replace(
        GatewayConfig("chat", "https://example.test/v1", "fixture", "local"),
        capabilities=CapabilityRegistry(routes=(route,)),
        require_current_evidence=True,
    )
    with listener(config, lambda *_: pytest.fail("stale evidence must not call upstream")) as url:
        with pytest.raises(HTTPError) as error:
            post(url, {"model": "m", "input": "synthetic"})
        assert error.value.code == 422
        error.value.close()


def test_vendor_effort_alias_cannot_hide_explicit_unsupported_capability():
    profile = ProviderProfile(
        "https://example.test/v1",
        "chat",
        "m",
        "2026-10-25",
        "synthetic",
        "max_tokens",
        "completion_total",
        reasoning_field="thinking.level",
        reasoning_values={"high": 3},
        reasoning_semantics="effort_equivalent",
    )
    registry = CapabilityRegistry(
        (
            CapabilityRecord(
                "https://example.test/v1",
                "chat",
                "m",
                {"reasoning_effort": False},
                "2026-10-25",
                "synthetic negative evidence",
            ),
        )
    )
    config = GatewayConfig(
        "chat",
        "https://example.test/v1",
        "fixture",
        "local",
        provider_profiles=ProviderProfiles((profile,)),
        capabilities=registry,
    )
    with listener(config, lambda *_: pytest.fail("must reject before upstream")) as url:
        with pytest.raises(HTTPError) as error:
            post(url, {"model": "m", "input": "hello", "reasoning": {"effort": "high"}})
        assert error.value.code == 422
        error.value.close()


@pytest.mark.parametrize(
    "extra",
    [
        {"tools": [{"type": "web_search"}]},
        {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "input_audio", "input_audio": {"data": "synthetic", "format": "wav"}}],
                }
            ]
        },
        {
            "messages": [
                {"role": "user", "content": [{"type": "video_url", "video_url": "https://example.invalid/video"}]}
            ]
        },
    ],
)
def test_text_and_function_evidence_do_not_certify_native_media_or_hosted_tools(extra):
    registry = CapabilityRegistry(
        (
            CapabilityRecord(
                "https://example.test/v1",
                "chat",
                "m",
                {"text": True, "tools": True},
                "2026-10-25",
                "synthetic text/function only",
            ),
        )
    )
    config = GatewayConfig(
        "chat", "https://example.test/v1", "fixture", "local", capabilities=registry, require_verified_capabilities=True
    )
    body = {"model": "m", "messages": [{"role": "user", "content": "synthetic"}]} | extra
    with listener(config, lambda *_: pytest.fail("unverified native feature must not call upstream")) as url:
        with pytest.raises(HTTPError) as error:
            post(url.replace("/responses", "/chat/completions"), body)
        assert error.value.code == 422
        error.value.close()
