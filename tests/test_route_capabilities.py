import io
import json
from dataclasses import replace
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post

from protoverta.capabilities import (
    CapabilityRegistry,
    RouteCapabilityRecord,
    route_policies,
)
from protoverta.server import GatewayConfig


def record(**changes):
    config = GatewayConfig("chat", "https://example.test/v1", "key", "local")
    values = {
        "base_url": config.upstream_base_url,
        "protocol": "chat",
        "model": "m",
        "source_protocol": "responses",
        "streaming": False,
        "client_profile": "strict",
        "policies": route_policies(config),
        "features": {"text": True},
        "checked_at": "2026-10-25",
        "evidence": "synthetic local fixture",
        "verified_with": "fixture core/client versions",
    }
    return RouteCapabilityRecord(**(values | changes))


def assess(registry, **changes):
    values = {
        "base_url": "https://example.test/v1",
        "protocol": "chat",
        "model": "m",
        "features": {"text"},
        "source_protocol": "responses",
        "streaming": False,
        "client_profile": "strict",
        "policies": route_policies(GatewayConfig("chat", "https://example.test/v1", "k", "t")),
    }
    return registry.assess_route(**(values | changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"source_protocol": "messages"},
        {"protocol": "responses"},
        {"streaming": True},
        {"client_profile": "codex"},
        {"model": "other"},
        {"base_url": "https://other.test/v1"},
    ],
)
def test_route_evidence_does_not_leak_to_a_different_selector(changes):
    registry = CapabilityRegistry(routes=(record(),))
    assert assess(registry).supported == ("text",)
    assert assess(registry, **changes).unknown == ("text",)


def test_policy_fingerprint_and_false_evidence_are_explicit():
    registry = CapabilityRegistry(routes=(record(features={"text": False}),))
    assert assess(registry).unsupported == ("text",)
    policies = dict(record().policies, upstream_reasoning="disabled")
    assert assess(registry, policies=policies).unknown == ("text",)
    with pytest.raises(ValueError):
        record(policies={"public_reasoning": True})
    with pytest.raises(ValueError):
        record(policies=dict(record().policies, public_reasoning=1))
    with pytest.raises(ValueError):
        CapabilityRegistry(routes=(record(), record()))


def test_schema_three_routes_and_older_registries_remain_separate():
    route = record()
    fields = {name: getattr(route, name) for name in route.__dataclass_fields__}
    fields.update(policies=dict(route.policies), features=dict(route.features))
    body = json.loads(json.dumps({"schema_version": 3, "models": [], "routes": [fields]}))
    assert assess(CapabilityRegistry.from_dict(body)).supported == ("text",)
    for version in (1, 2):
        old = CapabilityRegistry.from_dict({"schema_version": version, "models": []})
        assert assess(old).unknown == ("text",)


@pytest.mark.parametrize(
    ("routes", "required", "expected"),
    [([], False, 200), ([], True, 422), ([record()], True, 200), ([record(features={"text": False})], False, 422)],
)
def test_gateway_checks_exact_route_before_upstream(routes, required, expected):
    config = replace(
        GatewayConfig("chat", "https://example.test/v1", "key", "local"),
        capabilities=CapabilityRegistry(routes=tuple(routes)),
        require_verified_routes=required,
    )
    calls = []

    def upstream(request, timeout):
        calls.append(1)
        return io.BytesIO(
            b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}'
        )

    with listener(config, upstream) as url:
        if expected == 200:
            with post(url, {"model": "m", "input": "hi"}) as response:
                assert response.status == 200
        else:
            with pytest.raises(HTTPError) as error:
                post(url, {"model": "m", "input": "hi"})
            error.value.close()
    assert len(calls) == (1 if expected == 200 else 0)
