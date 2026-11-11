"""Independent strict model-list parsing and Messages auto-pagination."""

import pytest
from anthropic import Anthropic
from anthropic.types import ModelInfo
from openai import APIStatusError, OpenAI
from openai.types import Model

from protoverta.model_snapshot import ModelSnapshot, SnapshotModel
from protoverta.server import GatewayConfig, gateway_session


def config(reported=True):
    snapshot = ModelSnapshot(
        "https://fixture.test/v1",
        "chat",
        tuple(
            SnapshotModel(mid, "Fixture " + mid, 123, "fixture-owner", "active")
            if reported
            else SnapshotModel(mid, mid)
            for mid in ("vendor/a:free", "vendor/b:free")
        ),
    )
    return GatewayConfig("chat", "https://fixture.test/v1", "fake-key", "local", model_snapshot=snapshot)


def test_openai_model_list_requires_and_preserves_reported_fields():
    gateway = config()
    with (
        gateway_session(gateway) as origin,
        OpenAI(api_key="local", base_url=origin + "/v1", max_retries=0, timeout=3) as client,
    ):
        rows = client.models.list(extra_query={"format": "openai"}).data
        assert [Model.model_validate(row.model_dump()).id for row in rows] == ["vendor/a:free", "vendor/b:free"]
        assert all(row.created == 123 and row.owned_by == "fixture-owner" for row in rows)


def test_messages_strict_fields_and_real_sdk_auto_pagination():
    gateway = config()
    with (
        gateway_session(gateway) as origin,
        Anthropic(
            api_key="local",
            base_url=origin,
            max_retries=0,
            timeout=3,
            default_headers={"anthropic-version": "2026-10-21"},
        ) as client,
    ):
        rows = list(client.models.list(limit=1))
        assert [ModelInfo.model_validate(row.model_dump()).id for row in rows] == ["vendor/a:free", "vendor/b:free"]
        assert all(row.capabilities is None and row.max_input_tokens is None for row in rows)
        for item in gateway.model_snapshot.models:
            assert (
                ModelInfo.model_validate(item.wire("messages", messages_api_version="2026-10-21")).lifecycle == "active"
            )


def test_unknown_openai_metadata_fails_without_retry_or_invented_values():
    observations = []
    with (
        gateway_session(config(False), observer=observations.append) as origin,
        OpenAI(api_key="local", base_url=origin + "/v1", max_retries=0, timeout=3) as client,
    ):
        with pytest.raises(APIStatusError) as exc:
            client.models.list(extra_query={"format": "openai"})
        assert exc.value.status_code == 422
    assert len(observations) == 1 and observations[0].outcome == "unsupported_feature"
