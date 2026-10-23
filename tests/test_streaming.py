import json

import pytest

from protoverta.sse import SSEEvent, SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.stream_pipe import StreamTranscoder, StreamValidator
from protoverta.streaming import Delta, StreamDecoder, StreamError


def test_native_messages_preserves_thinking_and_signature_blocks():
    bodies = [
        ("message_start", {"type": "message_start", "message": {"model": "m"}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "private fixture"}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "opaque-fixture"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    wire = b"".join(encode_sse(kind, json.dumps(body)) for kind, body in bodies)
    validator = StreamValidator("messages")
    forwarded = validator.feed(wire)
    assert b"opaque-fixture" in forwarded
    assert b"message_stop" not in forwarded
    assert b"message_stop" in validator.finish()


def test_native_responses_preserves_reasoning_item():
    bodies = [
        ("response.created", {"response": {"status": "in_progress"}}),
        ("response.output_item.added", {"output_index": 0, "item": {"type": "reasoning", "id": "rs_fixture"}}),
        ("response.output_item.done", {"output_index": 0, "item": {"type": "reasoning", "id": "rs_fixture", "encrypted_content": "opaque-fixture"}}),
        ("response.completed", {"response": {"status": "completed"}}),
    ]
    wire = b"".join(encode_sse(kind, json.dumps({"type": kind, "sequence_number": seq, **body})) for seq, (kind, body) in enumerate(bodies))
    validator = StreamValidator("responses")
    forwarded = validator.feed(wire)
    assert b"opaque-fixture" in forwarded
    assert b"response.completed" not in forwarded
    assert b"response.completed" in validator.finish()


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
        messages("message_start", message={"model": "example-model", "usage": {"input_tokens": 5, "output_tokens": 1}}),
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
    assert deltas[0].output_tokens == 1
    assert deltas[-1].reason == "tool_calls"
    assert (deltas[-1].input_tokens, deltas[-1].output_tokens) == (5, 7)


def test_responses_text_and_tool():
    decoder = StreamDecoder("responses")
    final_text = {"id": "msg_1", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "hi", "annotations": [], "logprobs": []}]}
    final_tool = {"id": "fc_1", "type": "function_call", "status": "completed", "call_id": "call_1", "name": "lookup", "arguments": '{"x":1}'}
    events = [
        responses("response.created", 0, response={"model": "example-model"}),
        responses("response.output_item.added", 1, output_index=0, item={"id": "msg_1", "type": "message", "role": "assistant", "content": []}),
        responses("response.content_part.added", 2, item_id="msg_1", output_index=0, content_index=0, part={"type": "output_text", "text": "", "annotations": [], "logprobs": []}),
        responses("response.output_text.delta", 3, item_id="msg_1", output_index=0, content_index=0, delta="hi", logprobs=[]),
        responses("response.output_text.done", 4, item_id="msg_1", output_index=0, content_index=0, text="hi", logprobs=[]),
        responses("response.content_part.done", 5, item_id="msg_1", output_index=0, content_index=0, part={"type": "output_text", "text": "hi", "annotations": [], "logprobs": []}),
        responses("response.output_item.done", 6, output_index=0, item=final_text),
        responses("response.output_item.added", 7, output_index=1, item={"id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "lookup"}),
        responses("response.function_call_arguments.delta", 8, item_id="fc_1", output_index=1, delta='{"x":1}'),
        responses("response.function_call_arguments.done", 9, item_id="fc_1", output_index=1, arguments='{"x":1}'),
        responses("response.output_item.done", 10, output_index=1, item=final_tool),
        responses("response.completed", 11, response={"model": "example-model", "status": "completed", "output": [final_text, final_tool], "usage": {"input_tokens": 4, "output_tokens": 9}}),
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
        Delta("start", model="example-model", input_tokens=3, output_tokens=1),
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
    with pytest.raises(StreamError, match="input_tokens and output_tokens at start"):
        encoder.feed(Delta("start", model="example-model"))


def test_responses_target_rejects_missing_final_usage():
    encoder = StreamEncoder("responses")
    encoder.feed(Delta("start", model="example-model"))
    with pytest.raises(StreamError, match="requires input and output"):
        encoder.feed(Delta("finish", reason="stop"))


def test_messages_target_rejects_interleaved_tool_calls():
    encoder = StreamEncoder("messages")
    encoder.feed(Delta("start", model="example-model", input_tokens=2, output_tokens=1))
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


def test_responses_encoder_event_schema_has_item_identity_and_empty_logprobs():
    encoder = StreamEncoder("responses")
    deltas = [
        Delta("start", model="m"),
        Delta("text", text="hello"),
        Delta("tool_start", index=0, call_id="call_1", name="lookup"),
        Delta("tool_delta", index=0, text="{}"),
        Delta("tool_end", index=0),
        Delta("finish", reason="tool_calls", input_tokens=2, output_tokens=3),
    ]
    bodies = [json.loads(item.data) for delta in deltas for item in encoder.feed(delta)]
    created = bodies[0]["response"]
    completed = bodies[-1]["response"]
    for snapshot in (created, completed):
        assert isinstance(snapshot["created_at"], int)
        assert snapshot["object"] == "response"
        assert snapshot["model"] == "m"
        assert {"id", "status", "output", "usage", "error", "incomplete_details", "parallel_tool_calls", "text", "tools"} <= snapshot.keys()
    assert created["status"] == "in_progress" and created["output"] == []
    assert completed["status"] == "completed" and len(completed["output"]) == 2
    assert completed["usage"] == {
        "input_tokens": 2,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 3,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 5,
    }
    text_id = next(body["item"]["id"] for body in bodies if body["type"] == "response.output_item.added" and body["item"]["type"] == "message")
    tool_id = next(body["item"]["id"] for body in bodies if body["type"] == "response.output_item.added" and body["item"]["type"] == "function_call")
    for body in bodies:
        kind = body["type"]
        if kind in {"response.content_part.added", "response.content_part.done", "response.output_text.delta", "response.output_text.done"}:
            assert body["item_id"] == text_id
            assert body["content_index"] == 0
        if kind in {"response.output_text.delta", "response.output_text.done"}:
            assert body["logprobs"] == []
        if kind in {"response.function_call_arguments.delta", "response.function_call_arguments.done"}:
            assert body["item_id"] == tool_id


@pytest.mark.parametrize("kind,part", [
    ("response.content_part.added", {"type": "refusal", "refusal": "no"}),
    ("response.content_part.added", {"type": "output_text", "text": "", "annotations": [{"type": "url_citation"}], "logprobs": []}),
    ("response.content_part.added", {"type": "output_text", "text": "", "annotations": [], "logprobs": [{"token": "x"}]}),
])
def test_responses_rejects_unsupported_content_metadata(kind, part):
    decoder = StreamDecoder("responses")
    decoder.feed(responses("response.created", 0, response={"model": "m"}))
    decoder.feed(responses("response.output_item.added", 1, output_index=0, item={"id": "msg_1", "type": "message", "role": "assistant", "content": []}))
    with pytest.raises(StreamError, match="unsupported Responses content part metadata"):
        decoder.feed(responses(kind, 2, output_index=0, content_index=0, item_id="msg_1", part=part))


def test_transcoder_rejects_partial_event_after_terminal_marker():
    pipe = StreamTranscoder("chat", "responses")
    chunks = [
        chat(choices=[{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}]),
        chat(choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}]),
        chat(choices=[], usage={"prompt_tokens": 1, "completion_tokens": 1}),
        SSEEvent("message", "[DONE]"),
    ]
    wire = b"".join(encode_sse(item.event, item.data) for item in chunks)
    pipe.feed(wire + b"data: incomplete")
    with pytest.raises(StreamError, match="incomplete event"):
        pipe.finish()


def test_sse_parser_rejects_oversize_event():
    parser = SSEParser(max_event_chars=8)
    with pytest.raises(ValueError, match="size limit"):
        parser.feed(b"data: too-long")


def test_messages_encoder_preserves_initial_output_usage():
    encoder = StreamEncoder("messages")
    start = json.loads(encoder.feed(Delta("start", model="m", input_tokens=4, output_tokens=2))[0].data)
    assert start["message"]["usage"] == {"input_tokens": 4, "output_tokens": 2}


def test_chat_two_interleaved_tools_transcode_to_responses():
    pipe = StreamTranscoder("chat", "responses")
    upstream = [
        chat(choices=[{"index": 0, "delta": {"role": "assistant", "tool_calls": [
            {"index": 0, "id": "call_a", "function": {"name": "a", "arguments": '{"x":'}},
            {"index": 1, "id": "call_b", "function": {"name": "b", "arguments": '{"y":'}},
        ]}, "finish_reason": None}]),
        chat(choices=[{"index": 0, "delta": {"tool_calls": [
            {"index": 1, "function": {"arguments": "2}"}},
            {"index": 0, "function": {"arguments": "1}"}},
        ]}, "finish_reason": None}]),
        chat(choices=[{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]),
        chat(choices=[], usage={"prompt_tokens": 8, "completion_tokens": 4}),
        SSEEvent("message", "[DONE]"),
    ]
    output = b"".join(pipe.feed(encode_sse(item.event, item.data)) for item in upstream)
    output += pipe.finish()
    parser = SSEParser()
    events = parser.feed(output)
    assert parser.finish(strict=True) == []
    decoded = feed_all(StreamDecoder("responses"), events)
    assert [delta.kind for delta in decoded].count("tool_start") == 2
    assert [delta.kind for delta in decoded].count("tool_end") == 2
    assert {(delta.call_id, delta.name) for delta in decoded if delta.kind == "tool_start"} == {("call_a", "a"), ("call_b", "b")}
    assert decoded[-1].reason == "tool_calls"


def test_messages_upstream_error_never_generates_successful_terminal_event():
    pipe = StreamTranscoder("messages", "chat")
    pipe.feed(encode_sse("message_start", json.dumps({
        "type": "message_start", "message": {"model": "m", "content": [], "usage": {"input_tokens": 1, "output_tokens": 1}},
    })))
    with pytest.raises(StreamError, match="reported an error"):
        pipe.feed(encode_sse("error", json.dumps({"type": "error", "error": {"type": "overloaded_error"}})))


def test_responses_final_output_cannot_silently_add_items():
    decoder = StreamDecoder("responses")
    decoder.feed(responses("response.created", 0, response={"model": "m"}))
    with pytest.raises(StreamError, match="final output differs"):
        decoder.feed(responses("response.completed", 1, response={
            "model": "m", "status": "completed", "output": [{"type": "reasoning", "id": "rs_1"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }))


def test_terminal_event_withheld_until_verified_eof():
    start = chat(choices=[{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}])
    stop = chat(choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}])
    done = SSEEvent("message", "[DONE]")
    wire = b"".join(encode_sse(item.event, item.data) for item in (start, stop, done))
    validator = StreamValidator("chat")
    forwarded = validator.feed(wire)
    assert b"[DONE]" not in forwarded
    assert b"[DONE]" in validator.finish()
    with pytest.raises(StreamError, match="already finished"):
        validator.finish()

    transcoder = StreamTranscoder("chat", "responses")
    usage = chat(choices=[], usage={"prompt_tokens": 1, "completion_tokens": 1})
    transcode_wire = b"".join(encode_sse(item.event, item.data) for item in (start, stop, usage, done))
    output = transcoder.feed(transcode_wire)
    assert b"response.completed" not in output
    assert b"response.completed" in transcoder.finish()
    with pytest.raises(StreamError, match="already finished"):
        transcoder.feed(b"")


def test_terminal_is_discarded_on_trailing_garbage():
    start = chat(choices=[{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}])
    stop = chat(choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}])
    done = SSEEvent("message", "[DONE]")
    wire = b"".join(encode_sse(item.event, item.data) for item in (start, stop, done))
    validator = StreamValidator("chat")
    assert b"[DONE]" not in validator.feed(wire + b"data: trailing")
    with pytest.raises(StreamError, match="incomplete event"):
        validator.finish()


def test_length_finish_roundtrips_as_incomplete_responses_item():
    encoder = StreamEncoder("responses")
    events = []
    for delta in [
        Delta("start", model="m"),
        Delta("text", text="partial"),
        Delta("finish", reason="length", input_tokens=2, output_tokens=3),
    ]:
        events.extend(encoder.feed(delta))
    bodies = [json.loads(item.data) for item in events]
    done_item = next(body["item"] for body in bodies if body["type"] == "response.output_item.done")
    assert done_item["status"] == "incomplete"
    assert bodies[-1]["response"]["incomplete_details"] == {"reason": "max_output_tokens"}
    decoded = feed_all(StreamDecoder("responses"), events)
    assert decoded[-1].reason == "length"
