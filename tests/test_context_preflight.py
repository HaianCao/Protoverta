import copy
import io
from dataclasses import replace
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post

from protoverta.capabilities import CapabilityRecord, CapabilityRegistry
from protoverta.context import TokenCount, assess_context
from protoverta.errors import ConversionError
from protoverta.server import GatewayConfig


def request():
    return {
        "model": "m",
        "messages": [{"role": "system", "content": "rules"}, {"role": "user", "content": "hello 🌏"}],
        "max_tokens": 10,
        "tools": [{"type": "function", "function": {"name": "f", "parameters": {"type": "object", "properties": {}}}}],
    }


def test_estimate_is_labelled_and_includes_tools_history_without_mutating_request():
    body = request()
    before = copy.deepcopy(body)
    result = assess_context(body, "chat", 10000)
    assert result.verdict == "within-estimate" and not result.exact
    assert result.input_tokens > len("hello 🌏".encode()) and body == before
    assert result.headers()["X-Protoverta-Context-Count-Kind"] == "utf8-json-byte-estimate"
    assert assess_context(body, "chat", 1).verdict == "over-estimate"  # Report only.
    with pytest.raises(ConversionError, match="over-estimate"):
        assess_context(body, "chat", 1, policy="reject_estimate")


@pytest.mark.parametrize("missing", ["limit", "output", "image"])
def test_unknown_inputs_never_get_a_false_fit_claim(missing):
    body = request()
    if missing == "output":
        body.pop("max_tokens")
    if missing == "image":
        body["messages"][-1]["content"] = [{"type": "image_url", "image_url": {"url": "https://example.test/image.png"}}]
    result = assess_context(body, "chat", None if missing == "limit" else 10000)
    assert result.verdict == "unknown" and not result.exact
    with pytest.raises(ConversionError, match="unknown"):
        assess_context(body, "chat", None if missing == "limit" else 10000, policy="reject_estimate")


def test_exact_callback_is_explicit_trusted_and_cannot_mutate_upstream_body():
    body = request()

    def counter(payload, protocol):
        assert protocol == "chat" and payload["tools"]
        payload.clear()
        return TokenCount(20, exact=True)

    assert assess_context(body, "chat", 30, policy="exact", counter=counter).verdict == "within-exact"
    assert body["model"] == "m"
    with pytest.raises(ConversionError, match="over-exact"):
        assess_context(body, "chat", 29, policy="exact", counter=counter)
    with pytest.raises(ConversionError, match="verified operator"):
        assess_context(body, "chat", 1000, policy="exact")


def test_callback_failures_are_sanitized_and_bad_count_types_rejected():
    def failed(body, protocol):
        raise RuntimeError("private-key/private-path")

    with pytest.raises(ConversionError) as error:
        assess_context(request(), "chat", 1000, counter=failed)
    assert "private" not in str(error.value)
    with pytest.raises(ConversionError):
        assess_context(request(), "chat", 1000, counter=lambda *_: 20)
    for number in (-1, True, 1.2):
        with pytest.raises(ValueError):
            TokenCount(number)


@pytest.mark.parametrize(("policy", "status", "calls"), [("off", 200, 1), ("estimate", 200, 1), ("reject_estimate", 422, 0)])
def test_gateway_context_preflight_is_opt_in_and_rejection_never_calls_upstream(policy, status, calls):
    record = CapabilityRecord("https://example.test/v1", "chat", "m", {}, "2026-10-25", "synthetic", context_window_tokens=1)
    config = replace(GatewayConfig("chat", "https://example.test/v1", "key", "local"), context_preflight=policy, capabilities=CapabilityRegistry((record,)))
    received = []

    def upstream(request, timeout):
        received.append(1)
        return io.BytesIO(
            b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}'
        )

    with listener(config, upstream) as url:
        if status == 200:
            with post(url, {"model": "m", "input": "hello", "max_output_tokens": 10}) as response:
                assert response.status == 200
                assert ("X-Protoverta-Context-Verdict" in response.headers) == (policy != "off")
        else:
            with pytest.raises(HTTPError) as error:
                post(url, {"model": "m", "input": "hello", "max_output_tokens": 10})
            assert error.value.code == 422
            error.value.close()
    assert len(received) == calls


@pytest.mark.parametrize("kind", ["input_audio", "input_video", "input_file", "document", "image_url"])
def test_media_never_gets_a_fake_byte_token_count(kind):
    body = request() | {"messages": [{"role": "user", "content": [{"type": kind, "data": "synthetic"}]}]}
    result = assess_context(body, "chat", 1000)
    assert result.input_tokens is None and result.verdict == "unknown"
    with pytest.raises(ConversionError, match="unknown"):
        assess_context(body, "chat", 1000, policy="reject_estimate")


def test_tool_schema_field_names_are_not_media():
    body = request() | {
        "tools": [{"type": "function", "function": {"name": "image", "parameters": {"type": "object", "properties": {"type": {"const": "image"}}}}}]
    }
    assert assess_context(body, "chat", 10000).input_tokens is not None


def test_callback_estimate_is_not_mislabelled_as_byte_count():
    result = assess_context(request(), "chat", 1000, counter=lambda *_: TokenCount(10))
    assert result.headers()["X-Protoverta-Context-Count-Kind"] == "operator-estimate"


def test_native_chat_audio_history_is_unknown_without_operator_counter():
    body = request() | {"messages": [{"role": "assistant", "audio": {"id": "synthetic"}}]}
    assert assess_context(body, "chat", 1000).input_tokens is None
