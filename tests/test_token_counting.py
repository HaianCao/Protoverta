import io
import json
from dataclasses import replace
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post

from protoverta.core import ConversionError
from protoverta.server import GatewayConfig
from protoverta.token_counting import ModelTokenCounter, count_input


def config(protocol="messages", mode="native"):
    return replace(GatewayConfig(protocol, "https://example.test/prefix/v1", "fixture", "local"), token_count_mode=mode)


@pytest.mark.parametrize(
    "protocol,path", [("messages", "/messages/count_tokens"), ("responses", "/responses/input_tokens")]
)
def test_native_count_uses_exact_prefix_and_never_generates(protocol, path):
    captured = []

    def upstream(request, timeout):
        captured.append(request)
        return io.BytesIO(b'{"input_tokens":17,"object":"response.input_tokens"}')

    body = (
        {"model": "m", "messages": [{"role": "user", "content": "hello"}]}
        if protocol == "messages"
        else {"model": "m", "input": "hello"}
    )
    assert count_input(config(protocol), body, protocol, upstream_open=upstream) == (17, "provider-native")
    assert captured[0].full_url == "https://example.test/prefix/v1" + path
    assert "max_tokens" not in json.loads(captured[0].data)
    assert captured[0].get_header("Authorization") == "Bearer fixture"


def test_count_http_errors_close_and_sanitize_and_chat_never_falls_back():
    stream = io.BytesIO(b"private")

    def failed(request, timeout):
        raise HTTPError(request.full_url, 401, "private", {}, stream)

    with pytest.raises(ConversionError, match="HTTP 401"):
        count_input(config(), {"model": "m", "messages": []}, "messages", upstream_open=failed)
    assert stream.closed
    with pytest.raises(ConversionError, match="no certified"):
        count_input(config("chat"), {"model": "m", "messages": []}, "messages", upstream_open=failed)


def test_count_http_endpoint_opt_in_auth_and_estimate_provenance():
    with listener(config(mode="estimate"), lambda *_: pytest.fail("no upstream")) as url:
        endpoint = url.replace("/responses", "/messages/count_tokens")
        with post(endpoint, {"model": "m", "messages": [{"role": "user", "content": "hello"}]}) as response:
            assert response.headers["X-Protoverta-Count-Kind"] == "utf8-json-byte-estimate"
            assert type(json.load(response)["input_tokens"]) is int
    with listener(config(mode="off"), lambda *_: pytest.fail("no upstream")) as url:
        with pytest.raises(HTTPError) as error:
            post(url.replace("/responses", "/messages/count_tokens"), {"model": "m", "messages": []})
        assert error.value.code == 422
        error.value.close()


def test_model_bound_local_tokenizer_has_no_download_or_cross_model_claim():
    counter = ModelTokenCounter(
        "m",
        "messages",
        render_request=lambda body: json.dumps(body["messages"]),
        encode=lambda text: list(text.encode()),
        exact=False,
    )
    operator = replace(config(mode="off"), token_count_mode="operator", token_counter=counter)
    body = {"model": "m", "messages": [{"role": "user", "content": "synthetic"}]}
    count, provenance = count_input(operator, body, "messages")
    assert count > 0 and provenance == "operator-estimate"
    with pytest.raises(ConversionError):
        count_input(operator, body | {"model": "another"}, "messages")
    with pytest.raises(ConversionError):
        count_input(operator, body | {"messages": [{"role": "user", "content": [{"type": "image"}]}]}, "messages")


def test_native_count_uses_shared_provider_admission_and_closes_response(tmp_path):
    item = replace(config(), shared_admission_db=str(tmp_path / "count.sqlite"), shared_requests=1)
    responses = []

    def upstream(request, timeout):
        response = io.BytesIO(b'{"input_tokens":4}')
        responses.append(response)
        return response

    with listener(item, upstream) as url:
        endpoint = url.replace("/responses", "/messages/count_tokens")
        with post(endpoint, {"model": "m", "messages": [{"role": "user", "content": "hi"}]}) as response:
            assert json.load(response)["input_tokens"] == 4
        with pytest.raises(HTTPError) as error:
            post(endpoint, {"model": "m", "messages": [{"role": "user", "content": "hi"}]})
        assert error.value.code == 429
        error.value.close()
    assert len(responses) == 1 and responses[0].closed


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_count_preserves_upstream_failure_status_safe_headers_and_privacy(status):
    stream = io.BytesIO(b"private provider body")

    def failed(request, timeout):
        raise HTTPError(
            request.full_url,
            status,
            "private reason",
            {"Retry-After": "7", "Set-Cookie": "private", "X-Private": "private"},
            stream,
        )

    with listener(config(), failed) as url:
        with pytest.raises(HTTPError) as error:
            post(url.replace("/responses", "/messages/count_tokens"), {"model": "m", "messages": []})
        with error.value as response:
            assert response.code == status
            assert response.headers["Retry-After"] == "7"
            assert response.headers.get("Set-Cookie") is None
            assert response.headers.get("X-Private") is None
            assert b"private" not in response.read()
    assert stream.closed


def test_count_provider_schema_error_is_not_client_validation_error():
    with listener(config(), lambda request, timeout: io.BytesIO(b'{"input_tokens":-1}')) as url:
        with pytest.raises(HTTPError) as error:
            post(url.replace("/responses", "/messages/count_tokens"), {"model": "m", "messages": []})
        assert error.value.code == 502
        error.value.close()


def test_native_counter_timeout_is_504_and_does_not_fallback():
    calls = []

    def failed(request, timeout):
        calls.append(request.full_url)
        raise TimeoutError()

    with listener(config(), failed) as url:
        with pytest.raises(HTTPError) as error:
            post(url.replace("/responses", "/messages/count_tokens"), {"model": "m", "messages": []})
        assert error.value.code == 504
        error.value.close()
    assert len(calls) == 1 and calls[0].endswith("/messages/count_tokens")


def test_native_count_rate_error_cools_down_other_requests(tmp_path):
    item = replace(config(), shared_admission_db=str(tmp_path / "cooldown.sqlite"))
    calls = []

    def failed(request, timeout):
        calls.append(1)
        raise HTTPError(request.full_url, 429, "fixture", {"Retry-After": "7"}, io.BytesIO(b"{}"))

    with listener(item, failed) as url:
        for _ in range(2):
            with pytest.raises(HTTPError) as error:
                post(url.replace("/responses", "/messages/count_tokens"), {"model": "m", "messages": []})
            assert error.value.code == 429
            error.value.close()
    assert len(calls) == 1
