import io
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from protoverta.admission import AdmissionDenied, AdmissionStorageError
from protoverta.observability import RequestTrace, jsonl_observer
from protoverta.resources import ClientDisconnected
from protoverta.server import GatewayConfig, gateway_session, make_handler
from protoverta.sse import encode_sse


@contextmanager
def observed_server(upstream, observer, **options):
    config = GatewayConfig("chat", "https://example.test/private-prefix", "secret-key", "local-token", **options)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=upstream, observer=observer))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def post(origin, body=None, *, path="/v1/chat/completions", auth="Bearer local-token", headers=None):
    # Auth rejection precedes body parsing. An unnecessary body can race HTTP/1.0
    # early close on Windows; this fixture tests the 401 envelope, not body drain.
    data = (
        b""
        if auth != "Bearer local-token"
        else json.dumps(body or {"model": "private-model", "messages": [{"role": "user", "content": "private-prompt"}]}).encode()
    )
    return urlopen(Request(origin + path, data=data, headers={"Authorization": auth, **(headers or {})}), timeout=5)


def chat_response():
    response = io.BytesIO(b'{"model":"m","choices":[{"message":{"role":"assistant","content":"private-answer"},"finish_reason":"stop"}]}')
    response.headers = {"x-request-id": "upstream-private-id"}
    return response


def test_trace_is_frozen_bounded_and_has_no_payload_fields():
    now = [10.0]
    trace = RequestTrace("chat", "messages", clock=lambda: now[0])
    trace.status, trace.outcome = 200, "success"
    now[0] += 0.123
    record = trace.snapshot()
    assert 122 <= record.duration_ms <= 123
    assert re.fullmatch("[0-9a-f]{32}", record.request_id)
    assert set(asdict(record)) == {
        "request_id",
        "protocol",
        "upstream_protocol",
        "status",
        "outcome",
        "duration_ms",
        "streaming",
        "first_payload_ms",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "cache_creation_input_tokens",
        "requested_model_digest",
        "effective_model_digest",
        "phase",
        "feature",
        "agent_digest",
        "chain_digest",
    }
    assert record.first_payload_ms is record.input_tokens is record.output_tokens is None
    with pytest.raises(FrozenInstanceError):
        record.outcome = "timeout"
    now[0] = 0
    assert trace.snapshot().duration_ms == 0


def test_observed_usage_and_first_payload_are_measured_not_invented():
    now = [10.0]
    trace = RequestTrace("chat", "responses", clock=lambda: now[0])
    now[0] = 10.5
    trace.mark_first_payload()
    trace.usage(100, 10, 80, None)
    now[0] = 11.0
    trace.mark_first_payload()
    trace.usage(None, None, None, None)
    result = trace.snapshot()
    assert result.first_payload_ms == 500 and result.duration_ms == 1000
    assert result.input_tokens == 100 and result.output_tokens == 10
    assert result.cached_input_tokens == 80 and result.cache_creation_input_tokens is None


def test_jsonl_sink_serializes_concurrent_records_and_failures_are_optional():
    output = io.StringIO()
    observer = jsonl_observer(output)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: RequestTrace("chat", "chat").emit(observer), range(48)))
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(rows) == len({row["request_id"] for row in rows}) == 48

    def broken(record):
        raise OSError("private-key/path must never be logged")

    trace = RequestTrace("chat", "chat")
    trace.emit(broken)  # No exception/logging or retries from diagnostics.
    trace.emit(None)


def test_native_success_has_local_id_and_does_not_log_sensitive_fields():
    records, outbound = [], []

    def upstream(request, timeout):
        outbound.append(dict(request.header_items()))
        return chat_response()

    with observed_server(upstream, records.append) as origin:
        with post(origin, path="/v1/chat/completions?private-query=value", headers={"X-Request-ID": "client-private-id"}) as response:
            wire = response.read()
            local_id = response.headers["X-Protoverta-Request-ID"]
            assert response.headers["x-request-id"] == "upstream-private-id"
        assert b"private-answer" in wire
    assert len(records) == 1 and records[0].outcome == "success"
    assert records[0].status == 200 and records[0].request_id == local_id
    serialized = json.dumps(asdict(records[0]))
    for sentinel in (
        "private-prompt",
        "private-answer",
        "private-model",
        "private-prefix",
        "private-query",
        "secret-key",
        "local-token",
        "client-private-id",
        "upstream-private-id",
    ):
        assert sentinel not in serialized
    assert all("protoverta-request-id" not in key.lower() for key in outbound[0])


@pytest.mark.parametrize(
    ("path", "auth", "status", "outcome"),
    [
        ("/secret-path", "Bearer local-token", 404, "not_found"),
        ("/v1/chat/completions", "Bearer wrong-secret", 401, "authentication"),
        ("/v1/responses", "Bearer local-token", 422, "invalid_request"),
    ],
)
def test_local_rejections_emit_one_record_without_upstream_io(path, auth, status, outcome):
    records = []

    def forbidden(*args, **kwargs):
        pytest.fail("rejected request must not reach upstream")

    with observed_server(forbidden, records.append) as origin:
        with pytest.raises(HTTPError) as caught:
            post(origin, path=path, auth=auth)
        assert caught.value.code == status
        assert caught.value.headers["X-Protoverta-Request-ID"]
        caught.value.close()
    assert len(records) == 1 and records[0].outcome == outcome
    assert records[0].status == status
    assert records[0].protocol == ("unknown" if status == 404 else "responses" if status == 422 else "chat")


@pytest.mark.parametrize(
    ("error", "status", "outcome"),
    [
        (URLError("private-transport-text"), 502, "upstream_transport"),
        (TimeoutError("private-timeout-text"), 504, "timeout"),
        (AdmissionDenied(1), 429, "shared_admission"),
        (AdmissionStorageError("private-path"), 503, "admission_unavailable"),
    ],
)
def test_runtime_errors_are_fixed_categories(error, status, outcome):
    records = []

    def failing(*args, **kwargs):
        raise error

    with observed_server(failing, records.append) as origin:
        with pytest.raises(HTTPError) as caught:
            post(origin)
        caught.value.close()
    assert len(records) == 1 and records[0].status == status
    assert records[0].outcome == outcome
    assert "private-" not in json.dumps(asdict(records[0]))


def test_upstream_http_status_is_preserved_with_no_error_message_in_observation():
    records = []

    def upstream(request, timeout):
        raise HTTPError(request.full_url, 400, "private", {}, io.BytesIO(b'{"error":{"message":"private-Azure-item"}}'))

    with observed_server(upstream, records.append) as origin:
        with pytest.raises(HTTPError) as caught:
            post(origin)
        assert b"private-Azure-item" in caught.value.read()
        caught.value.close()
    assert records[0].outcome == "upstream_http" and records[0].status == 400
    assert "Azure" not in json.dumps(asdict(records[0]))


class Segments(io.BytesIO):
    def __init__(self, chunks):
        super().__init__()
        self.chunks = iter(chunks)

    def read1(self, size):
        return next(self.chunks, b"")


def chat_chunk(delta, finish=None):
    return encode_sse(
        "message", json.dumps({"object": "chat.completion.chunk", "model": "m", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})
    )


@pytest.mark.parametrize("visible", [False, True])
def test_reasoning_failure_before_or_after_headers_is_not_success(visible):
    records = []
    chunks = [chat_chunk({"role": "assistant", "content": "visible" if visible else ""}), chat_chunk({"reasoning_content": "private-reasoning"})]
    with observed_server(lambda request, timeout: Segments(chunks), records.append) as origin:
        body = {"model": "m", "input": "hi", "stream": True}
        if visible:
            with post(origin, body, path="/v1/responses") as response:
                wire = response.read()
            assert b"unsupported_reasoning" in wire and b"response.completed" not in wire
        else:
            with pytest.raises(HTTPError) as caught:
                post(origin, body, path="/v1/responses")
            caught.value.close()
    assert len(records) == 1 and records[0].outcome == "unsupported_reasoning"
    assert records[0].status == (200 if visible else 422)
    assert records[0].streaming
    assert "private-reasoning" not in json.dumps(asdict(records[0]))


@pytest.mark.parametrize("complete", [True, False])
def test_stream_terminal_validation_controls_outcome(complete):
    records = []
    chunks = [chat_chunk({"role": "assistant", "content": "hello"})]
    if complete:
        chunks += [chat_chunk({}, "stop"), encode_sse("message", "[DONE]")]
    with (
        observed_server(lambda request, timeout: Segments(chunks), records.append) as origin,
        post(origin, {"model": "m", "messages": [], "stream": True}) as response,
    ):
        wire = response.read()
    assert records[0].status == 200 and records[0].streaming
    assert records[0].outcome == ("success" if complete else "invalid_stream")
    assert (b"[DONE]" in wire) == complete


def test_parallel_requests_have_unique_ids_and_observer_failure_does_not_break_response():
    records = []
    with observed_server(lambda request, timeout: chat_response(), records.append) as origin:

        def run(_):
            with post(origin) as response:
                response.read()
                return response.headers["X-Protoverta-Request-ID"]

        with ThreadPoolExecutor(6) as pool:
            ids = list(pool.map(run, range(12)))
    assert len(records) == len(set(ids)) == 12
    assert {record.request_id for record in records} == set(ids)
    assert all(record.outcome == "success" for record in records)

    def broken(record):
        raise OSError("private")

    with observed_server(lambda request, timeout: chat_response(), broken) as origin, post(origin) as response:
        assert b"private-answer" in response.read()


def test_gateway_session_observer_is_explicit_and_default_has_no_output(capsys):
    config = GatewayConfig("chat", "https://example.test/v1", "key", "local")
    records = []
    for observer in (None, records.append):
        with gateway_session(config, observer=observer) as origin:
            with pytest.raises(HTTPError) as caught:
                post(origin, auth="wrong")
            caught.value.close()
    assert len(records) == 1 and records[0].outcome == "authentication"
    assert capsys.readouterr() == ("", "")


def test_capacity_rejection_and_followup_success_are_separate_records():
    records = []
    entered, release = threading.Event(), threading.Event()

    def upstream(request, timeout):
        entered.set()
        assert release.wait(5)
        return chat_response()

    with observed_server(upstream, records.append, max_concurrent_requests=1) as origin, ThreadPoolExecutor(1) as pool:

        def first():
            with post(origin) as response:
                return response.read()

        pending = pool.submit(first)
        try:
            assert entered.wait(3)
            with pytest.raises(HTTPError) as caught:
                post(origin)
            assert caught.value.code == 503
            caught.value.close()
        finally:
            release.set()
        assert b"private-answer" in pending.result(timeout=5)
    assert sorted(record.outcome for record in records) == ["capacity", "success"]
    assert len({record.request_id for record in records}) == 2


def test_stream_limit_record_is_distinct_from_generic_bad_stream():
    records = []
    with observed_server(lambda request, timeout: Segments([chat_chunk({"content": "large"})]), records.append, max_stream_bytes=1) as origin:
        with pytest.raises(HTTPError) as caught:
            post(origin, {"model": "m", "messages": [], "stream": True})
        caught.value.close()
    assert records[0].status == 502 and records[0].outcome == "stream_limit"


def test_upstream_connection_reset_is_not_mislabeled_as_local_disconnect():
    records = []

    class Reset(io.BytesIO):
        def read1(self, size):
            raise ConnectionResetError("private-upstream-reset")

    with observed_server(lambda request, timeout: Reset(), records.append) as origin:
        with pytest.raises(HTTPError) as caught:
            post(origin, {"model": "m", "messages": [], "stream": True})
        caught.value.close()
    assert records[0].outcome == "invalid_stream" and records[0].status == 502


def test_cancelled_upstream_wait_has_no_status_and_does_not_retry():
    records, calls = [], []

    def upstream(request, timeout):
        calls.append(1)
        raise ClientDisconnected("synthetic cancellation")

    with observed_server(upstream, records.append) as origin, pytest.raises(OSError):
        post(origin)
    assert calls == [1] and len(records) == 1
    assert records[0].outcome == "client_disconnected" and records[0].status is None


def test_invalid_json_response_is_not_reported_as_explicit_upstream_http_rejection():
    records = []
    with observed_server(lambda request, timeout: io.BytesIO(b"bad-json"), records.append) as origin:
        with pytest.raises(HTTPError) as caught:
            post(origin)
        caught.value.close()
    assert records[0].outcome == "invalid_response" and records[0].status == 502


def test_translated_upstream_auth_failure_is_not_local_authentication_failure():
    records = []

    def upstream(request, timeout):
        raise HTTPError(request.full_url, 401, "private", {}, io.BytesIO(b'{"error":{"message":"private-provider-auth"}}'))

    with observed_server(upstream, records.append) as origin:
        with pytest.raises(HTTPError) as caught:
            post(origin, {"model": "m", "input": "hi"}, path="/v1/responses")
        caught.value.close()
    assert records[0].outcome == "upstream_http" and records[0].status == 401


@pytest.mark.parametrize("enabled", [False, True])
def test_standalone_cli_diagnostics_is_explicit_stderr_only(monkeypatch, capsys, enabled):
    from protoverta import __main__ as cli

    class FakeServer:
        server_port = 12345

        def serve_forever(self, poll_interval):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    def fake_serve(config, *, port, observer):
        assert (observer is not None) == enabled
        RequestTrace("chat", "chat").emit(observer)
        return FakeServer()

    monkeypatch.setattr(cli, "serve", fake_serve)
    monkeypatch.setenv("TEST_PROTO_KEY", "synthetic-key")
    monkeypatch.setenv("TEST_LOCAL_TOKEN", "synthetic-token")
    monkeypatch.setattr(
        "sys.argv",
        [
            "protoverta",
            "--upstream-protocol",
            "chat",
            "--upstream-base-url",
            "https://example.test/v1",
            "--upstream-key-env",
            "TEST_PROTO_KEY",
            "--inbound-token-env",
            "TEST_LOCAL_TOKEN",
            *(["--diagnostics"] if enabled else []),
        ],
    )
    assert cli.main() == 0
    output = capsys.readouterr()
    assert output.out == "READY http://127.0.0.1:12345\n"
    assert bool(output.err) == enabled
    if enabled:
        assert json.loads(output.err)["protocol"] == "chat"
        assert "synthetic" not in output.err
