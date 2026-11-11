import io
import json
from urllib.error import HTTPError, URLError

import pytest
from test_gateway_capabilities import listener, post

from protoverta.failures import gateway_failure
from protoverta.server import GatewayConfig


@pytest.mark.parametrize("target", ["chat", "messages", "responses"])
@pytest.mark.parametrize(
    "status,kind",
    [
        (400, "invalid_request"),
        (401, "authentication"),
        (403, "permission"),
        (404, "not_found"),
        (413, "too_large"),
        (422, "invalid_request"),
        (429, "rate_limit"),
        (500, "upstream"),
        (503, "overloaded"),
        (504, "timeout"),
        (529, "overloaded"),
    ],
)
def test_failure_taxonomy_is_independent_of_provider_message(target, status, kind):
    failure = gateway_failure(status, "context window rate limit authentication")
    payload = failure.payload(target)
    assert failure.kind == payload["error"]["code"] == kind
    assert (payload.get("type") == "error") == (target == "messages")


@pytest.mark.parametrize("target", ["messages", "responses"])
@pytest.mark.parametrize("status", [400, 401, 403, 429, 503, 504])
def test_translated_upstream_errors_preserve_status_retry_headers_and_redact_secrets(target, status):
    def upstream(request, *, timeout):
        raw = json.dumps(
            {"error": {"message": "failed upstream-secret and local-secret", "code": "unknown-provider-code"}}
        ).encode()
        raise HTTPError(
            request.full_url,
            status,
            "failed",
            {"Retry-After": "12", "x-should-retry": "false", "Authorization": "private"},
            io.BytesIO(raw),
        )

    config = GatewayConfig("chat", "https://example.test/v1", "upstream-secret", "local")
    body = {"model": "m", "messages": [], "max_tokens": 8} if target == "messages" else {"model": "m", "input": "Hi"}
    with listener(config, upstream) as url, pytest.raises(HTTPError) as caught:
        post(url.removesuffix("responses") + target, body)
    assert caught.value.code == status
    assert caught.value.headers["Retry-After"] == "12"
    assert caught.value.headers["x-should-retry"] == "false"
    assert "Authorization" not in caught.value.headers
    payload = caught.value.read()
    caught.value.close()
    assert b"upstream-secret" not in payload and b"local-secret" not in payload
    assert b"unknown-provider-code" not in payload


def test_context_errors_require_known_structured_codes_and_messages_are_bounded():
    failure = gateway_failure(400, "secret" * 1000, code="context_window_exceeded", secrets=("secret",))
    assert failure.kind == "context_window" and failure.code == "context_length_exceeded"
    assert "secret" not in failure.message and len(failure.message) == 2000


def test_responses_stream_errors_use_event_fields_not_http_error_envelope():
    failure = gateway_failure(502, "Upstream failed")
    payload = failure.stream_payload("responses", sequence_number=12)
    assert payload == {
        "type": "error",
        "code": "upstream",
        "message": "Upstream failed",
        "param": None,
        "sequence_number": 12,
    }
    assert "error" not in payload


def test_gateway_config_repr_never_includes_keys_or_tokens():
    config = GatewayConfig("chat", "https://example.test/v1", "private-upstream-key", "private-local-token")
    assert "private-upstream-key" not in repr(config)
    assert "private-local-token" not in repr(config)


@pytest.mark.parametrize("status,kind", [(401, "authentication"), (403, "permission"), (429, "rate_limit")])
def test_context_code_never_overrides_authentication_permission_or_rate_limit(status, kind):
    assert gateway_failure(status, "failure", code="context_length_exceeded").kind == kind


@pytest.mark.parametrize(
    "exception,status,code",
    [
        (TimeoutError("private"), 504, "upstream_timeout"),
        (URLError(TimeoutError("private")), 504, "upstream_timeout"),
        (URLError("private"), 502, "upstream_transport"),
    ],
)
def test_transport_failure_does_not_expose_raw_exception(exception, status, code):
    def upstream(request, *, timeout):
        raise exception

    config = GatewayConfig("chat", "https://example.test/v1", "key", "local")
    with listener(config, upstream) as url, pytest.raises(HTTPError) as caught:
        post(url, {"model": "m", "input": "Hi"})
    assert caught.value.code == status
    payload = caught.value.read()
    caught.value.close()
    assert json.loads(payload)["error"]["code"] == code
    assert b"private" not in payload
