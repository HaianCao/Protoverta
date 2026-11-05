import io
import json
from dataclasses import replace
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post

from protoverta.evidence_identity import route_identity
from protoverta.server import GatewayConfig


def test_openrouter_route_is_session_local_pinned_and_default_off():
    base = GatewayConfig("chat", "https://openrouter.ai/api/v1", "fixture", "local")
    pinned = replace(base, openrouter_provider="fixture/endpoint")
    bodies = []

    def upstream(request, timeout):
        bodies.append(json.loads(request.data))
        return io.BytesIO(b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}')

    for item, count in ((pinned, 2), (pinned, 1), (base, 1)):
        with listener(item, upstream) as url:
            for _ in range(count):
                with post(url, {"model": "m", "input": "synthetic"}):
                    pass
    assert bodies[0]["provider"] == {"only": ["fixture/endpoint"], "order": ["fixture/endpoint"], "allow_fallbacks": False, "require_parameters": True}
    assert bodies[0]["session_id"] == bodies[1]["session_id"] != bodies[2]["session_id"]
    assert "provider" not in bodies[3] and "session_id" not in bodies[3]
    assert route_identity(base) != route_identity(pinned)


@pytest.mark.parametrize(
    "prefix,protocol", [("https://example.invalid/v1", "chat"), ("https://openrouter.ai/api/v1", "responses"), ("https://openrouter.ai/other", "chat")]
)
def test_routing_never_guesses_provider_or_protocol_support(prefix, protocol):
    with pytest.raises(ValueError):
        GatewayConfig(protocol, prefix, "fixture", "local", openrouter_provider="fixture")


def test_native_request_cannot_override_session_route():
    item = GatewayConfig("chat", "https://openrouter.ai/api/v1", "fixture", "local", openrouter_provider="fixture")
    with listener(item, lambda *_: pytest.fail("conflict must not reach upstream")) as url:
        with pytest.raises(HTTPError) as error:
            post(url.replace("/responses", "/chat/completions"), {"model": "m", "messages": [], "provider": {"allow_fallbacks": True}})
        assert error.value.code == 422
        error.value.close()
