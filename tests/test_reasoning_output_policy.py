"""Explicit reasoning_output='omit' policy and provider-reported stream errors.

Shapes mirror live provider streams observed on 2026-11-06 (OpenRouter
``reasoning``/``reasoning_details``, NVIDIA NIM ``reasoning_content`` and an
HTTP 200 stream whose first event is a provider error object).
"""

import json

import pytest

from protoverta import ConversionError, convert_response
from protoverta.automatic_reasoning import automatic_route_reasoning
from protoverta.capabilities import _policy_key
from protoverta.server import GatewayConfig
from protoverta.stream_pipe import StreamTranscoder
from protoverta.streaming import StreamDecoder, UnsupportedReasoningError, UpstreamReportedError

USAGE = {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14}
BASE = {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m"}


def chunk(delta, finish=None, **extra):
    return {**BASE, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}


OPENROUTER = [
    chunk({"role": "assistant", "content": "", "reasoning": "thinking", "reasoning_details": [{"type": "reasoning.text", "text": "thinking"}]}),
    chunk({"content": "OK", "reasoning": None}),
    chunk({}, "stop", usage=USAGE),
]
NIM = [chunk({"role": "assistant", "reasoning_content": "hmm"}), chunk({"content": "OK"}), chunk({}, "stop", usage=USAGE)]


def run(target, chunks, **options):
    transcoder = StreamTranscoder("chat", target, messages_usage_policy="deferred", **options)
    out = b""
    for item in chunks:
        out += transcoder.feed(f"data: {json.dumps(item)}\n\n".encode())
    out += transcoder.feed(b"data: [DONE]\n\n") + transcoder.finish()
    return transcoder, out.decode()


@pytest.mark.parametrize("chunks", [OPENROUTER, NIM])
def test_chat_reasoning_rejected_by_default_and_omitted_explicitly(chunks):
    with pytest.raises(UnsupportedReasoningError):
        run("messages", chunks)
    transcoder, out = run("messages", chunks, omit_reasoning=True)
    assert '"OK"' in out and "thinking" not in out and "hmm" not in out
    assert transcoder.decoder.omitted_reasoning


def test_openrouter_reasoning_field_is_public_reasoning_and_details_are_omitted():
    with pytest.raises(UnsupportedReasoningError):
        run("responses", OPENROUTER, public_reasoning=True)
    _, out = run("responses", OPENROUTER, public_reasoning=True, omit_reasoning=True)
    assert "response.reasoning_text.delta" in out and "thinking" in out and "reasoning.text" not in out


def test_messages_thinking_blocks_are_omitted_only_by_policy():
    events = [
        ("message_start", {"type": "message_start", "message": {"model": "m", "content": [], "usage": {"input_tokens": 3, "output_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "secret plan"}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "OK"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    raw = "".join(f"event: {name}\ndata: {json.dumps(body)}\n\n" for name, body in events).encode()
    with pytest.raises(UnsupportedReasoningError):
        StreamTranscoder("messages", "chat").feed(raw)
    transcoder = StreamTranscoder("messages", "chat", omit_reasoning=True)
    out = (transcoder.feed(raw) + transcoder.finish()).decode()
    assert "OK" in out and "secret plan" not in out and "sig" not in out


def test_responses_reasoning_items_are_omitted_with_their_events():
    item = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"}
    done_item = {**item, "summary": [{"type": "summary_text", "text": "s"}]}
    message = {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "OK", "annotations": [], "logprobs": []}],
    }
    seq = iter(range(100))
    events = [
        {"type": "response.created", "response": {"model": "m"}},
        {"type": "response.output_item.added", "output_index": 0, "item": item},
        {
            "type": "response.reasoning_summary_part.added",
            "output_index": 0,
            "item_id": "rs_1",
            "summary_index": 0,
            "part": {"type": "summary_text", "text": ""},
        },
        {"type": "response.reasoning_summary_text.delta", "output_index": 0, "item_id": "rs_1", "summary_index": 0, "delta": "s"},
        {"type": "response.output_item.done", "output_index": 0, "item": {**done_item, "status": "completed"}},
        {"type": "response.output_item.added", "output_index": 1, "item": {**message, "status": "in_progress", "content": []}},
        {
            "type": "response.content_part.added",
            "output_index": 1,
            "item_id": "msg_1",
            "content_index": 0,
            "part": {"type": "output_text", "text": "", "annotations": [], "logprobs": []},
        },
        {"type": "response.output_text.delta", "output_index": 1, "item_id": "msg_1", "content_index": 0, "delta": "OK", "logprobs": []},
        {"type": "response.output_text.done", "output_index": 1, "item_id": "msg_1", "content_index": 0, "text": "OK", "logprobs": []},
        {"type": "response.content_part.done", "output_index": 1, "item_id": "msg_1", "content_index": 0, "part": message["content"][0]},
        {"type": "response.output_item.done", "output_index": 1, "item": message},
        {
            "type": "response.completed",
            "response": {
                "model": "m",
                "status": "completed",
                "output": [{**done_item, "status": "completed"}, message],
                "usage": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
            },
        },
    ]
    raw = "".join(f"event: {e['type']}\ndata: {json.dumps({**e, 'sequence_number': next(seq)})}\n\n" for e in events).encode()
    with pytest.raises(UnsupportedReasoningError):
        StreamTranscoder("responses", "chat").feed(raw)
    transcoder = StreamTranscoder("responses", "chat", omit_reasoning=True)
    out = (transcoder.feed(raw) + transcoder.finish()).decode()
    assert '"OK"' in out and "opaque" not in out and transcoder.decoder.omitted_reasoning


@pytest.mark.parametrize(
    "body,source",
    [
        (
            {
                "id": "x",
                "object": "chat.completion",
                "created": 1,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "OK", "reasoning": "r", "reasoning_details": [{"type": "reasoning.text"}]},
                    }
                ],
                "usage": USAGE,
            },
            "chat",
        ),
        (
            {
                "id": "x",
                "type": "message",
                "role": "assistant",
                "model": "m",
                "stop_reason": "end_turn",
                "content": [{"type": "thinking", "thinking": "t", "signature": "s"}, {"type": "text", "text": "OK"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
            "messages",
        ),
    ],
)
def test_json_reasoning_omitted_only_by_policy(body, source):
    target = "responses" if source == "messages" else "messages"
    with pytest.raises(ConversionError, match="reasoning|thinking"):
        convert_response(body, source, target)
    converted = json.dumps(convert_response(body, source, target, omit_reasoning=True))
    assert "OK" in converted and '"r"' not in converted and "signature" not in converted


def test_provider_error_event_inside_http_200_stream_is_surfaced():
    decoder = StreamDecoder("chat")
    from protoverta.sse import SSEEvent

    with pytest.raises(UpstreamReportedError, match="Invalid request") as caught:
        decoder.feed(SSEEvent("message", json.dumps({"error": {"message": "Invalid request", "type": "invalid_request_error", "code": 400}})))
    assert caught.value.status == 400 and caught.value.kind == "invalid_request_error"


def test_automatic_route_policy_and_route_evidence_compatibility():
    assert automatic_route_reasoning("messages", "messages") == ("provider_default", False, "reject")
    assert automatic_route_reasoning("messages", "chat") == ("provider_default", False, "omit")
    assert automatic_route_reasoning("responses", "chat") == ("provider_default", True, "omit")
    assert automatic_route_reasoning("responses", "messages") == ("provider_default", False, "omit")
    legacy = {
        "messages_usage_policy": "deferred",
        "upstream_reasoning": "provider_default",
        "namespace_tools": "reject",
        "tool_names": "alias",
        "client_tool_search": "reject",
        "public_reasoning": False,
    }
    assert _policy_key(legacy) == _policy_key({**legacy, "reasoning_output": "reject"}) != _policy_key({**legacy, "reasoning_output": "omit"})
    with pytest.raises(ValueError):
        GatewayConfig("chat", "https://p.example/v1", "k", "t", reasoning_output="drop")
