"""Unknown request policy is omitted; explicit per-request context is preserved."""

import copy
import io
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_json_wire_boundaries import listener, request
from wire_cases import source_wire

from protoverta import (
    ConversionError,
    StreamEncoder,
    StreamTranscoder,
    bind_response_context,
    convert_response,
)
from protoverta.response_context import POLICY_FIELDS, restore_request_context
from protoverta.sse import SSEParser


def chat_result(count=0):
    message = {"role": "assistant", "content": "synthetic"}
    if count:
        message["tool_calls"] = [{"id": f"call_{i}", "type": "function", "function": {"name": "lookup", "arguments": "{}"}} for i in range(count)]
    return {"model": "m", "choices": [{"message": message, "finish_reason": "tool_calls" if count else "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1}}


def context(parallel=True):
    return {"parallel_tool_calls": parallel, "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
            "tool_choice": {"type": "function", "name": "lookup"}}


@pytest.mark.parametrize("source", ["chat", "messages"])
@pytest.mark.parametrize("count", [0, 1, 2])
def test_completed_response_never_infers_policy_from_call_count(source, count):
    body = convert_response(chat_result(count), "chat", source)
    output = convert_response(body, source, "responses")
    assert not set(POLICY_FIELDS) & output.keys()
    assert len([item for item in output["output"] if item["type"] == "function_call"]) == count


@pytest.mark.parametrize("source", ["chat", "messages"])
@pytest.mark.parametrize("parallel", [True, False])
def test_completed_response_uses_only_explicit_request_context(source, parallel):
    original = context(parallel)
    before = copy.deepcopy(original)
    body = convert_response(chat_result(1), "chat", source)
    output = convert_response(body, source, "responses", request_context=original)
    expected = {**original, "tools": [{**original["tools"][0], "description": None, "strict": None}]}
    assert {field: output[field] for field in POLICY_FIELDS} == expected
    output["tools"].clear()
    assert original == before


@pytest.mark.parametrize("source", ["chat", "messages"])
@pytest.mark.parametrize("metadata", [None, {}, {"parallel_tool_calls": False}, {"tools": [], "tool_choice": "none", "parallel_tool_calls": True}])
def test_stream_snapshots_have_matching_known_policy_only(source, metadata):
    metadata = copy.deepcopy(metadata)
    original = copy.deepcopy(metadata)
    pipe = StreamTranscoder(source, "responses", request_context=metadata)
    if metadata:
        metadata["parallel_tool_calls"] = "mutated after construction"
        if "tools" in metadata:
            metadata["tools"].append({"type": "function", "name": "late"})
    raw = pipe.feed(source_wire(source)) + pipe.finish()
    snapshots = [json.loads(event.data)["response"] for event in SSEParser().feed(raw) if "response" in json.loads(event.data)]
    assert snapshots[0]["status"] == "in_progress" and snapshots[-1]["status"] == "completed"
    for snapshot in snapshots:
        assert {field: snapshot[field] for field in POLICY_FIELDS if field in snapshot} == (original or {})


def test_restore_context_removes_stale_guessed_policy_but_preserves_output():
    original = {"output": [], "parallel_tool_calls": True, "tools": [], "tool_choice": "auto"}
    result = restore_request_context(original, {})
    assert result["output"] == [] and not set(POLICY_FIELDS) & result.keys()
    assert original["parallel_tool_calls"] is True


def test_context_binding_is_isolated_between_concurrent_streams_and_json():
    def run(index):
        metadata = {"parallel_tool_calls": bool(index % 2), "tools": [], "tool_choice": "none", "instructions": str(index)}
        transform = bind_response_context(metadata)
        metadata["instructions"] = "late mutation"
        assert transform({"output": []})["instructions"] == str(index)
        result = convert_response(chat_result(), "chat", "responses", request_context={"parallel_tool_calls": bool(index % 2)})
        assert result["parallel_tool_calls"] is bool(index % 2)
        assert "tools" not in result and "tool_choice" not in result

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(run, range(32)))


@pytest.mark.parametrize("metadata", [[], {"parallel_tool_calls": "yes"}, {"tools": False}, {"tools": [1]}, {"tool_choice": 0}, {"tool_choice": "invalid"}, {"max_output_tokens": True}, {"temperature": float("nan")}, {"instructions": 1}])
def test_invalid_context_is_rejected_before_conversion_or_stream_start(metadata):
    with pytest.raises(ConversionError):
        bind_response_context(metadata)
    with pytest.raises(ConversionError):
        convert_response(chat_result(), "chat", "responses", request_context=metadata)
    with pytest.raises(ConversionError):
        StreamTranscoder("chat", "responses", request_context=metadata)


def test_context_cannot_rewrite_native_or_other_protocol_output():
    native = convert_response(chat_result(), "chat", "responses")
    with pytest.raises(ConversionError):
        convert_response(native, "responses", "responses", request_context={})
    with pytest.raises(ConversionError):
        convert_response(chat_result(), "chat", "messages", request_context={})
    with pytest.raises(ValueError):
        StreamEncoder("chat", request_context={})
    with pytest.raises(ValueError):
        StreamTranscoder("responses", "responses", request_context={})


@pytest.mark.parametrize("source", ["chat", "messages"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_gateway_metadata_matches_original_explicit_request_only(source, stream, explicit):
    metadata = {"tools": [], "tool_choice": "none", "parallel_tool_calls": False} if explicit else {}
    body = {"model": "m", "input": "synthetic", "max_output_tokens": 20, "stream": stream, **metadata}
    raw = source_wire(source, tools=False) if stream else json.dumps(convert_response(chat_result(), "chat", source)).encode()
    with listener(source, lambda req, timeout: io.BytesIO(raw)) as origin, request(origin, "responses", json.dumps(body).encode()) as response:
        encoded = response.read()
    snapshots = ([json.loads(event.data)["response"] for event in SSEParser().feed(encoded) if "response" in json.loads(event.data)]
                 if stream else [json.loads(encoded)])
    assert snapshots
    for snapshot in snapshots:
        assert {field: snapshot[field] for field in POLICY_FIELDS if field in snapshot} == metadata
