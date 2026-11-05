import importlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from protoverta.sse import SSEEvent, SSEParser, encode_sse
from protoverta.stream_pipe import StreamTranscoder
from protoverta.streaming import StreamError, UnsupportedReasoningError


@pytest.fixture
def diagnostics_module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("stream_diagnostics")


def chat_chunk(delta, reason=None, usage=None):
    body = {"object": "chat.completion.chunk", "model": "synthetic", "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
    if usage is not None:
        body["usage"] = usage
    return encode_sse("message", json.dumps(body))


def tool_stream(*, usage=False):
    wire = chat_chunk(
        {
            "role": "assistant",
            "tool_calls": [{"index": 0, "id": "private-call-id", "type": "function", "function": {"name": "private-tool-name", "arguments": "{}"}}],
        }
    )
    wire += chat_chunk({}, "tool_calls")
    if usage:
        wire += encode_sse(
            "message",
            json.dumps({"object": "chat.completion.chunk", "model": "synthetic", "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}),
        )
    return wire + encode_sse("message", "[DONE]")


@pytest.mark.parametrize("target", ["responses", "messages"])
def test_missing_usage_tool_failure_is_in_encoder_without_decoder_exception(diagnostics_module, target):
    diagnostic = diagnostics_module.StreamDiagnostics()
    with diagnostic.capture(observe_transport=False):
        pipe = StreamTranscoder("chat", target, messages_usage_policy="deferred")
        with pytest.raises(StreamError, match="requires.*token"):
            pipe.feed(tool_stream())
    report = diagnostic.snapshot()
    assert report["failures"][0] == {"stage": "encoder", "operation": "feed", "category": "invalid_stream"}
    assert not any(item["stage"] == "decoder" for item in report["failures"])
    assert report["last_delta"] == {"kind": "finish", "input_tokens_present": False, "output_tokens_present": False}
    assert report["states"]["decoder"]["ended"] is True
    assert "private" not in json.dumps(report)


@pytest.mark.parametrize("target", ["responses", "messages"])
def test_diagnostics_preserve_successful_bytes_and_clean_eof(diagnostics_module, target, monkeypatch):
    monkeypatch.setattr("protoverta.stream_encoder.uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="synthetic")))
    monkeypatch.setattr("protoverta.stream_encoder.time", SimpleNamespace(time=lambda: 100))

    def convert():
        pipe = StreamTranscoder("chat", target, messages_usage_policy="deferred")
        return pipe.feed(tool_stream(usage=True)) + pipe.finish()

    expected = convert()
    diagnostic = diagnostics_module.StreamDiagnostics()
    with diagnostic.capture(observe_transport=False):
        wire = convert()
    assert wire == expected
    events = SSEParser().feed(wire)
    assert events[-1].event == ("response.completed" if target == "responses" else "message_stop")
    report = diagnostic.snapshot()
    assert report["failures"] == []
    assert report["last_delta"]["input_tokens_present"] and report["last_delta"]["output_tokens_present"]
    assert report["last_usage_frame"]["choices_count"] == 0
    assert report["states"]["encoder"]["ended"] is True
    assert StreamTranscoder.feed.__module__ == "protoverta.stream_pipe"


@pytest.mark.parametrize("mode,operation,category", [("utf8", "feed", "invalid_utf8"), ("incomplete_frame", "finish", "invalid_stream")])
def test_parser_failure_is_recorded_without_decoder_exception(diagnostics_module, mode, operation, category):
    diagnostic = diagnostics_module.StreamDiagnostics()
    with diagnostic.capture(observe_transport=False):
        pipe = StreamTranscoder("chat", "responses")
        with pytest.raises(StreamError):
            if mode == "utf8":
                pipe.feed(b"data: \xff\n\n")
            else:
                pipe.feed(b'data: {"private": "sentinel"}')
                pipe.finish()
    assert diagnostic.failures[0] == {"stage": "parser", "operation": operation, "category": category}
    assert not any(item["stage"] == "decoder" for item in diagnostic.failures)
    assert "private" not in json.dumps(diagnostic.snapshot())


def test_missing_terminal_is_decoder_eof_failure(diagnostics_module):
    diagnostic = diagnostics_module.StreamDiagnostics()
    with diagnostic.capture(observe_transport=False):
        pipe = StreamTranscoder("chat", "responses")
        pipe.feed(chat_chunk({"role": "assistant", "content": "private-text"}))
        with pytest.raises(StreamError, match="terminal"):
            pipe.finish()
    assert diagnostic.failures[0] == {"stage": "decoder", "operation": "finish", "category": "invalid_stream"}
    assert "private" not in json.dumps(diagnostic.snapshot())


@pytest.mark.parametrize("field", ["reasoning", "reasoning_content", "reasoning_details", "private-unknown-field"])
def test_diagnostics_retain_strict_rejection_and_never_echo_field_values(diagnostics_module, field):
    diagnostic = diagnostics_module.StreamDiagnostics()
    with diagnostic.capture(observe_transport=False):
        pipe = StreamTranscoder("chat", "responses")
        expected = StreamError if field.startswith("private") else UnsupportedReasoningError
        with pytest.raises(expected):
            pipe.feed(chat_chunk({field: "private-reasoning-key"}))
    assert diagnostic.failures[0]["stage"] == "decoder"
    assert diagnostic.failures[0]["category"] == ("invalid_stream" if field.startswith("private") else "unsupported_reasoning")
    assert "private" not in json.dumps(diagnostic.snapshot())


@pytest.mark.parametrize(
    "exc,category",
    [
        (HTTPError("https://private-url", 429, "private-error-key", {"Retry-After": "38"}, io.BytesIO(b"private-body")), "upstream_http"),
        (URLError("private-key"), "upstream_transport"),
        (URLError(TimeoutError("private-key")), "upstream_timeout"),
    ],
)
def test_transport_status_and_category_are_safe_and_original_error_is_retained(diagnostics_module, exc, category):
    diagnostic = diagnostics_module.StreamDiagnostics()

    def opener(request, *, timeout):
        raise exc

    with pytest.raises(type(exc)) as caught:
        diagnostic.wrap_open(opener)(Request("https://example.test"), timeout=1)
    assert caught.value is exc
    report = diagnostic.snapshot()
    assert report["requests"] == 1
    assert report["failures"] == [{"stage": "transport", "operation": "open", "category": category}]
    if isinstance(exc, HTTPError):
        assert report["http_statuses"] == [429] and report["retry_after_seconds"] == 38
    assert "private" not in json.dumps(report)


def test_response_proxy_preserves_chunk_reads_context_close_and_eof(diagnostics_module):
    response = io.BytesIO(b"private-payload")
    diagnostic = diagnostics_module.StreamDiagnostics()
    with diagnostic.wrap_open(lambda request, timeout: response)(Request("https://example.test"), timeout=1) as proxy:
        assert proxy.read1(7) == b"private"
        assert proxy.read() == b"-payload"
        assert proxy.read1(1) == b""
    assert response.closed
    assert diagnostic.snapshot()["bytes_read"] == 15
    assert diagnostic.snapshot()["eof_count"] == 1
    assert "private" not in json.dumps(diagnostic.snapshot())


def test_response_read_failure_is_distinct_from_open_failure(diagnostics_module):
    class Response(io.BytesIO):
        def read1(self, size):
            raise TimeoutError("private-key")

    diagnostic = diagnostics_module.StreamDiagnostics()
    with diagnostic.wrap_open(lambda request, timeout: Response())(Request("https://example.test"), timeout=1) as proxy, pytest.raises(TimeoutError):
        proxy.read1(4)
    assert diagnostic.failures == [{"stage": "transport", "operation": "read", "category": "upstream_timeout"}]


def test_diagnostic_storage_and_snapshot_are_bounded(diagnostics_module):
    diagnostic = diagnostics_module.StreamDiagnostics(max_records=2)
    with diagnostic.capture(observe_transport=False):
        for _ in range(50):
            pipe = StreamTranscoder("chat", "responses")
            with pytest.raises(StreamError):
                pipe.feed(chat_chunk({"private-field": "private-value"}))
    report = diagnostic.snapshot()
    assert len(report["failures"]) == len(report["first_frames"]) == 2
    assert report["frame_count"] == 50 and report["dropped_failures"] == 98
    report["failures"].clear()
    assert len(diagnostic.failures) == 2
    assert len(json.dumps(diagnostic.snapshot())) < 4000
    assert "private" not in json.dumps(diagnostic.snapshot())


def test_shape_never_echoes_unknown_object_finish_reason_or_function_fields(diagnostics_module):
    assert diagnostics_module.chat_shape(SSEEvent("private-event", '{"object":"private-object"}')) == {"kind": "other"}
    wire = chat_chunk(
        {
            "tool_calls": [
                {
                    "id": "private-id",
                    "private-field": "private-value",
                    "function": {"name": "private-name", "arguments": "private-arguments", "private-signature": "private-state"},
                }
            ]
        },
        "private-reason",
    )
    shape = diagnostics_module.chat_shape(SSEParser().feed(wire)[0])
    assert shape["choices"][0]["finish_reason"] == "other"
    assert shape["choices"][0]["tools"][0]["unknown_fields"]
    assert shape["choices"][0]["tools"][0]["function"]["unknown_fields"]
    assert "private" not in json.dumps(shape)


def test_probe_budget_stops_before_io_and_is_distinct_from_provider_429(diagnostics_module):
    requests = []
    diagnostic = diagnostics_module.StreamDiagnostics(max_upstream_requests=1)

    def opener(request, *, timeout):
        requests.append(request)
        return io.BytesIO(b"fixture")

    opened = diagnostic.wrap_open(opener)
    request = Request("https://example.test")
    with opened(request, timeout=1) as response:
        assert response.read() == b"fixture"
    with pytest.raises(diagnostics_module.ProbeBudgetExceeded) as caught:
        opened(request, timeout=1)
    assert caught.value.code == 422 and caught.value.headers["x-should-retry"] == "false"
    caught.value.close()
    report = diagnostic.snapshot()
    assert len(requests) == report["requests"] == report["max_upstream_requests"] == 1
    assert report["failures"] == [{"stage": "probe", "operation": "open", "category": "probe_request_budget"}]
    assert report["http_statuses"] == [] and report["retry_after_seconds"] is None


@pytest.mark.parametrize("limit", [0, -1, True, "1"])
def test_request_budget_must_be_an_explicit_positive_integer(diagnostics_module, limit):
    with pytest.raises(ValueError):
        diagnostics_module.StreamDiagnostics(max_upstream_requests=limit)


@pytest.mark.parametrize("observed", [False, True])
def test_transport_capture_keeps_gateway_session_observer(diagnostics_module, monkeypatch, observed):
    from urllib.request import ProxyHandler, build_opener

    from protoverta import server

    opened = []
    records = []
    payload = {
        "model": "fixture",
        "choices": [{"message": {"role": "assistant", "content": "fixture-answer"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }

    def upstream(request, *, timeout):
        opened.append(request)
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(diagnostics_module, "build_opener", lambda *_: SimpleNamespace(open=upstream))
    diagnostic = diagnostics_module.StreamDiagnostics()
    original = server.make_handler
    config = server.GatewayConfig("chat", "https://example.invalid/v1", "synthetic-upstream", "synthetic-local")
    with diagnostic.capture(), server.gateway_session(config, observer=records.append if observed else None) as origin:
        request = Request(
            origin + "/v1/responses",
            data=b'{"model":"fixture","input":"fixture"}',
            headers={"Authorization": "Bearer synthetic-local", "Content-Type": "application/json"},
        )
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            assert response.status == 200
            assert b"fixture-answer" in response.read()
    assert server.make_handler is original
    assert len(opened) == diagnostic.requests == 1
    assert len(records) == int(observed)
    assert not diagnostic.failures
    if observed:
        assert records[0].outcome == "success"
