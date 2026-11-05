import importlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request

import pytest

from protoverta.core import Protocol
from protoverta.sse import SSEParser
from protoverta.streaming import StreamDecoder, StreamError


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("smoke_agent_faults")


@pytest.mark.parametrize("protocol", list(Protocol))
def test_fault_prefix_has_real_partial_payload_and_no_success_terminal(harness, protocol):
    decoder = StreamDecoder(protocol)
    deltas = [delta for event in SSEParser().feed(harness.fault_prefix(protocol)) for delta in decoder.feed(event)]
    assert any(delta.kind == "text" and delta.text == "P05_PARTIAL_NOT_SUCCESS" for delta in deltas)
    assert not any(delta.kind == "finish" for delta in deltas)
    with pytest.raises(StreamError):
        decoder.finish()


@pytest.mark.parametrize(
    "fault,category,operation", [("timeout", "upstream_timeout", "read"), ("truncated", "invalid_stream", "finish"), ("invalid", "invalid_stream", "feed")]
)
def test_trace_gate_requires_failure_at_the_injected_stage(harness, fault, category, operation):
    record = {"stream_error": category, "operation": operation}
    assert harness.verified_trace([record], fault)
    assert not harness.verified_trace([{**record, "operation": "open"}], fault)
    assert not harness.verified_trace([{**record, "stream_error": "upstream_http"}], fault)
    assert not harness.verified_trace([{"result": "failed"}], fault)
    assert not harness.verified_trace(None, fault)


@pytest.mark.parametrize("protocol", list(Protocol))
def test_fault_retries_keep_original_endpoint_and_model_gate(harness, protocol):
    state = harness.Scenario("claude", protocol, "truncated")
    state.stages["a"] = 1
    path = {Protocol.CHAT: "/v1/chat/completions", Protocol.MESSAGES: "/v1/messages", Protocol.RESPONSES: "/v1/responses"}[protocol]
    body = {"model": "different-model", "messages": []}
    with pytest.raises(ValueError, match="endpoint/model"):
        state.open(Request("http://127.0.0.1" + path, data=json.dumps(body).encode()), timeout=2)
    body["model"] = "p04-fixture-model"
    with pytest.raises(ValueError, match="endpoint/model"):
        state.open(Request("http://127.0.0.1/wrong", data=json.dumps(body).encode()), timeout=2)


def test_fault_retry_budget_cannot_dispatch_unbounded_child_requests(harness):
    state = harness.Scenario("claude", Protocol.CHAT, "truncated")
    state.stages["a"] = harness.MAX_FAULT_ATTEMPTS
    body = {"model": "p04-fixture-model", "messages": [{"role": "user", "content": harness.parallel.TASKS[0]}]}
    with pytest.raises(ValueError, match="bounded retry"):
        state.open(Request("http://127.0.0.1/v1/chat/completions", data=json.dumps(body).encode()), timeout=2)
    assert state.stages["a"] == harness.MAX_FAULT_ATTEMPTS


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("fault", ["truncated", "invalid", "timeout"])
def test_fault_reply_never_supplies_a_successful_terminal_and_timeout_needs_eof(harness, streaming, fault):
    state = harness.Scenario("claude", Protocol.CHAT, fault)
    request = SimpleNamespace(
        data=json.dumps({"stream": streaming}).encode(), fixture_connection=SimpleNamespace(settimeout=lambda _: None, recv=lambda _: b"")
    )
    handler = SimpleNamespace(wfile=io.BytesIO(), send_response=lambda _: None, send_header=lambda *_: None, end_headers=lambda: None)
    harness.FaultReply(state, request).write_to(handler)
    assert state.fault_sent.is_set()
    assert state.timeout_disconnected.is_set() is (fault == "timeout")
    assert b"[DONE]" not in handler.wfile.getvalue()
    assert b"response.completed" not in handler.wfile.getvalue()
    assert handler.close_connection is True


@pytest.mark.parametrize("value", [True, 0, -1, float("nan"), float("inf"), "2"])
def test_fixture_timeout_validation_precedes_cli_discovery(harness, monkeypatch, value):
    monkeypatch.setattr(harness.single.shutil, "which", lambda _: pytest.fail("invalid timeout must not discover CLI"))
    with pytest.raises(ValueError, match="timeouts"):
        harness.single.run("claude", Protocol.CHAT, gateway_limits={"progress_timeout": value})


def test_unknown_fixture_timeout_is_rejected(harness):
    with pytest.raises(ValueError, match="timeouts"):
        harness.single.run("claude", Protocol.CHAT, gateway_limits={"typo": 1})


@pytest.mark.parametrize("fault", [None, "missing_warning", "missing_partial", "false_success", "boolean_counter", "denied", "wrong_child"])
def test_native_partial_handback_needs_explicit_warning_and_correlated_parent_result(harness, fault):
    text = "Agent terminated early due to an API error: agent did NOT finish its task P05_PARTIAL_NOT_SUCCESS"
    if fault == "missing_warning":
        text = "P05_PARTIAL_NOT_SUCCESS"
    elif fault == "missing_partial":
        text = "Agent terminated early due to an API error: agent did NOT finish its task"
    elif fault == "false_success":
        text += " " + harness.parallel.FINALS[0]
    state = SimpleNamespace(
        native_partial_handback=harness.native_partial_failure(text), parent_failure_correlated=fault != "wrong_child", expected_failure=lambda _: False
    )
    result = {
        "is_error": False,
        "permission_denials": ["denied"] if fault == "denied" else [],
        "subagent_stats": {"spawned": 2, "completed": 2, "failed": False if fault == "boolean_counter" else 0},
    }
    assert harness.verified_lifecycle("claude", json.dumps(result), state) is (fault is None)


def test_timeout_does_not_claim_upstream_disconnect_when_socket_has_extra_data(harness):
    state = harness.Scenario("claude", Protocol.CHAT, "timeout")
    request = SimpleNamespace(data=b'{"stream":true}', fixture_connection=SimpleNamespace(settimeout=lambda _: None, recv=lambda _: b"x"))
    handler = SimpleNamespace(wfile=io.BytesIO(), send_response=lambda _: None, send_header=lambda *_: None, end_headers=lambda: None)
    with pytest.raises(ValueError, match="Unexpected data"):
        harness.FaultReply(state, request).write_to(handler)
    assert not state.timeout_disconnected.is_set()


def test_inherited_driver_boolean_cannot_pass_fault_gate(harness, monkeypatch):
    monkeypatch.setattr(harness.parallel, "drive", lambda *args, **kwargs: {"cli_version": "fixture", "passed": True})
    assert not harness.run("codex", Protocol.CHAT, Path("unused"), "timeout")["passed"]
