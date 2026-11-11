import copy

import pytest
from test_discovery import pages

from protoverta.core import ConversionError
from protoverta.discovery import CatalogPolicy, fetch_catalog
from protoverta.provider_profiles import ProviderProfile, ProviderProfiles


def profile(**extra):
    return ProviderProfile(
        "https://example.test/v1",
        "chat",
        "m",
        "2026-10-25",
        "synthetic equivalent wire contract",
        "max_tokens",
        "completion_total",
        **extra,
    )


def test_reasoning_profile_is_exact_reversible_injective_and_never_clamps():
    registry = ProviderProfiles(
        (
            profile(
                reasoning_field="thinking.level",
                reasoning_values={"low": 1, "medium": 2, "high": 3},
                reasoning_semantics="effort_equivalent",
            ),
        )
    )
    body = {"model": "m", "reasoning_effort": "high"}
    original = copy.deepcopy(body)
    assert registry.apply("https://example.test/v1", "chat", body) == {"model": "m", "thinking": {"level": 3}}
    assert body == original
    assert registry.apply("https://other.test/v1", "chat", body) == body
    with pytest.raises(ConversionError):
        registry.apply("https://example.test/v1", "chat", body | {"reasoning_effort": "max"})
    with pytest.raises(ConversionError):
        registry.apply("https://example.test/v1", "chat", body | {"thinking": {"level": 0}})
    with pytest.raises(ValueError):
        profile(
            reasoning_field="thinking.level",
            reasoning_values={"low": 1, "high": 1},
            reasoning_semantics="effort_equivalent",
        )
    assert registry.fingerprint() != ProviderProfiles((profile(),)).fingerprint()


def test_catalog_nested_collection_alternate_id_query_and_numbered_pages():
    requests = []
    policy = CatalogPolicy(
        collection_field="result.models",
        id_field="model_id",
        pagination="numbered",
        query_params=(("kind", "coding"),),
        first_page=0,
    )
    result = fetch_catalog(
        "https://example.test/v1",
        {},
        policy,
        upstream_open=pages(
            [
                {"result": {"models": [{"model_id": "a"}]}, "pagination": {"total_pages": 2}},
                {"result": {"models": [{"model_id": "b"}]}, "pagination": {"total_pages": 2}},
            ],
            requests,
        ),
    )
    assert [item.id for item in result] == ["a", "b"]
    assert requests[0].full_url.endswith("?kind=coding&page=0")
    assert requests[1].full_url.endswith("?kind=coding&page=1")
    result = fetch_catalog(
        "https://example.test/v1", {}, CatalogPolicy(collection_field=None), upstream_open=pages([[{"id": "a"}]])
    )
    assert result[0].id == "a"


@pytest.mark.parametrize(
    "params", [(("after_id", "x"),), (("page", "2"),), (("a", "1"), ("a", "2")), (("secret\n", "x"),)]
)
def test_query_cannot_override_cursors_or_inject_headers(params):
    with pytest.raises(ValueError):
        CatalogPolicy(query_params=params)
