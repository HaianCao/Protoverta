from protoverta.capabilities import (
    CapabilityRecord,
    CapabilityRegistry,
    RouteCapabilityRecord,
    route_policies,
)
from protoverta.selection_status import selection_status
from protoverta.server import GatewayConfig


def test_discovery_is_not_verification_and_route_requires_own_evidence():
    gateway = GatewayConfig("chat", "https://fixture.test/v1", "key", "local", client_profile="codex")
    record = CapabilityRecord(
        gateway.upstream_base_url, "chat", "m", {"text": True, "streaming": True}, "2026-11-04", "fixture"
    )

    def status(registry, model="m", **options):
        return selection_status(
            registry,
            gateway.upstream_base_url,
            "chat",
            model,
            source_protocol="responses",
            client_profile="codex",
            **options,
        )["status"]

    assert status(None) == "unverified"
    assert status(CapabilityRegistry((record,))) == "unverified"
    route = RouteCapabilityRecord(
        gateway.upstream_base_url,
        "chat",
        "m",
        "responses",
        True,
        "codex",
        route_policies(gateway),
        record.features,
        "2026-11-04",
        "fixture",
        "fixture",
    )
    registry = CapabilityRegistry((record,), (route,))
    assert status(registry) == "verified"
    assert status(registry, "other") == "unverified"
    assert status(registry, freshness_required=True) == "unverified"
    assert status(registry, overrides={"namespace_tools": "flatten"}) == "unverified"
    negative = CapabilityRecord(gateway.upstream_base_url, "chat", "m", {"streaming": False}, "2026-11-04", "fixture")
    assert status(CapabilityRegistry((negative,))) == "unsupported"
    assert status(CapabilityRegistry((negative,)), freshness_required=True) == "unsupported"
