import copy
import io
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from protoverta.core import ConversionError, Protocol
from protoverta.server import GatewayConfig, apply_upstream_reasoning, make_handler


@pytest.mark.parametrize("protocol", list(Protocol))
def test_default_reasoning_preserves_native_payload(protocol):
    payload = {"reasoning": {"effort": "high"}, "thinking": {"type": "adaptive"}}
    assert apply_upstream_reasoning(payload, protocol, "provider_default") is payload


@pytest.mark.parametrize("policy", ["disabled", "openrouter_disabled"])
@pytest.mark.parametrize("protocol", list(Protocol))
def test_disabled_policy_overrides_effort_without_mutating_input(protocol, policy):
    payload = {"model": "m", "reasoning_effort": "high", "reasoning": {"effort": "high"}, "thinking": {"type": "enabled", "budget_tokens": 100}, "output_config": {"effort": "high", "format": {"type": "json_schema"}}}
    original = copy.deepcopy(payload)
    result = apply_upstream_reasoning(payload, protocol, policy)
    assert payload == original
    if protocol == Protocol.MESSAGES:
        assert result["thinking"] == {"type": "disabled"}
        assert result["output_config"] == {"format": {"type": "json_schema"}}
        assert "reasoning" not in result and "reasoning_effort" not in result
    elif policy == "openrouter_disabled":
        assert result["reasoning"] == {"enabled": False}
        assert "reasoning_effort" not in result
    elif protocol == Protocol.CHAT:
        assert result["reasoning_effort"] == "none"
        assert "reasoning" not in result
    else:
        assert result["reasoning"] == {"effort": "none"}


def test_unknown_reasoning_policy_fails_at_configuration():
    with pytest.raises(ValueError, match="upstream_reasoning"):
        GatewayConfig("chat", "https://example.test/v1", "key", "local", upstream_reasoning="hide")


def test_disabled_messages_preserves_history_and_rejects_invalid_output_config():
    history = [{"role": "assistant", "content": [{"type": "thinking", "thinking": "opaque", "signature": "signed"}]}]
    result = apply_upstream_reasoning({"messages": history, "output_config": {"effort": "high"}}, Protocol.MESSAGES, "disabled")
    assert result["messages"] is history
    assert "output_config" not in result
    with pytest.raises(ConversionError, match="output_config"):
        apply_upstream_reasoning({"output_config": None}, Protocol.MESSAGES, "disabled")


@pytest.mark.parametrize("upstream", list(Protocol))
@pytest.mark.parametrize("inbound", list(Protocol))
def test_gateway_applies_reasoning_policy_after_conversion(upstream, inbound):
    captured = {}

    def open_upstream(request, *, timeout):
        captured.update(json.loads(request.data))
        # A native error is enough to inspect the actual outbound request.
        raise HTTPError(request.full_url, 400, "unsupported", {}, io.BytesIO(b'{"error":{"message":"fixture"}}'))

    config = GatewayConfig(upstream, "https://example.test/v1", "key", "local", upstream_reasoning="openrouter_disabled")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=open_upstream))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = {"model": "m", "input": "Hello", "max_output_tokens": 32} if inbound == Protocol.RESPONSES else {"model": "m", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 32}
        endpoint = "chat/completions" if inbound == Protocol.CHAT else inbound.value
        request = Request(f"http://127.0.0.1:{server.server_port}/v1/{endpoint}", data=json.dumps(payload).encode(), headers={"Authorization": "Bearer local"})
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=3)
        assert error.value.code == 400
        error.value.close()
        if upstream == Protocol.MESSAGES:
            assert captured["thinking"] == {"type": "disabled"}
        else:
            assert captured["reasoning"] == {"enabled": False}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
