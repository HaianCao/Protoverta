import copy
import json

import pytest

from protoverta import ConversionError, convert_response
from protoverta.sse import SSEEvent
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta, StreamDecoder, StreamError


@pytest.mark.parametrize("protocol", ["chat", "messages"])
def test_unknown_tool_fields_are_rejected_in_json_and_sse(protocol):
    if protocol == "chat":
        call = {"id": "c1", "type": "function", "function": {"name": "add", "arguments": "{}", "unknown_semantics": True}}
        response = {"model": "m", "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [call]}}]}
        event = SSEEvent("message", json.dumps({"object": "chat.completion.chunk", "model": "m", "choices": [{"index": 0, "finish_reason": None, "delta": {"tool_calls": [{"index": 0, **call}]}}]}))
        decoder = StreamDecoder("chat")
    else:
        block = {"type": "tool_use", "id": "c1", "name": "add", "input": {}, "toolset_name": "browser"}
        response = {"model": "m", "content": [block], "stop_reason": "tool_use"}
        decoder = StreamDecoder("messages")
        decoder.feed(SSEEvent("message_start", json.dumps({"type": "message_start", "message": {"model": "m"}})))
        event = SSEEvent("content_block_start", json.dumps({"type": "content_block_start", "index": 0, "content_block": block}))
    with pytest.raises(ConversionError, match="unsupported fields"):
        convert_response(response, protocol, "responses")
    with pytest.raises(StreamError, match="unsupported fields"):
        decoder.feed(event)


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
def test_repeated_call_id_is_rejected_before_second_stream_tool_start(protocol):
    encoder = StreamEncoder(protocol)
    decoder = StreamDecoder(protocol)
    start = Delta("start", model="m", input_tokens=1, output_tokens=0)
    for event in encoder.feed(start):
        decoder.feed(event)
    first = encoder.feed(Delta("tool_start", index=0, call_id="same_id", name="add"))
    for event in first:
        decoder.feed(event)
    with pytest.raises(StreamError, match="duplicate.*tool call ID"):
        encoder.feed(Delta("tool_start", index=1, call_id="same_id", name="add"))
    # Construct a second source event with a different block/index but same ID.
    if protocol == "chat":
        body = json.loads(first[0].data)
        body["choices"][0]["delta"]["tool_calls"][0]["index"] = 1
        event = SSEEvent(first[0].event, json.dumps(body))
    elif protocol == "messages":
        body = json.loads(first[0].data)
        body["index"] = 1
        event = SSEEvent(first[0].event, json.dumps(body))
    else:
        body = json.loads(first[0].data)
        body.update(output_index=1, sequence_number=body["sequence_number"] + 1)
        body["item"]["id"] = "other_item"
        event = SSEEvent(first[0].event, json.dumps(body))
    with pytest.raises(StreamError, match="duplicate.*tool call ID"):
        decoder.feed(event)


def test_json_duplicate_tool_call_ids_are_not_forwarded():
    call = {"id": "same", "type": "function", "function": {"name": "add", "arguments": "{}"}}
    response = {"model": "m", "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [call, copy.deepcopy(call)]}}]}
    with pytest.raises(ConversionError, match="duplicate response tool call ID"):
        convert_response(response, "chat", "responses")


@pytest.mark.parametrize("index", [0, True, -1, 1, "0"])
def test_completed_chat_json_call_index_must_agree_with_array_position(index):
    from protoverta import convert_request

    call = {"index": index, "id": "c1", "type": "function", "function": {"name": "add", "arguments": "{}"}}
    message = {"role": "assistant", "content": None, "tool_calls": [call]}
    response = {"model": "m", "choices": [{"finish_reason": "tool_calls", "message": message}]}
    request = {"model": "m", "messages": [message]}
    if type(index) is int and index == 0:
        assert convert_response(response, "chat", "responses")["output"][0]["call_id"] == "c1"
        assert convert_request(request, "chat", "responses")["input"][0]["call_id"] == "c1"
    else:
        with pytest.raises(ConversionError, match="array position"):
            convert_response(response, "chat", "responses")
        with pytest.raises(ConversionError, match="array position"):
            convert_request(request, "chat", "responses")


def test_malformed_format_discriminator_reports_conversion_error():
    from protoverta import convert_request

    with pytest.raises(ConversionError):
        convert_request({"model": "m", "input": "x", "text": {"format": {"type": []}}}, "responses", "chat")


def test_chat_unknown_empty_delta_fields_are_not_silently_ignored():
    event = SSEEvent("message", json.dumps({"object": "chat.completion.chunk", "model": "m", "choices": [{"index": 0, "finish_reason": None, "delta": {"unknown_semantics": {}}}]}))
    with pytest.raises(StreamError, match="unknown_semantics"):
        StreamDecoder("chat").feed(event)


def test_upstream_responses_namespace_is_rejected_in_json_and_stream():
    item = {"type": "function_call", "id": "fc1", "call_id": "c1", "name": "add", "namespace": "math", "arguments": "{}", "status": "completed"}
    response = {"model": "m", "status": "completed", "output": [item]}
    with pytest.raises(ConversionError, match="namespace"):
        convert_response(response, "responses", "chat")
    decoder = StreamDecoder("responses")
    decoder.feed(SSEEvent("response.created", json.dumps({"type": "response.created", "sequence_number": 0, "response": {"model": "m"}})))
    with pytest.raises(StreamError, match="namespace"):
        decoder.feed(SSEEvent("response.output_item.added", json.dumps({"type": "response.output_item.added", "sequence_number": 1, "output_index": 0, "item": dict(item, arguments="", status="in_progress")})))
    # Native payload forwarding continues to preserve namespace fields.
    assert convert_response(response, "responses", "responses")["output"][0]["namespace"] == "math"
