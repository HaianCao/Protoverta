import io
import json
import socket
import threading
import time
from http.client import BadStatusLine, IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import BaseRequestHandler, TCPServer
from urllib.error import HTTPError, URLError

import pytest

from protoverta.discovery import CatalogError, CatalogPolicy, fetch_catalog, parse_page


@pytest.mark.parametrize("wire", [
    b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n20\r\n{"data":[',
    b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nprivate-invalid-size\r\n',
    b'private-invalid-status\r\n\r\n',
], ids=["truncated-chunk", "invalid-chunk-size", "invalid-status-line"])
def test_catalog_real_http_framing_errors_are_safe_and_close_connection(wire):
    disconnected = threading.Event()
    requests = []

    class Handler(BaseRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            data = bytearray()
            while b"\r\n\r\n" not in data:
                chunk = self.request.recv(4096)
                if not chunk:
                    return
                data.extend(chunk)
            requests.append(bytes(data))
            self.request.sendall(wire)
            # End the malformed response while retaining the read half so that
            # the test can observe the client's cleanup, rather than our close.
            self.request.shutdown(socket.SHUT_WR)
            try:
                if self.request.recv(1) == b"":
                    disconnected.set()
            except ConnectionResetError:
                disconnected.set()

    with TCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with pytest.raises(CatalogError) as error:
                fetch_catalog(f"http://127.0.0.1:{server.server_address[1]}/v1", {}, CatalogPolicy(timeout=2))
            assert str(error.value) == "catalog transport or JSON validation failed"
            assert disconnected.wait(2)
            assert len(requests) == 1  # No retry or partial catalog success.
        finally:
            server.shutdown()
            thread.join(timeout=3)
        assert not thread.is_alive()


@pytest.mark.parametrize("failure", [IncompleteRead(b"private-partial-body"), BadStatusLine("private-status")])
def test_catalog_closes_open_response_on_http_framing_failure(failure):
    class Response(io.BytesIO):
        def read(self, size=-1):
            raise failure

        read1 = read

    response = Response()
    with pytest.raises(CatalogError) as error:
        fetch_catalog("https://example.test/v1", {}, upstream_open=lambda request, timeout: response)
    assert response.closed
    assert str(error.value) == "catalog transport or JSON validation failed"


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


@pytest.mark.parametrize("value", [chr(0xD800), chr(0xDFFF), "private-" + chr(0xD800) + "-name"], ids=["high-surrogate", "low-surrogate", "embedded-surrogate"])
@pytest.mark.parametrize("field,message", [
    ("id", "model catalog contains an invalid model ID"),
    ("display_name", "model catalog contains an invalid display name"),
    ("last_id", "invalid catalog pagination cursor"),
])
@pytest.mark.parametrize("from_wire", [False, True], ids=["parsed-object", "escaped-json"])
def test_catalog_rejects_surrogates_before_return_or_cursor_reuse(value, field, message, from_wire):
    policy = CatalogPolicy(pagination="after_id")
    body = {"data": [{"id": "model", "display_name": "Model"}]}
    if field == "last_id":
        body.update(has_more=True, last_id=value)
    else:
        body["data"][0][field] = value
    captured = []
    with pytest.raises(CatalogError) as error:
        if from_wire:
            fetch_catalog("https://example.test/v1", {}, policy, upstream_open=pages([body], captured))
        else:
            parse_page(body, policy)
    assert str(error.value) == message
    assert len(captured) == int(from_wire)  # Invalid cursor never reaches a second request.


@pytest.mark.parametrize("escaped", [False, True], ids=["utf8", "escaped-pair"])
def test_catalog_preserves_valid_unicode_and_supplementary_characters(escaped):
    identifier = "mô-hình-🚀"
    name = "Mô hình tiếng Việt 日本語 🚀"
    captured = []
    bodies = iter([
        {"data": [{"id": identifier, "display_name": name}], "has_more": True, "last_id": identifier},
        {"data": [], "has_more": False},
    ])

    def opener(request, timeout):
        captured.append(request.full_url)
        return io.BytesIO(json.dumps(next(bodies), ensure_ascii=escaped).encode("utf-8"))

    result = fetch_catalog("https://example.test/v1", {}, CatalogPolicy(pagination="after_id"), upstream_open=opener)
    assert len(result) == 1 and result[0].id == identifier and result[0].display_name == name
    assert json.dumps(result[0].as_dict(), ensure_ascii=False).encode("utf-8")
    from urllib.parse import parse_qs, urlsplit

    assert parse_qs(urlsplit(captured[1]).query)["after_id"] == [identifier]


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
