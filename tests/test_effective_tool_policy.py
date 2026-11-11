"""Effective policy authorizes calls before client-visible tool frames."""

import copy
import io
import json
from urllib.error import HTTPError

import pytest
from test_json_wire_boundaries import request
from test_response_snapshot_consistency import listener, snapshots

from protoverta import StreamEncoder, convert_request, convert_response
from protoverta.server import GatewayConfig
from protoverta.sse import SSEParser, encode_sse
from protoverta.streaming import Delta
from protoverta.tool_policy import (
    BoundToolPolicy,
    ToolPolicyViolation,
    normalize_tool_policy,
)

PROTOCOLS = ("chat", "messages", "responses")


def prompt(choice=None, parallel=None, names=("f", "g")):
    body = {
        "model": "m",
        "input": "synthetic",
        "max_output_tokens": 20,
        "tools": [{"type": "function", "name": n, "parameters": {"type": "object"}} for n in names],
    }
    if choice is not None:
        body["tool_choice"] = choice
    if parallel is not None:
        body["parallel_tool_calls"] = parallel
    return body


def reply(source, calls=(), *, stream=False, length=False, text="ok"):
    if not stream:
        message = {"role": "assistant", "content": text}
        if calls:
            message["tool_calls"] = [
                {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": "{}"}}
                for i, n in enumerate(calls)
            ]
        chat = {
            "model": "m",
            "choices": [
                {"message": message, "finish_reason": "length" if length else "tool_calls" if calls else "stop"}
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        }
        return json.dumps(convert_response(chat, "chat", source)).encode()
    encoder = StreamEncoder(source)
    deltas = [Delta("start", model="m", input_tokens=2, output_tokens=0)]
    if text:
        deltas.append(Delta("text", text=text))
    for i, name in enumerate(calls):
        deltas += [
            Delta("tool_start", index=i, call_id=f"c{i}", name=name),
            Delta("tool_delta", index=i, text="{}"),
            Delta("tool_end", index=i),
        ]
    deltas.append(
        Delta(
            "finish", reason="length" if length else "tool_calls" if calls else "stop", input_tokens=2, output_tokens=1
        )
    )
    return b"".join(encode_sse(e.event, e.data) for d in deltas for e in encoder.feed(d))


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("names", [(), ("f",)])
def test_default_policy_is_normalized_on_copy_and_explicit_controls_survive(protocol, names):
    original = convert_request(prompt(names=names), "responses", protocol)
    before = copy.deepcopy(original)
    effective, choice, parallel = normalize_tool_policy(original, protocol)
    guard = BoundToolPolicy(effective, protocol)
    assert choice == ("auto" if names else "none") and parallel is bool(names)
    assert guard.choice == choice and guard.parallel == parallel
    assert original == before
    if names:
        body = convert_request(prompt("none", False, names), "responses", protocol)
        bound, choice, parallel = normalize_tool_policy(body, protocol)
        assert choice == "none" and parallel is False
        with pytest.raises(ToolPolicyViolation):
            BoundToolPolicy(bound, protocol).call("function", "f")


@pytest.mark.parametrize("choice", ["required", {"type": "function", "name": "missing"}])
@pytest.mark.parametrize("source", ["chat", "messages"])
def test_unsatisfiable_tool_choice_rejects_before_any_upstream_request(source, choice):
    calls = []
    cfg = GatewayConfig(source, "https://example.invalid/v1", "synthetic", "local")
    with listener(cfg, lambda *a, **k: calls.append(1)) as url:
        with pytest.raises(HTTPError) as caught:
            request(url, "responses", json.dumps(prompt(choice, names=())).encode())
        assert caught.value.code == 422
        assert caught.value.headers["x-should-retry"] == "false"
        caught.value.close()
    assert calls == []


@pytest.mark.parametrize("source,target", [(a, b) for a in PROTOCOLS for b in PROTOCOLS if a != b])
@pytest.mark.parametrize("stream", [False, True])
def test_forced_single_tool_success_across_all_six_routes(source, target, stream):
    body = convert_request(prompt({"type": "function", "name": "f"}, False), "responses", target)
    body["stream"] = stream
    cfg = GatewayConfig(source, "https://example.invalid/v1", "synthetic", "local", messages_usage_policy="deferred")
    seen = []

    def upstream(req, *, timeout):
        seen.append(json.loads(req.data))
        return io.BytesIO(reply(source, ("f",), stream=stream))

    with listener(cfg, upstream) as url, request(url, target, json.dumps(body).encode()) as response:
        raw = response.read()
        assert response.status == 200
    assert len(seen) == 1 and b"tool_policy_violation" not in raw
    if stream:
        assert not any(e.event == "error" for e in SSEParser().feed(raw))
    else:
        assert json.loads(raw).get("error") is None


VIOLATIONS = [
    ("none", True, ("f",)),
    ("auto", True, ("unknown",)),
    ({"type": "function", "name": "f"}, True, ("g",)),
    ("auto", False, ("f", "g")),
    ("required", True, ()),
    ({"type": "function", "name": "f"}, True, ()),
]


@pytest.mark.parametrize("choice,parallel,returned", VIOLATIONS)
@pytest.mark.parametrize("source", ["chat", "messages"])
@pytest.mark.parametrize("stream", [False, True])
def test_policy_violations_are_non_retryable_and_never_completed(source, stream, choice, parallel, returned):
    seen = []
    cfg = GatewayConfig(source, "https://example.invalid/v1", "synthetic", "local", retry_attempts=2)

    def upstream(req, *, timeout):
        seen.append(1)
        return io.BytesIO(reply(source, returned, stream=stream, text=""))

    body = {**prompt(choice, parallel), "stream": stream}
    with listener(cfg, upstream) as url:
        with pytest.raises(HTTPError) as caught:
            request(url, "responses", json.dumps(body).encode())
        assert caught.value.code == 502 and caught.value.headers["x-should-retry"] == "false"
        error = json.load(caught.value)["error"]
        assert error["code"] == "tool_policy_violation" and error["feature"] == "tool_policy"
        caught.value.close()
    assert seen == [1]


@pytest.mark.parametrize("source", ["chat", "messages"])
def test_late_stream_violation_does_not_expose_unauthorized_tool_or_success(source):
    class Trickle(io.BytesIO):
        def read1(self, size=-1):
            return super().read1(min(size, 1))

    cfg = GatewayConfig(source, "https://example.invalid/v1", "synthetic", "local")
    with (
        listener(cfg, lambda *a, **k: Trickle(reply(source, ("unknown",), stream=True, text="visible"))) as url,
        request(url, "responses", json.dumps({**prompt(), "stream": True}).encode()) as response,
    ):
        assert response.status == 200
        events = [json.loads(e.data) for e in SSEParser().feed(response.read())]
    assert any(e.get("delta") == "visible" for e in events)
    assert events[-1]["code"] == "tool_policy_violation"
    assert not any(
        e.get("type") == "response.completed" or e.get("item", {}).get("type") == "function_call" for e in events
    )


@pytest.mark.parametrize("stream", [False, True])
def test_output_limit_before_required_call_remains_a_truthful_incomplete_response(stream):
    cfg = GatewayConfig("chat", "https://example.invalid/v1", "synthetic", "local")
    with (
        listener(cfg, lambda *a, **k: io.BytesIO(reply("chat", stream=stream, length=True))) as url,
        request(url, "responses", json.dumps({**prompt("required"), "stream": stream}).encode()) as response,
    ):
        raw = response.read()
    final = snapshots(raw)[-1] if stream else json.loads(raw)
    assert final["status"] == "incomplete" and final["incomplete_details"]["reason"] == "max_output_tokens"


def test_undeclared_custom_tool_kind_cannot_masquerade_as_an_authorized_function():
    body, _, _ = normalize_tool_policy(prompt(), "responses")
    with pytest.raises(ToolPolicyViolation):
        BoundToolPolicy(body, "responses").call("custom", "f")


@pytest.mark.parametrize("source", ["chat", "messages"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("mode", ["namespace", "search"])
def test_policy_uses_wire_names_but_snapshot_keeps_effective_logical_tools(source, stream, mode):
    from test_namespaces import body as namespaced_body
    from test_tool_search import body as search_body

    body = namespaced_body() if mode == "namespace" else search_body()
    before = copy.deepcopy(body)
    body["stream"] = stream
    cfg = GatewayConfig(
        source,
        "https://example.invalid/v1",
        "synthetic",
        "local",
        namespace_tools="flatten",
        tool_names="alias",
        client_tool_search="function",
    )
    seen = []

    def upstream(req, *, timeout):
        wire = json.loads(req.data)
        seen.append(wire)
        tool = wire["tools"][1 if mode == "namespace" else 0]
        name = tool.get("function", tool)["name"]
        return io.BytesIO(reply(source, (name,), stream=stream))

    with listener(cfg, upstream) as url, request(url, "responses", json.dumps(body).encode()) as response:
        raw = response.read()
    final = snapshots(raw)[-1] if stream else json.loads(raw)
    assert final["status"] == "completed" and len(seen) == 1
    assert final["tool_choice"] == "auto" and final["parallel_tool_calls"] is True
    if mode == "namespace":
        assert final["tools"][1]["name"] == "crm"
        assert (final["output"][1]["name"], final["output"][1]["namespace"]) == ("lookup", "crm")
    else:
        assert final["tools"] == [before["tools"][0]]  # Unloaded schemas are not active.
        assert final["output"][1]["type"] == "tool_search_call"
        assert "pv_client_tool_search" not in json.dumps(final)


def test_messages_serialization_cannot_hide_an_upstream_parallel_policy_violation():
    from protoverta.stream_pipe import StreamTranscoder

    body, _, _ = normalize_tool_policy(prompt("auto", False), "responses")
    guard = BoundToolPolicy(convert_request(body, "responses", "chat"), "chat")
    pipe = StreamTranscoder("chat", "messages", messages_usage_policy="deferred", tool_policy=guard)
    with pytest.raises(ToolPolicyViolation):
        pipe.feed(reply("chat", ("f", "g"), stream=True))
