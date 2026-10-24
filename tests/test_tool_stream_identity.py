import json

import pytest

from protoverta.sse import SSEEvent, SSEParser, encode_sse
from protoverta.stream_pipe import StreamTranscoder
from protoverta.streaming import StreamDecoder, StreamError


def chunk(tools=None, *, reason=None, usage=None):
    body = {"object": "chat.completion.chunk", "model": "m", "choices": [{"index": 0, "delta": {"tool_calls": tools} if tools is not None else {}, "finish_reason": reason}]}
    if usage is not None:
        body.update(choices=[], usage=usage)
    return SSEEvent("message", json.dumps(body))


def test_sparse_parallel_calls_keep_id_arguments_and_first_start_order():
    events = [chunk([{"index": 31, "id": "b", "function": {"name": "second", "arguments": '{"b":'}}, {"index": 2, "id": "a", "function": {"name": "first", "arguments": '{"a":'}}]), chunk([{"index": 2, "function": {"arguments": "1}"}}, {"index": 31, "function": {"arguments": "2}"}}]), chunk(reason="tool_calls"), chunk(usage={"prompt_tokens": 2, "completion_tokens": 4}), SSEEvent("message", "[DONE]")]
    wire = b"".join(encode_sse(event.event, event.data) for event in events)
    pipe = StreamTranscoder("chat", "messages", messages_usage_policy="deferred")
    output = b"".join(pipe.feed(wire[index:index + 1]) for index in range(len(wire))) + pipe.finish()
    result = [json.loads(event.data) for event in SSEParser().feed(output)]
    calls = [event for event in result if event["type"] == "content_block_start"]
    assert [(item["content_block"]["id"], item["content_block"]["name"]) for item in calls] == [("b", "second"), ("a", "first")]
    arguments = {}
    for event in result:
        if event["type"] == "content_block_delta":
            arguments[event["index"]] = arguments.get(event["index"], "") + event["delta"]["partial_json"]
    assert [json.loads(arguments[item["index"]]) for item in calls] == [{"b": 2}, {"a": 1}]


@pytest.mark.parametrize("partial", [{"id": "a", "function": {"arguments": "{}"}}, {"index": True, "function": {"arguments": "{}"}}, {"index": 0, "id": "changed", "function": {"arguments": "{}"}}, {"index": 1, "id": "a", "function": {"name": "other", "arguments": "{}"}}, {"index": 0, "function": {"name": "changed"}}])
def test_ambiguous_or_changed_identity_is_rejected_not_inferred(partial):
    decoder = StreamDecoder("chat")
    decoder.feed(chunk([{"index": 0, "id": "a", "function": {"name": "one", "arguments": ""}}]))
    with pytest.raises(StreamError):
        decoder.feed(chunk([partial]))
