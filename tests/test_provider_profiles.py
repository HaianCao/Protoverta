import copy
import io
import json

import pytest
from test_gateway_capabilities import listener, post

from protoverta import ConversionError
from protoverta.provider_profiles import ProviderProfile, ProviderProfiles
from protoverta.server import GatewayConfig


def profile(**options):
    return ProviderProfile(**{"base_url": "https://example.test/v1", "protocol": "chat", "model": "m", "checked_at": "2026-10-24", "evidence": "synthetic fixture; not a real provider certification", "chat_token_field": "max_tokens", "token_budget_semantics": "completion_total", **options})


def test_exact_scoped_field_mapping_keeps_options_history_and_input_unchanged():
    profiles = ProviderProfiles((profile(),))
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "max_completion_tokens": 10, "reasoning_effort": "high", "tools": [], "top_p": 0.7}
    before = copy.deepcopy(body)
    output = profiles.apply("https://EXAMPLE.test/v1/", "chat", body)
    assert output == {**{key: value for key, value in body.items() if key != "max_completion_tokens"}, "max_tokens": 10}
    assert body == before
    assert profiles.apply("https://other.test/v1", "chat", body) == body
    assert profiles.apply("https://example.test/v1", "chat", {**body, "model": "other"}) == {**body, "model": "other"}
    with pytest.raises(ConversionError, match="ambiguous"):
        profiles.apply("https://example.test/v1", "chat", {**body, "max_tokens": 8})


@pytest.mark.parametrize("options", [{"protocol": "messages"}, {"evidence": ""}, {"chat_token_field": "other"}, {"token_budget_semantics": "unknown"}, {"checked_at": "invalid"}])
def test_profiles_require_narrow_evidence_and_known_semantics(options):
    with pytest.raises(ValueError):
        profile(**options)


def test_profile_file_and_gateway_use_final_wire_field(tmp_path):
    record = profile()
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"schema_version": 1, "profiles": [vars(record)]}), encoding="utf-8")
    profiles = ProviderProfiles.load(path)
    captured = []

    def upstream(request, *, timeout):
        captured.append(json.loads(request.data))
        return io.BytesIO(b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}')

    config = GatewayConfig("chat", "https://example.test/v1", "key", "local", provider_profiles=profiles)
    with listener(config, upstream) as url, post(url, {"model": "m", "input": "Hi", "max_output_tokens": 9}) as response:
        assert response.status == 200
    assert captured[0]["max_tokens"] == 9 and "max_completion_tokens" not in captured[0]
