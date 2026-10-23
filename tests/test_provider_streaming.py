import json

import pytest

from protoverta.core import ConversionError, convert_response
from protoverta.sse import SSEEvent, SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.stream_pipe import StreamTranscoder, StreamValidator
from protoverta.streaming import Delta, StreamDecoder, StreamError


def encoded_events(protocol):
    encoder = StreamEncoder(protocol)
    return [event for delta in [Delta("start", model="m"), Delta("text", text="ok"),
                               Delta("finish", reason="stop", input_tokens=3, output_tokens=2)]
            for event in encoder.feed(delta)]


def chat_postlude():
    events = encoded_events("chat")
    usage = json.loads(events[-2].data)["usage"]
    body = json.loads(events[-3].data)
    body["usage"] = usage
    body["choices"][0]["delta"] = {"role": "assistant", "content": ""}
    return events[:-2], body, events[-1]


@pytest.mark.parametrize("target", ["chat", "messages", "responses"])
def test_chat_usage_postlude_with_repeated_empty_finished_choice(target):
    prefix, postlude, done = chat_postlude()
    events = [*prefix, SSEEvent("message", json.dumps(postlude)), done]
    wire = b"".join(encode_sse(e.event, e.data) for e in events)
    pipe = StreamValidator("chat") if target == "chat" else StreamTranscoder("chat", target, messages_usage_policy="deferred")
    output = pipe.feed(wire) + pipe.finish()
    decoder = StreamDecoder(target)
    result = [d for e in SSEParser().feed(output) for d in decoder.feed(e)]
    decoder.finish()
    assert (result[-1].input_tokens, result[-1].output_tokens) == (3, 2)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("mutation", ["content", "finish_reason", "missing_usage", "repeated_usage"])
def test_chat_postlude_does_not_allow_content_or_invalid_finish(native, mutation):
    prefix, body, _done = chat_postlude()
    if mutation == "content":
        body["choices"][0]["delta"]["content"] = "late content"
    elif mutation == "finish_reason":
        body["choices"][0]["finish_reason"] = "length"
    elif mutation == "missing_usage":
        body.pop("usage")
    postlude = encode_sse("message", json.dumps(body))
    pipe = StreamValidator("chat") if native else StreamTranscoder("chat", "responses")
    pipe.feed(b"".join(encode_sse(e.event, e.data) for e in prefix))
    if mutation == "repeated_usage":
        pipe.feed(postlude)
    with pytest.raises(StreamError, match="after|completion"):
        pipe.feed(postlude)


@pytest.mark.parametrize("target", ["chat", "messages", "responses"])
def test_data_only_responses_with_optional_done_sentinel(target):
    events = encoded_events("responses")
    wire = b"".join(b"data: " + e.data.encode() + b"\n\n" for e in events) + b"data: [DONE]\n\n"
    pipe = StreamValidator("responses") if target == "responses" else StreamTranscoder("responses", target, messages_usage_policy="deferred")
    prefix = pipe.feed(wire)
    assert b"message_stop" not in prefix and b"response.completed" not in prefix
    output = prefix + pipe.finish()
    decoder = StreamDecoder(target)
    result = [d for e in SSEParser().feed(output) for d in decoder.feed(e)]
    decoder.finish()
    assert result[-1].reason == "stop"


@pytest.mark.parametrize("native", [False, True])
def test_responses_sentinel_is_not_an_early_or_repeat_terminal(native):
    pipe = StreamValidator("responses") if native else StreamTranscoder("responses", "chat")
    with pytest.raises(StreamError):
        pipe.feed(b"data: [DONE]\n\n")
    pipe = StreamValidator("responses") if native else StreamTranscoder("responses", "chat")
    events = encoded_events("responses")
    pipe.feed(b"".join(encode_sse(e.event, e.data) for e in events) + b"data: [DONE]\n\n")
    with pytest.raises(StreamError, match="after stream completion"):
        pipe.feed(b"data: [DONE]\n\n")


def test_responses_error_without_sequence_is_an_error_not_success():
    decoder = StreamDecoder("responses")
    with pytest.raises(StreamError, match="reported an error"):
        decoder.feed(SSEEvent("message", '{"type":"error","error":{"message":"failed"}}'))


@pytest.mark.parametrize("placeholder", [None, "", []])
def test_json_empty_reasoning_placeholders_do_not_drop_real_reasoning(placeholder):
    body = {"model": "m", "choices": [{"message": {"role": "assistant", "content": "ok", "reasoning": placeholder}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
    assert convert_response(body, "chat", "messages")["content"][0]["text"] == "ok"
    body["choices"][0]["message"]["reasoning"] = "not portable"
    with pytest.raises(ConversionError, match="reasoning"):
        convert_response(body, "chat", "messages")


@pytest.mark.parametrize("citations", [None, []])
def test_empty_citations_are_not_meaningful_citations(citations):
    body = {"model": "m", "content": [{"type": "text", "text": "ok", "citations": citations}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 3, "output_tokens": 2}}
    assert convert_response(body, "messages", "chat")["choices"][0]["message"]["content"] == "ok"
    body["content"][0]["citations"] = [{"type": "web_search_result_location", "url": "https://example.test"}]
    with pytest.raises(ConversionError, match="citations"):
        convert_response(body, "messages", "chat")
