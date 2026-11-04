import io
import json
from dataclasses import asdict, replace
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post

from protoverta.capabilities import (
    CapabilityRecord,
    CapabilityRegistry,
    RouteCapabilityRecord,
    route_policies,
)
from protoverta.context import TokenCount
from protoverta.core import Protocol
from protoverta.observability import RequestTrace
from protoverta.server import GatewayConfig
from protoverta.session_diagnostics import diagnostic_journal, latest_diagnostics

BASE = "https://example.invalid/provider/v1"


def response(protocol, model):
    if protocol == Protocol.CHAT:
        value = {"model": model, "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
    elif protocol == Protocol.MESSAGES:
        value = {"model": model, "role": "assistant", "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}
    else:
        value = {"model": model, "output": []}
    return io.BytesIO(json.dumps(value).encode())


def records(protocol, *, child=True, child_context=64):
    rows = [CapabilityRecord(BASE, protocol, "main", {"text": True, "tools": True, "reasoning_effort": True}, "2026-11-04", "synthetic", max_output_tokens=128, context_window_tokens=1024, reasoning_efforts=("low", "high"))]
    if child:
        rows.append(CapabilityRecord(BASE, protocol, "worker", {"text": True, "tools": False, "reasoning_effort": True}, "2026-11-04", "synthetic-child", max_output_tokens=8, context_window_tokens=child_context, reasoning_efforts=("low",)))
    return rows


@pytest.mark.parametrize("protocol", list(Protocol))
@pytest.mark.parametrize("fault", ["tools", "effort", "output", "context"])
def test_child_rejection_uses_child_capabilities_and_budget_before_dispatch(protocol, fault):
    calls = []
    registry = CapabilityRegistry(tuple(records(protocol, child_context=12 if fault == "context" else 64)))
    config = GatewayConfig(protocol, BASE, "fake", "local", capabilities=registry,
                           context_preflight="reject_estimate", token_counter=lambda *_: TokenCount(10, exact=True))
    def upstream(request, *, timeout):
        body = json.loads(request.data)
        calls.append(body)
        return response(protocol, body["model"])
    body = {"model": "worker", "input": "synthetic", "max_output_tokens": 8}
    if fault == "tools":
        body["tools"] = [{"type": "function", "name": "read", "parameters": {"type": "object"}}]
    elif fault == "effort":
        body["reasoning"] = {"effort": "high"}
    elif fault == "output":
        body["max_output_tokens"] = 9
    with listener(config, upstream) as url:
        with pytest.raises(HTTPError) as error:
            post(url, body)
        assert error.value.code == 422
        error.value.close()
        assert not calls
        with post(url, {**body, "model": "main"}) as result:
            assert result.status == 200
    assert [call["model"] for call in calls] == ["main"]


@pytest.mark.parametrize("verified", [False, True])
@pytest.mark.parametrize("route", [False, True])
def test_unknown_child_never_borrows_parent_evidence_or_silently_falls_back(verified, route):
    calls = []
    config = GatewayConfig("chat", BASE, "fake", "local")
    route_record = RouteCapabilityRecord(BASE, "chat", "main", "responses", False, "strict", route_policies(config), {"text": True}, "2026-11-04", "synthetic", "fixture")
    registry = CapabilityRegistry(tuple(records("chat", child=False)), (route_record,))
    config = replace(config, capabilities=registry, require_verified_routes=verified if route else False,
                     require_verified_capabilities=verified if not route else False)
    def upstream(request, *, timeout):
        body = json.loads(request.data)
        calls.append(body)
        return response(Protocol.CHAT, body["model"])
    with listener(config, upstream) as url:
        if verified:
            with pytest.raises(HTTPError) as error:
                post(url, {"model": "worker", "input": "synthetic", "max_output_tokens": 8})
            assert error.value.code == 422
            error.value.close()
            assert not calls
        else:
            with post(url, {"model": "worker", "input": "synthetic", "max_output_tokens": 8}):
                pass
            assert [call["model"] for call in calls] == ["worker"]


def test_both_models_require_their_own_positive_route_evidence_in_verified_mode():
    config = GatewayConfig("chat", BASE, "fake", "local")
    routes = tuple(RouteCapabilityRecord(BASE, "chat", model, "responses", False, "strict", route_policies(config),
                                        {"text": True}, "2026-11-04", "synthetic-" + model, "fixture") for model in ("main", "worker"))
    config = replace(config, capabilities=CapabilityRegistry(tuple(records("chat")), routes), require_verified_routes=True)
    called = []
    def upstream(request, *, timeout):
        body = json.loads(request.data)
        called.append(body["model"])
        return response(Protocol.CHAT, body["model"])
    with listener(config, upstream) as url:
        for model in ("main", "worker"):
            with post(url, {"model": model, "input": "synthetic", "max_output_tokens": 8}):
                pass
    assert called == ["main", "worker"]


def test_model_digests_distinguish_models_without_raw_or_global_identity():
    a, b, other_session = (RequestTrace("responses", "chat") for _ in range(3))
    a.models("private/main", "private/main", b"one-session")
    b.models("private/worker", "private/worker", b"one-session")
    other_session.models("private/main", "private/main", b"different-session")
    assert a.requested_model_digest == a.effective_model_digest
    assert a.requested_model_digest != b.requested_model_digest != other_session.requested_model_digest
    assert a.requested_model_digest != other_session.requested_model_digest
    assert "private/" not in json.dumps(asdict(a.snapshot()))
    a.models("private/main", "private/worker", b"one-session")
    assert a.requested_model_digest != a.effective_model_digest


def test_gateway_records_actual_models_separately_with_one_session_identity():
    observed, calls = [], []
    config = GatewayConfig("chat", BASE, "fake", "local")
    def upstream(request, *, timeout):
        body = json.loads(request.data)
        calls.append(body["model"])
        return response(Protocol.CHAT, body["model"])
    with listener(config, upstream, observer=observed.append) as url:
        for model in ("main", "worker", "main"):
            with post(url, {"model": model, "input": "synthetic"}):
                pass
    assert calls == ["main", "worker", "main"]
    assert len(observed) == 3
    assert observed[0].requested_model_digest == observed[2].requested_model_digest
    assert observed[0].requested_model_digest != observed[1].requested_model_digest
    assert all(item.requested_model_digest == item.effective_model_digest and item.requested_model_digest for item in observed)


@pytest.mark.parametrize("value", [None, {}, "", "x" * 513, chr(0xD800)])
def test_model_trace_never_turns_bad_metadata_into_a_request_failure(value):
    trace = RequestTrace("responses", "chat")
    trace.models(value, value, b"synthetic")
    assert trace.requested_model_digest is trace.effective_model_digest is None


def test_journal_accepts_old_rows_and_validates_new_model_digests(tmp_path):
    trace = RequestTrace("responses", "chat")
    trace.status, trace.outcome = 200, "success"
    trace.models("main", "main", b"synthetic")
    with diagnostic_journal(tmp_path) as sink:
        sink(trace.snapshot())
    report = latest_diagnostics(tmp_path)
    assert report["records"][0]["requested_model_digest"] == trace.requested_model_digest
    path = next(tmp_path.glob("session-*.jsonl"))
    lines = path.read_text(encoding="utf-8").splitlines()
    old = asdict(trace.snapshot())
    old.pop("requested_model_digest")
    old.pop("effective_model_digest")
    path.write_text(lines[0] + "\n" + json.dumps(old) + "\n", encoding="utf-8")
    assert latest_diagnostics(tmp_path)["records"][0]["requested_model_digest"] is None
    old["requested_model_digest"] = "private/raw-model"
    path.write_text(lines[0] + "\n" + json.dumps(old) + "\n", encoding="utf-8")
    assert latest_diagnostics(tmp_path)["records"] == []


@pytest.mark.parametrize("protocol", [Protocol.CHAT, Protocol.MESSAGES])
@pytest.mark.parametrize("opaque", ["reasoning", "previous_response_id"])
def test_child_cannot_inherit_or_fetch_provider_owned_opaque_parent_state(protocol, opaque):
    calls = []
    config = GatewayConfig(protocol, BASE, "fake", "local")
    body = {"model": "worker", "input": "continue", "max_output_tokens": 8}
    if opaque == "reasoning":
        body["input"] = [{"role": "user", "content": "continue"}, {"type": "reasoning", "id": "owner-only", "summary": [], "encrypted_content": "synthetic-parent-state"}]
    else:
        body["previous_response_id"] = "synthetic-parent-response"
    def upstream(*args, **kwargs):
        calls.append(True)
        raise AssertionError("opaque state must fail before network")
    with listener(config, upstream) as url:
        with pytest.raises(HTTPError) as error:
            post(url, body)
        assert error.value.code == 422
        raw = error.value.read()
        assert b"synthetic-parent-state" not in raw and b"synthetic-parent-response" not in raw
        error.value.close()
    assert not calls
