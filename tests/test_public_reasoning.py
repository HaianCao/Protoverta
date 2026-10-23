import copy
import io
import json

import pytest
from test_gateway_capabilities import listener, post

from protoverta import (
    ConversionError,
    StreamDecoder,
    StreamTranscoder,
    convert_request,
    convert_response,
)
from protoverta.reasoning import reasoning_item
from protoverta.server import GatewayConfig
from protoverta.sse import SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta, StreamError


def chat_result():
    return {"model": "m", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "reasoning_content": "public analysis", "content": "answer"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 4}}


def test_completed_public_reasoning_is_distinct_from_answer_and_replayable():
    body = chat_result()
    original = copy.deepcopy(body)
    with pytest.raises(ConversionError, match="reasoning"):
        convert_response(body, "chat", "responses")
    result = convert_response(body, "chat", "responses", public_reasoning=True)
    assert result["output"][0]["content"] == [{"type": "reasoning_text", "text": "public analysis"}]
    assert result["output"][0]["summary"] == []
    assert result["output"][1]["content"][0]["text"] == "answer"
    replay = convert_request({"model": "m", "input": result["output"]}, "responses", "chat", public_reasoning=True)
    assert replay["messages"] == [body["choices"][0]["message"]]
    restored = convert_response(result, "responses", "chat", public_reasoning=True)
    assert restored["choices"][0]["message"] == body["choices"][0]["message"]
    assert body == original


@pytest.mark.parametrize("field,value", [("encrypted_content", "opaque"), ("signature", "signed"), ("summary", [{"type": "summary_text", "text": "summary"}]), ("content", [{"type": "reasoning_text", "text": "x", "signature": "signed"}])])
def test_reasoning_summary_signature_and_opaque_state_are_not_laundered(field, value):
    item = reasoning_item("public")
    item[field] = value
    with pytest.raises(ConversionError):
        convert_request({"model": "m", "input": [item]}, "responses", "chat", public_reasoning=True)


@pytest.mark.parametrize("field", ["reasoning", "reasoning_details"])
def test_other_chat_reasoning_extensions_remain_errors(field):
    body = chat_result()
    body["choices"][0]["message"][field] = "unknown"
    with pytest.raises(ConversionError):
        convert_response(body, "chat", "responses", public_reasoning=True)


def test_messages_never_gets_fabricated_thinking_or_signature():
    with pytest.raises(ConversionError, match="signatures"):
        convert_response(chat_result(), "chat", "messages", public_reasoning=True)
    with pytest.raises(ConversionError, match="signatures"):
        convert_request({"model": "m", "max_tokens": 32, "messages": [chat_result()["choices"][0]["message"]]}, "chat", "messages", public_reasoning=True)


def wire(protocol, *, tools=False):
    encoder = StreamEncoder(protocol, public_reasoning=True)
    deltas = [Delta("start", model="m", input_tokens=3, output_tokens=0), Delta("reasoning_text", text="public "), Delta("reasoning_text", text="analysis")]
    if tools:
        deltas += [Delta("tool_start", index=0, call_id="c", name="lookup"), Delta("tool_delta", index=0, text="{}"), Delta("tool_end", index=0)]
    else:
        deltas += [Delta("text", text="answer")]
    deltas += [Delta("finish", reason="tool_calls" if tools else "stop", input_tokens=3, output_tokens=4)]
    return b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta))


@pytest.mark.parametrize("source,target", [("chat", "responses"), ("responses", "chat")])
@pytest.mark.parametrize("tools", [False, True])
def test_stream_reasoning_and_answer_or_tool_remain_separate(source, target, tools):
    original = wire(source, tools=tools)
    pipeline = StreamTranscoder(source, target, public_reasoning=True)
    output = b"".join(pipeline.feed(original[index:index + 7]) for index in range(0, len(original), 7)) + pipeline.finish()
    decoder = StreamDecoder(target, public_reasoning=True)
    deltas = [delta for event in SSEParser().feed(output) for delta in decoder.feed(event)]
    decoder.finish()
    assert "".join(delta.text for delta in deltas if delta.kind == "reasoning_text") == "public analysis"
    assert "".join(delta.text for delta in deltas if delta.kind == "text") == ("" if tools else "answer")
    assert deltas[-1].kind == "finish"
    assert any(delta.kind == "tool_start" for delta in deltas) == tools


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_strict_default_rejects_public_reasoning_stream(protocol):
    decoder = StreamDecoder(protocol)
    with pytest.raises(StreamError):
        for event in SSEParser().feed(wire(protocol)):
            decoder.feed(event)


def test_interleaved_reasoning_is_not_reordered():
    encoder = StreamEncoder("responses", public_reasoning=True)
    encoder.feed(Delta("start", model="m"))
    encoder.feed(Delta("text", text="answer first"))
    with pytest.raises(StreamError):
        encoder.feed(Delta("reasoning_text", text="too late"))
    request = {"model": "m", "input": [{"role": "assistant", "content": "answer first"}, reasoning_item("too late")]}
    with pytest.raises(ConversionError):
        convert_request(request, "responses", "chat", public_reasoning=True)


@pytest.mark.parametrize("stream", [False, True])
def test_gateway_public_reasoning_is_explicit_and_kept_outside_answer(stream):
    def upstream(request, *, timeout):
        assert json.loads(request.data)["messages"][0]["content"] == "Hi"
        return io.BytesIO(wire("chat") if stream else json.dumps(chat_result()).encode())

    config = GatewayConfig("chat", "https://example.test/v1", "key", "local", public_reasoning=True)
    with listener(config, upstream) as url, post(url, {"model": "m", "input": "Hi", "stream": stream}) as response:
        data = response.read()
    if stream:
        decoder = StreamDecoder("responses", public_reasoning=True)
        deltas = [delta for event in SSEParser().feed(data) for delta in decoder.feed(event)]
        decoder.finish()
        assert "".join(delta.text for delta in deltas if delta.kind == "text") == "answer"
        assert "".join(delta.text for delta in deltas if delta.kind == "reasoning_text") == "public analysis"
    else:
        assert json.loads(data)["output"][0]["type"] == "reasoning"


@pytest.mark.parametrize("mutation", ["snapshot", "identity", "opaque", "early_done"])
def test_reasoning_stream_detects_changed_snapshots_and_invalid_lifecycle(mutation):
    events = list(SSEParser().feed(wire("responses")))
    for index, event in enumerate(events):
        body = json.loads(event.data)
        if mutation == "snapshot" and body["type"] == "response.reasoning_text.done":
            body["text"] = "changed"
        elif mutation == "identity" and body["type"] == "response.reasoning_text.delta":
            body["item_id"] = "wrong"
        elif mutation == "opaque" and body["type"] == "response.output_item.added":
            body["item"]["encrypted_content"] = "opaque"
        elif mutation == "early_done" and body["type"] == "response.content_part.added":
            events.pop(index)
            break
        else:
            continue
        events[index] = type(event)(event.event, json.dumps(body))
        break
    decoder = StreamDecoder("responses", public_reasoning=True)
    with pytest.raises(StreamError):
        for event in events:
            decoder.feed(event)


@pytest.mark.parametrize("value", ["true", 1, None])
def test_public_reasoning_requires_boolean_opt_in(value):
    with pytest.raises(TypeError):
        GatewayConfig("chat", "https://example.test/v1", "key", "token", public_reasoning=value)


def test_incomplete_public_reasoning_is_not_replayed_as_complete_history():
    item = reasoning_item("partial", status="incomplete")
    with pytest.raises(ConversionError, match="incomplete"):
        convert_request({"model": "m", "input": [item]}, "responses", "chat", public_reasoning=True)


def test_codex_null_encrypted_placeholder_is_not_opaque_state():
    item = {**reasoning_item("public"), "encrypted_content": None}
    result = convert_request({"model": "m", "input": [item]}, "responses", "chat", public_reasoning=True)
    assert result["messages"][0]["reasoning_content"] == "public"
