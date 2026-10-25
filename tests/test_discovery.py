import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError

import pytest

from protoverta.discovery import CatalogError, CatalogPolicy, fetch_catalog


def test_real_transport_rejects_redirect_and_slow_drip_total_deadline():
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            paths.append(self.path)
            if self.path == "/models":
                self.send_response(302)
                self.send_header("Location", "/redirected")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            try:
                for byte in b'{"data":[]}':
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.04)
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(CatalogError, match="redirect"):
            fetch_catalog(base, {"Authorization": "Bearer synthetic"})
        assert paths == ["/models"]
        started = time.monotonic()
        with pytest.raises(CatalogError):
            fetch_catalog(base, {}, CatalogPolicy(path="/slow", timeout=0.1))
        assert time.monotonic() - started < 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def pages(values, captured=None):
    iterator = iter(values)

    def opener(request, timeout):
        if captured is not None:
            captured.append(request)
        return io.BytesIO(json.dumps(next(iterator)).encode())

    return opener


def test_catalog_reports_explicit_metadata_but_never_certifies_capabilities():
    policy = CatalogPolicy(pagination="after_id", name_field="name", context_field="limits.context", output_field="limits.output", modalities_field="architecture.input_modalities")
    captured = []
    result = fetch_catalog("https://example.test/api/v1", {"Authorization": "Bearer synthetic"}, policy, upstream_open=pages([
        {"data": [{"id": "b", "name": "Bravo", "limits": {"context": 1000, "output": 100}, "architecture": {"input_modalities": ["text", "image"]}}], "has_more": True, "last_id": "b"},
        {"data": [{"id": "a"}], "has_more": False},
    ], captured))
    assert [item.id for item in result] == ["a", "b"]
    assert result[1].context_window_tokens == 1000 and result[1].input_modalities == ("text", "image")
    assert all(item.capability_status == "unknown" for item in result)
    assert captured[1].full_url == "https://example.test/api/v1/models?limit=1000&after_id=b"


@pytest.mark.parametrize("policy,values", [
    (CatalogPolicy(max_pages=1, pagination="after_id"), [{"data": [], "has_more": True, "last_id": "a"}]),
    (CatalogPolicy(max_models=1), [{"data": [{"id": "a"}, {"id": "b"}]}]),
    (CatalogPolicy(max_bytes=5), [{"data": []}]),
    (CatalogPolicy(), [{"data": [], "has_more": True, "last_id": "a"}]),
    (CatalogPolicy(pagination="after_id"), [{"data": [], "has_more": True, "last_id": "a"}, {"data": [], "has_more": True, "last_id": "b"}, {"data": [], "has_more": True, "last_id": "a"}]),
    (CatalogPolicy(pagination="after_id"), [{"data": [{"id": "a", "display_name": "First"}], "has_more": True, "last_id": "a"}, {"data": [{"id": "a", "display_name": "Changed"}]}]),
])
def test_catalog_fails_closed_on_limits_cycles_conflicts_and_unconfigured_pagination(policy, values):
    with pytest.raises(CatalogError):
        fetch_catalog("https://example.test/v1", {}, policy, upstream_open=pages(values))


@pytest.mark.parametrize("body", [{"data": {}}, {"data": [None]}, {"data": [{"id": "bad\nmodel"}]}, {"data": [], "has_more": "true"}, {"data": [{"id": "m", "display_name": 1}]}])
def test_catalog_rejects_malformed_data_without_echoing_values(body):
    with pytest.raises(CatalogError):
        fetch_catalog("https://example.test/v1", {}, upstream_open=pages([body]))


@pytest.mark.parametrize("path", ["https://evil.test/models", "//evil.test/models", "/../models", "/models?next=secret", "/models#fragment", "/models\r\nx"])
def test_catalog_never_takes_provider_urls_or_header_injection_as_paths(path):
    with pytest.raises(ValueError):
        CatalogPolicy(path=path)


def test_catalog_transport_errors_close_http_errors_and_do_not_leak_credentials():
    response = io.BytesIO(b"private-error-body")

    def upstream(request, timeout):
        raise HTTPError(request.full_url, 401, "private-key", {}, response)

    with pytest.raises(CatalogError, match="HTTP 401") as error:
        fetch_catalog("https://example.test/v1", {}, upstream_open=upstream)
    assert response.closed and "private" not in str(error.value)

    def failed(request, timeout):
        raise URLError("private-proxy-key")

    with pytest.raises(CatalogError) as error:
        fetch_catalog("https://example.test/v1", {}, upstream_open=failed)
    assert "private" not in str(error.value)


def test_catalog_policy_mapping_requires_exact_prefix_and_evidence(tmp_path):
    path = tmp_path / "catalog.json"
    body = {"schema_version": 1, "base_url": "https://example.test/v1", "checked_at": "2026-10-25", "evidence": "synthetic", "policy": {"context_field": "context_length"}}
    path.write_text(json.dumps(body), encoding="utf-8")
    assert CatalogPolicy.load(path, body["base_url"]).context_field == "context_length"
    with pytest.raises(CatalogError):
        CatalogPolicy.load(path, "https://other.test/v1")


def test_redirect_response_is_closed_before_raising():
    from protoverta.discovery import _NoRedirect

    response = io.BytesIO(b"private")
    with pytest.raises(CatalogError):
        _NoRedirect().redirect_request(None, response, 302, "Found", {}, "https://other.test")
    assert response.closed


@pytest.mark.parametrize("field,value", [("context_length", True), ("context_length", -1), ("context_length", 0), ("modalities", "image")])
def test_malformed_reported_metadata_does_not_turn_into_unknown_or_certified(field, value):
    policy = CatalogPolicy(context_field="context_length", modalities_field="modalities")
    with pytest.raises(CatalogError):
        fetch_catalog("https://example.test/v1", {}, policy, upstream_open=pages([{"data": [{"id": "m", field: value}]}]))
