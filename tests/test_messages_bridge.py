import json

import pytest

from protoverta.core import ConversionError, convert_response
from protoverta.profiles import prepare_claude_request
from protoverta.sse import SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.stream_pipe import StreamTranscoder
from protoverta.streaming import Delta, StreamDecoder, StreamError


def wire_for(source, deltas):
    encoder = StreamEncoder(source)
    return b"".join(
        encode_sse(e.event, e.data) for d in deltas for e in encoder.feed(d)
    )


def decode_messages(wire):
    parser, decoder = SSEParser(), StreamDecoder("messages")
    events = parser.feed(wire)
    result = [d for event in events for d in decoder.feed(event)]
    parser.finish(strict=True)
    decoder.finish()
    return result, events


@pytest.mark.parametrize("source", ["chat", "responses"])
@pytest.mark.parametrize("chunk_size", [1, 17, 100000])
def test_deferred_messages_streams_text_and_serializes_overlapping_tools(
    source, chunk_size
):
    prefix = wire_for(source, [Delta("start", model="m"), Delta("text", text="hello")])
    # A separate encoder is used for the complete stream; the prefix above only
    # proves that conversion emits text before upstream usage exists.
    probe = StreamTranscoder(source, "messages", messages_usage_policy="deferred")
    assert b"hello" in probe.feed(prefix)
    with pytest.raises(StreamError, match="terminal"):
        probe.finish()
    deltas = [Delta("start", model="m"), Delta("text", text="hello")]
    deltas += [
        Delta("tool_start", index=i, call_id=f"call_{i}", name="lookup")
        for i in range(2)
    ]
    deltas += [
        Delta("tool_delta", index=1, text='{"b":2}'),
        Delta("tool_delta", index=0, text='{"a":1}'),
    ]
    deltas += [Delta("tool_end", index=1), Delta("tool_end", index=0)]
    deltas += [
        Delta(
            "finish",
            reason="tool_calls",
            input_tokens=130,
            output_tokens=7,
            cached_input_tokens=100,
        )
    ]
    wire = wire_for(source, deltas)
    pipe = StreamTranscoder(source, "messages", messages_usage_policy="deferred")
    output = b"".join(
        pipe.feed(wire[i : i + chunk_size]) for i in range(0, len(wire), chunk_size)
    )
    assert b"message_stop" not in output
    decoded, events = decode_messages(output + pipe.finish())
    assert decoded[0].input_tokens == 0
    assert (
        decoded[-1].input_tokens,
        decoded[-1].cached_input_tokens,
        decoded[-1].output_tokens,
    ) == (130, 100, 7)
    assert [d.call_id for d in decoded if d.kind == "tool_start"] == [
        "call_0",
        "call_1",
    ]
    assert [d.text for d in decoded if d.kind == "tool_delta" and d.text] == [
        '{"a":1}',
        '{"b":2}',
    ]
    opened = None
    for event in events:
        body = json.loads(event.data)
        if event.event == "content_block_start":
            assert opened is None
            opened = body["index"]
        elif event.event == "content_block_stop":
            assert opened == body["index"]
            opened = None


def test_messages_deferred_requires_real_final_usage_and_bounds_queue():
    pipe = StreamTranscoder("chat", "messages", messages_usage_policy="deferred")
    wire = wire_for(
        "chat",
        [
            Delta("start", model="m"),
            Delta("text", text="partial"),
            Delta("finish", reason="stop"),
        ],
    )
    with pytest.raises(StreamError, match="real input_tokens"):
        pipe.feed(wire)
    pipe = StreamTranscoder(
        "chat", "messages", messages_usage_policy="deferred", max_pending_bytes=200
    )
    wire = wire_for(
        "chat",
        [
            Delta("start", model="m"),
            Delta("tool_start", index=0, call_id="a", name="a"),
            Delta("tool_start", index=1, call_id="b", name="b"),
            Delta("tool_delta", index=1, text="x" * 300),
        ],
    )
    with pytest.raises(StreamError, match="buffer exceeds"):
        pipe.feed(wire)


def test_messages_cache_usage_is_totalled_for_json_and_streaming():
    body = {
        "model": "m",
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 10,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 20,
            "output_tokens": 2,
        },
    }
    result = convert_response(body, "messages", "responses")
    assert result["usage"]["input_tokens"] == 130
    assert result["usage"]["total_tokens"] == 132
    assert result["usage"]["input_tokens_details"]["cached_tokens"] == 100
    pipe = StreamTranscoder("messages", "responses")
    wire = wire_for(
        "messages",
        [
            Delta(
                "start",
                model="m",
                input_tokens=130,
                output_tokens=0,
                cached_input_tokens=100,
                cache_creation_input_tokens=20,
            ),
            Delta("text", text="ok"),
            Delta(
                "finish",
                reason="stop",
                input_tokens=130,
                output_tokens=2,
                cached_input_tokens=100,
                cache_creation_input_tokens=20,
            ),
        ],
    )
    events = SSEParser().feed(pipe.feed(wire) + pipe.finish())
    assert json.loads(events[-1].data)["response"]["usage"] == result["usage"]


def test_truncated_text_json_roundtrip_and_measured_details():
    chat = {
        "model": "m",
        "choices": [
            {
                "message": {"role": "assistant", "content": "partial"},
                "finish_reason": "length",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 3},
            "completion_tokens_details": {"reasoning_tokens": 2},
        },
    }
    response = convert_response(chat, "chat", "responses")
    converted = convert_response(response, "responses", "chat")
    assert converted["choices"][0]["finish_reason"] == "length"
    assert converted["usage"] == {**chat["usage"], "total_tokens": 15}
    response["status"] = "completed"
    with pytest.raises(ConversionError, match="completed assistant"):
        convert_response(response, "responses", "chat")


def test_claude_profile_is_explicit_and_does_not_erase_thinking():
    body = {
        "model": "m",
        "metadata": {"user_id": "fixture"},
        "thinking": {"type": "disabled"},
        "system": [
            {"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "a",
                        "is_error": True,
                        "content": "failed",
                    }
                ],
            }
        ],
    }
    result = prepare_claude_request(body)
    assert "cache_control" not in result["system"][0]
    assert (
        result["messages"][0]["content"][0]["content"]
        == "[Tool execution error]\nfailed"
    )
    assert body["messages"][0]["content"][0]["is_error"] is True
    body["thinking"] = {"type": "adaptive"}
    with pytest.raises(ConversionError, match="thinking disabled"):
        prepare_claude_request(body)


@pytest.mark.parametrize(
    "source,target",
    [
        (a, b)
        for a in ("chat", "messages", "responses")
        for b in ("chat", "messages", "responses")
        if a != b
    ],
)
@pytest.mark.parametrize("reason", ["stop", "length"])
def test_all_six_streaming_edges_and_length_finish(source, target, reason):
    wire = wire_for(
        source,
        [
            Delta("start", model="m", input_tokens=3, output_tokens=0),
            Delta("text", text="partial"),
            Delta("finish", reason=reason, input_tokens=3, output_tokens=2),
        ],
    )
    pipe = StreamTranscoder(source, target, messages_usage_policy="deferred")
    result = pipe.feed(wire) + pipe.finish()
    parser, decoder = SSEParser(), StreamDecoder(target)
    deltas = [d for event in parser.feed(result) for d in decoder.feed(event)]
    decoder.finish()
    assert deltas[-1].reason == reason
    assert deltas[-1].input_tokens == 3


def test_deferred_messages_does_not_succeed_after_invalid_arguments_or_trailing_event():
    wire = wire_for(
        "chat",
        [
            Delta("start", model="m"),
            Delta("tool_start", index=0, call_id="a", name="lookup"),
            Delta("tool_delta", index=0, text="not json"),
            Delta("tool_end", index=0),
            Delta("finish", reason="tool_calls", input_tokens=3, output_tokens=2),
        ],
    )
    pipe = StreamTranscoder("chat", "messages", messages_usage_policy="deferred")
    with pytest.raises(StreamError, match="valid JSON"):
        pipe.feed(wire)
    wire = wire_for(
        "chat",
        [
            Delta("start", model="m"),
            Delta("text", text="ok"),
            Delta("finish", reason="stop", input_tokens=3, output_tokens=2),
        ],
    )
    pipe = StreamTranscoder("chat", "messages", messages_usage_policy="deferred")
    assert b"message_stop" not in pipe.feed(wire)
    with pytest.raises(StreamError, match="after stream completion"):
        pipe.feed(encode_sse("message", "[DONE]"))


def test_chat_hidden_reasoning_is_not_silently_discarded():
    pipe = StreamTranscoder("chat", "messages", messages_usage_policy="deferred")
    body = {"object": "chat.completion.chunk", "model": "m", "choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": "hidden"}, "finish_reason": None}]}
    with pytest.raises(StreamError, match="reasoning_content"):
        pipe.feed(encode_sse("message", json.dumps(body)))
