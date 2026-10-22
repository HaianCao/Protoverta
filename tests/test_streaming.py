import json

import pytest

from protoverta.sse import SSEEvent, SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.stream_pipe import StreamTranscoder
from protoverta.streaming import Delta, StreamDecoder, StreamError


def event(kind, **body):
    return SSEEvent(kind, json.dumps(body))


def chat(**body):
    return event("message", object="chat.completion.chunk", model="example-model", **body)


def messages(kind, **body):
    return event(kind, type=kind, **body)


def responses(kind, sequence, **body):
    return event(kind, type=kind, sequence_number=sequence, **body)


def feed_all(decoder, events):
    deltas = []
    for item in events:
        deltas.extend(decoder.feed(item))
    decoder.finish()
    return deltas


def test_chat_interleaved_tools_and_usage():
    decoder = StreamDecoder("chat")
    events = [
        chat(choices=[{"index": 0, "delta": {"role": "assistant", "content": "working "}, "finish_reason": None}]),
        chat(choices=[{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_a", "type": "function", "function": {"name": "one", "arguments": '{"a":'}},
            {"index": 1, "id": "call_b", "type": "function", "function": {"name": "two", "arguments": '{"b":'}},
        ]}, "finish_reason": None}]),
        chat(choices=[{"index": 0, "delta": {"tool_calls": [
            {"index": 1, "function": {"arguments": "2}"}},
            {"index": 0, "function": {"arguments": "1}"}},
        ]}, "finish_reason": None}]),
        chat(choices=[{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]),
        chat(choices=[], usage={"prompt_tokens": 12, "completion_tokens": 8}),
        SSEEvent("message", "[DONE]"),
    ]
    deltas = feed_all(decoder, events)
    assert [delta.kind for delta in deltas] == [
        "start", "text", "tool_start", "tool_delta", "tool_start", "tool_delta",
        "tool_delta", "tool_delta", "tool_end", "tool_end", "finish",
    ]
    assert [(delta.index, delta.text) for delta in deltas if delta.kind == "tool_delta"] == [
        (0, '{"a":'), (1, '{"b":'), (1, "2}"), (0, "1}"),
    ]
    assert (deltas[-1].reason, deltas[-1].input_tokens, deltas[-1].output_tokens) == ("tool_calls", 12, 8)


def test_messages_text_and_tool_blocks():
    decoder = StreamDecoder("messages")
    events = [
        messages("message_start", message={"model": "example-model", "usage": {"input_tokens": 5}}),
        messages("content_block_start", index=0, content_block={"type": "text", "text": ""}),
        messages("content_block_delta", index=0, delta={"type": "text_delta", "text": "hello"}),
        messages("content_block_stop", index=0),
        messages("content_block_start", index=1, content_block={"type": "tool_use", "id": "call_a", "name": "one", "input": {}}),
        messages("content_block_delta", index=1, delta={"type": "input_json_delta", "partial_json": '{"a":1}'}),
        messages("content_block_stop", index=1),
        messages("message_delta", delta={"stop_reason": "tool_use"}, usage={"output_tokens": 7}),
        messages("message_stop"),
    ]
    deltas = feed_all(decoder, events)
    assert [delta.kind for delta in deltas] == ["start", "text", "tool_start", "tool_delta", "tool_end", "finish"]
    assert deltas[-1].reason == "tool_calls"
    assert (deltas[-1].input_tokens, deltas[-1].output_tokens) == (5, 7)


def test_responses_text_and_tool():
    decoder = StreamDecoder("responses")
    events = [
        responses("response.created", 0, response={"model": "example-model"}),
        responses("response.output_item.added", 1, output_index=0, item={"id": "msg_1", "type": "message", "role": "assistant"}),
        responses("response.output_text.delta", 2, output_index=0, delta="hi"),
        responses("response.output_item.done", 3, output_index=0, item={"id": "msg_1", "type": "message", "role": "assistant", "status": "completed"}),
        responses("response.output_item.added", 4, output_index=1, item={"id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "lookup"}),
        responses("response.function_call_arguments.delta", 5, output_index=1, delta='{"x":1}'),
        responses("response.output_item.done", 6, output_index=1, item={"id": "fc_1", "type": "function_call", "status": "completed", "call_id": "call_1", "name": "lookup", "arguments": '{"x":1}'}),
        responses("response.completed", 7, response={"model": "example-model", "status": "completed", "usage": {"input_tokens": 4, "output_tokens": 9}}),
    ]
    deltas = feed_all(decoder, events)
    assert [delta.kind for delta in deltas] == ["start", "text", "tool_start", "tool_delta", "tool_end", "finish"]
    assert (deltas[-1].reason, deltas[-1].input_tokens, deltas[-1].output_tokens) == ("tool_calls", 4, 9)


@pytest.mark.parametrize("protocol,partial", [
    ("chat", [chat(choices=[{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}])]),
    ("messages", [messages("message_start", message={"model": "example-model", "usage": {"input_tokens": 1}})]),
    ("responses", [responses("response.created", 0, response={"model": "example-model"})]),
])
def test_interrupted_stream_rejected(protocol, partial):
    decoder = StreamDecoder(protocol)
    for item in partial:
        decoder.feed(item)
    with pytest.raises(StreamError, match="before its terminal"):
        decoder.finish()


def test_invalid_json_is_stream_error():
    with pytest.raises(StreamError, match="invalid JSON"):
        StreamDecoder("chat").feed(SSEEvent("message", "{"))


def test_chat_requires_finish_reason_before_done():
    decoder = StreamDecoder("chat")
    decoder.feed(chat(choices=[{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}]))
    with pytest.raises(StreamError, match="before finish_reason"):
        decoder.feed(SSEEvent("message", "[DONE]"))


def test_messages_cannot_reopen_block_index():
    decoder = StreamDecoder("messages")
    decoder.feed(messages("message_start", message={"model": "example-model", "usage": {"input_tokens": 1}}))
    decoder.feed(messages("content_block_start", index=0, content_block={"type": "text", "text": ""}))
    decoder.feed(messages("content_block_stop", index=0))
    with pytest.raises(StreamError, match="duplicate"):
        decoder.feed(messages("content_block_start", index=0, content_block={"type": "text", "text": ""}))


def test_responses_reject_out_of_order_sequence():
    decoder = StreamDecoder("responses")
    decoder.feed(responses("response.created", 1, response={"model": "example-model"}))
    with pytest.raises(StreamError, match="did not increase"):
        decoder.feed(responses("response.in_progress", 1))


@pytest.mark.parametrize("target", ["chat", "messages", "responses"])
def test_encoder_roundtrip_text_and_tool(target):
    encoder = StreamEncoder(target)
    source = [
        Delta("start", model="example-model", input_tokens=3),
        Delta("text", text="hello"),
        Delta("tool_start", index=0, call_id="call_1", name="lookup"),
        Delta("tool_delta", index=0, text='{"a":'),
        Delta("tool_delta", index=0, text="1}"),
        Delta("tool_end", index=0, call_id="call_1"),
        Delta("finish", reason="tool_calls", input_tokens=3, output_tokens=7),
    ]
    wire = b"".join(encode_sse(item.event, item.data) for delta in source for item in encoder.feed(delta))
    encoder.finish()
    parser = SSEParser()
    decoder = StreamDecoder(target)
    result = []
    for byte in wire:
        for item in parser.feed(bytes([byte])):
            result.extend(decoder.feed(item))
    assert parser.finish() == []
    decoder.finish()
    assert "".join(delta.text for delta in result if delta.kind == "text") == "hello"
    assert "".join(delta.text for delta in result if delta.kind == "tool_delta") == '{"a":1}'
    assert result[-1].reason == "tool_calls"
    assert (result[-1].input_tokens, result[-1].output_tokens) == (3, 7)


def test_messages_target_rejects_unknown_initial_usage():
    encoder = StreamEncoder("messages")
    with pytest.raises(StreamError, match="input_tokens at start"):
        encoder.feed(Delta("start", model="example-model"))


def test_responses_target_rejects_missing_final_usage():
    encoder = StreamEncoder("responses")
    encoder.feed(Delta("start", model="example-model"))
    with pytest.raises(StreamError, match="requires input and output"):
        encoder.feed(Delta("finish", reason="stop"))


def test_messages_target_rejects_interleaved_tool_calls():
    encoder = StreamEncoder("messages")
    encoder.feed(Delta("start", model="example-model", input_tokens=2))
    encoder.feed(Delta("tool_start", index=0, call_id="call_a", name="a"))
    with pytest.raises(StreamError, match="cannot interleave"):
        encoder.feed(Delta("tool_start", index=1, call_id="call_b", name="b"))


def test_transcoder_rejects_interrupted_upstream_after_partial_output():
    pipe = StreamTranscoder("messages", "chat")
    wire = encode_sse("message_start", json.dumps({
        "type": "message_start", "message": {"model": "m", "content": [], "usage": {"input_tokens": 1}},
    }))
    assert pipe.feed(wire)
    with pytest.raises(StreamError, match="before its terminal"):
        pipe.finish()


def test_transcoder_rejects_chat_to_messages_before_emitting_bytes():
    with pytest.raises(StreamError, match="input_tokens at stream start"):
        StreamTranscoder("chat", "messages")
