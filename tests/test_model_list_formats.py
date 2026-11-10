import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from test_gateway_capabilities import listener

from protoverta.model_snapshot import ModelSnapshot, SnapshotModel
from protoverta.server import GatewayConfig

BASE = "https://fixture.test/v1"


def no_upstream(*args, **kwargs):
    pytest.fail("listing cannot call upstream")


def test_messages_shape_and_bidirectional_paging_with_unknown_release():
    snapshot = ModelSnapshot.from_ids(BASE, "chat", tuple("m" + str(i) for i in range(25)))
    config = GatewayConfig("chat", BASE, "key", "local", model_snapshot=snapshot)
    with listener(config, no_upstream) as endpoint:
        url = endpoint.replace("/responses", "/models")
        with urlopen(Request(url, headers={"x-api-key": "local", "anthropic-version": "2023-06-01"}), timeout=3) as response:
            first = json.load(response)
        assert len(first["data"]) == 20 and first["has_more"] is True and "object" not in first
        assert first["data"][0] == {"id": "m0", "display_name": "m0", "type": "model", "created_at": "1970-01-01T00:00:00Z"}
        assert snapshot.page("before_id=m20&limit=3&format=messages")["first_id"] == "m17"
        assert snapshot.page("before_id=m1&limit=3&format=messages")["has_more"] is False
        # Claude Code 2.1.248+ sends both credentials; both matching is accepted.
        with urlopen(Request(url, headers={"x-api-key": "local", "Authorization": "Bearer local"}), timeout=3) as response:
            assert response.status == 200
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(url, headers={"x-api-key": "local", "Authorization": "Bearer other"}), timeout=3)
        assert exc.value.code == 401
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(url, headers={"x-api-key": "wrong", "anthropic-version": "2023-06-01"}), timeout=3)
        assert json.load(exc.value)["type"] == "error"


def test_openai_projection_requires_actual_metadata_not_fabricated_values():
    unknown = ModelSnapshot.from_ids(BASE, "chat", ("m",))
    with pytest.raises(ValueError, match="reported creation"):
        unknown.page("format=openai")
    with pytest.raises(ValueError, match="reported lifecycle"):
        unknown.page("format=messages", messages_api_version="2026-10-21")
    reported = ModelSnapshot(BASE, "chat", (SnapshotModel("m", "Display", 123, "provider-owner"),))
    assert reported.page("format=openai")["data"] == [{"id": "m", "object": "model", "created": 123, "owned_by": "provider-owner"}]


@pytest.mark.parametrize("query", ["format=unknown", "after_id=m&before_id=m", "before_id=", "format=messages&format=openai"])
def test_invalid_projection_or_cursor_query(query):
    with pytest.raises(ValueError):
        ModelSnapshot.from_ids(BASE, "chat", ("m",)).page(query)


@pytest.mark.parametrize("metadata", [{"created": True}, {"created": -1}, {"created": 253402300800}, {"owned_by": "\n"}])
def test_invalid_reported_metadata_is_not_advertised(metadata):
    with pytest.raises(ValueError):
        SnapshotModel("m", "Display", **metadata)


def test_reported_release_dates_do_not_depend_on_windows_crt_timestamp_range():
    model = SnapshotModel("m", "Display", created=253402300799)
    assert model.wire("messages")["created_at"] == "9999-12-31T23:59:59Z"


def test_pinned_sdk_model_list_parsing_is_strictly_validated():
    pytest.importorskip("openai")
    pytest.importorskip("anthropic")
    from anthropic import Anthropic
    from anthropic.types import ModelInfo
    from openai import OpenAI
    from openai.types import Model

    snapshot = ModelSnapshot(BASE, "chat", (SnapshotModel("vendor/model:free", "Human name", 123, "fixture-owner", "active"),))
    assert Model.model_validate(snapshot.models[0].wire("openai")).id == "vendor/model:free"
    assert ModelInfo.model_validate(snapshot.models[0].wire("messages", messages_api_version="2026-10-21")).capabilities is None
    config = GatewayConfig("chat", BASE, "key", "local", model_snapshot=snapshot)
    with listener(config, no_upstream) as endpoint:
        base = endpoint.removesuffix("/responses")
        with OpenAI(api_key="local", base_url=base, max_retries=0) as client:
            rows = client.models.list(extra_query={"format": "openai"}).data
            assert Model.model_validate(rows[0].model_dump()).id == "vendor/model:free"
        with Anthropic(api_key="local", base_url=base.removesuffix("/v1"), max_retries=0, default_headers={"anthropic-version": "2026-10-21"}) as client:
            rows = client.models.list().data
            assert ModelInfo.model_validate(rows[0].model_dump()).display_name == "Human name"


def test_missing_vendor_metadata_is_explicit_nonretryable_422():
    config = GatewayConfig("chat", BASE, "key", "local", model_snapshot=ModelSnapshot.from_ids(BASE, "chat", ("m",)))
    with listener(config, no_upstream) as endpoint:
        for query, version in (("?format=openai", None), ("", "2026-10-21")):
            headers = {"Authorization": "Bearer local"}
            if version:
                headers["anthropic-version"] = version
            with pytest.raises(HTTPError) as exc:
                urlopen(Request(endpoint.replace("/responses", "/models") + query, headers=headers), timeout=3)
            assert exc.value.code == 422 and exc.value.headers["x-should-retry"] == "false"
