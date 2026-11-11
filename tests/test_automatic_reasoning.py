import pytest

from protoverta.automatic_reasoning import automatic_reasoning
from protoverta.core import ConversionError, convert_response


@pytest.mark.parametrize("client", ["chat", "messages", "responses"])
def test_native_auto_preserves_provider_reasoning(client):
    assert automatic_reasoning(client, client, "https://openrouter.ai/api/v1") == ("provider_default", False)


@pytest.mark.parametrize("client,upstream", [("chat", "responses"), ("responses", "chat")])
def test_unsigned_public_text_route_does_not_disable_reasoning(client, upstream):
    assert automatic_reasoning(client, upstream, "https://example.test/v1") == ("provider_default", True)


@pytest.mark.parametrize(
    "client,upstream",
    [("messages", "chat"), ("messages", "responses"), ("responses", "messages"), ("chat", "messages")],
)
def test_cross_messages_requests_disable_without_claiming_output_is_filtered(client, upstream):
    assert automatic_reasoning(client, upstream, "https://example.test/v1") == ("disabled", False)
    expected = "disabled" if upstream == "messages" else "openrouter_disabled"
    assert automatic_reasoning(client, upstream, "https://openrouter.ai/api/v1/") == (expected, False)


@pytest.mark.parametrize(
    "url",
    [
        "https://openrouter.ai.evil.test/api/v1",
        "https://example.test/openrouter.ai/api/v1",
        "http://openrouter.ai/api/v1",
        "https://openrouter.ai/other",
        "https://openrouter.ai:8443/api/v1",
    ],
)
def test_vendor_policy_is_exact_endpoint_scoped(url):
    assert automatic_reasoning("messages", "chat", url) == ("disabled", False)


def test_auto_cannot_filter_reasoning_if_provider_ignores_disable():
    _, public = automatic_reasoning("messages", "chat", "https://example.test/v1")
    body = {
        "model": "m",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "answer", "reasoning_content": "reasoning"},
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    }
    with pytest.raises(ConversionError, match="reasoning"):
        convert_response(body, "chat", "messages", public_reasoning=public)
