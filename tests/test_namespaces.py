import copy
import json

import pytest

from protoverta.core import ConversionError, Protocol, convert_request, convert_response
from protoverta.namespaces import NamespaceMap
from protoverta.sse import SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.stream_pipe import StreamTranscoder
from protoverta.streaming import Delta, StreamError


def body():
    return {"model": "m", "max_output_tokens": 128, "input": "Hello", "tools": [
        {"type": "function", "name": "lookup", "parameters": {"type": "object"}},
        {"type": "namespace", "name": "crm", "description": "CRM tools", "tools": [{"type": "function", "name": "lookup", "description": "Find contact", "parameters": {"type": "object"}}]},
        {"type": "namespace", "name": "mail", "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}]},
    ]}


@pytest.mark.parametrize("target", ["chat", "messages"])
def test_namespaces_are_distinct_and_preserve_descriptions_and_input(target):
    original = body()
    before = copy.deepcopy(original)
    mapper = NamespaceMap()
    prepared = mapper.prepare(original)
    converted = convert_request(prepared, "responses", target)
    assert len(converted["tools"]) == 3
    assert len({tool["name"] for tool in prepared["tools"]}) == 3
    assert all(len(tool["name"]) <= 64 for tool in prepared["tools"])
    assert prepared["tools"][1]["description"] == "CRM tools\n\nFind contact"
    assert original == before
    assert NamespaceMap().prepare(original) == prepared


def test_history_rebuilds_mapping_even_when_old_tool_definition_is_absent():
    mapper = NamespaceMap()
    request = {"model": "m", "input": [
        {"type": "function_call", "name": "lookup", "namespace": "old", "call_id": "c1", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "old result"},
    ]}
    prepared = mapper.prepare(request)
    converted = convert_request(prepared, "responses", "chat")
    assert converted["messages"][0]["tool_calls"][0]["function"]["name"] == mapper.alias("old", "lookup")
    assert converted["messages"][1]["tool_call_id"] == "c1"


def test_json_tool_response_restores_only_call_name_not_text_or_arguments():
    mapper = NamespaceMap()
    prepared = mapper.prepare(body())
    alias = prepared["tools"][1]["name"]
    response = {"model": "m", "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": alias, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": alias, "arguments": json.dumps({"value": alias})}}]}}]}
    restored = mapper.restore(convert_response(response, "chat", "responses"))
    call = next(item for item in restored["output"] if item["type"] == "function_call")
    assert (call["name"], call["namespace"], call["call_id"]) == ("lookup", "crm", "call_1")
    assert json.loads(call["arguments"])["value"] == alias
    assert alias in str(next(item for item in restored["output"] if item["type"] == "message"))


@pytest.mark.parametrize("source", [Protocol.CHAT, Protocol.MESSAGES])
def test_stream_restores_added_done_and_terminal_snapshot(source):
    mapper = NamespaceMap()
    alias = mapper.prepare(body())["tools"][1]["name"]
    encoder = StreamEncoder(source)
    deltas = [Delta("start", model="m", input_tokens=1, output_tokens=0), Delta("tool_start", index=0, call_id="c1", name=alias), Delta("tool_delta", index=0, text='{"x":1}'), Delta("tool_end", index=0), Delta("finish", reason="tool_calls", input_tokens=1, output_tokens=1)]
    wire = b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta))
    pipe = StreamTranscoder(source, "responses", response_transform=mapper.restore)
    output = b"".join(pipe.feed(wire[i:i + 7]) for i in range(0, len(wire), 7))
    assert b"response.completed" not in output
    output += pipe.finish()
    events = [json.loads(event.data) for event in SSEParser().feed(output)]
    items = [event["item"] for event in events if "item" in event]
    items += events[-1]["response"]["output"]
    items = [item for item in items if item["type"] == "function_call"]
    assert len(items) == 3
    assert all((item["name"], item["namespace"], item["call_id"]) == ("lookup", "crm", "c1") for item in items)


@pytest.mark.parametrize("mutation", ["duplicate", "nested", "custom", "strict", "unknown", "collision"])
def test_unsafe_namespace_shapes_are_rejected(mutation):
    request = body()
    namespace = request["tools"][1]
    if mutation == "duplicate":
        namespace["tools"] *= 2
    elif mutation in {"nested", "custom"}:
        namespace["tools"][0]["type"] = "namespace" if mutation == "nested" else "custom"
    elif mutation == "strict":
        namespace["tools"][0]["strict"] = True
    elif mutation == "unknown":
        namespace["defer_loading"] = True
    else:
        request["tools"][0]["name"] = NamespaceMap().alias("crm", "lookup")
    with pytest.raises(ConversionError):
        NamespaceMap().prepare(request)


def test_unknown_alias_rejects_stream_without_fabricated_success():
    mapper = NamespaceMap()
    mapper.prepare(body())
    encoder = StreamEncoder("responses", response_transform=mapper.restore)
    encoder.feed(Delta("start", model="m", input_tokens=1, output_tokens=0))
    with pytest.raises(StreamError, match="unknown namespace"):
        encoder.feed(Delta("tool_start", index=0, call_id="c1", name="pv_ns_unknown"))


def test_historical_flat_function_cannot_collide_with_generated_alias():
    request = body()
    request["input"] = [{"type": "function_call", "name": NamespaceMap().alias("crm", "lookup"), "call_id": "old", "arguments": "{}"}]
    with pytest.raises(ConversionError, match="collides"):
        NamespaceMap().prepare(request)
