import base64
import io
import json
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post

from protoverta.core import ConversionError, Protocol, convert_request, convert_response
from protoverta.profiles import prepare_claude_request, prepare_codex_request
from protoverta.server import GatewayConfig
from protoverta.sse import encode_sse
from protoverta.stream_pipe import StreamTranscoder
from protoverta.streaming import StreamError

PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR").decode("ascii")
PDF_B64 = base64.b64encode(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n").decode("ascii")

SIX_DIRECTIONS = [(a, b) for a in Protocol for b in Protocol if a != b]


@pytest.mark.parametrize("source,target", SIX_DIRECTIONS)
def test_text_tool_results_preserved_across_all_six_directions(source, target):
    requests = {
        Protocol.CHAT: {
            "model": "m",
            "max_tokens": 64,
            "messages": [
                {"role": "user", "content": "run"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "fn", "arguments": "{}"}}],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "plain-result"},
            ],
        },
        Protocol.MESSAGES: {
            "model": "m",
            "max_tokens": 64,
            "messages": [
                {"role": "user", "content": "run"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "fn", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "plain-result"}]},
            ],
        },
        Protocol.RESPONSES: {
            "model": "m",
            "max_output_tokens": 64,
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "run"}]},
                {"type": "function_call", "call_id": "c1", "name": "fn", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "c1", "output": "plain-result"},
            ],
        },
    }
    converted = convert_request(requests[source], source, target)
    assert "plain-result" in json.dumps(converted)


@pytest.mark.parametrize("source,target", SIX_DIRECTIONS)
def test_structured_text_part_arrays_in_tool_results_across_all_six_directions(source, target):
    requests = {
        Protocol.CHAT: {
            "model": "m",
            "max_tokens": 64,
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "fn", "arguments": "{}"}}],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "content": [{"type": "text", "text": "part-A:"}, {"type": "text", "text": "part-B"}],
                },
            ],
        },
        Protocol.MESSAGES: {
            "model": "m",
            "max_tokens": 64,
            "messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "fn", "input": {}}]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "c1",
                            "content": [{"type": "text", "text": "part-A:"}, {"type": "text", "text": "part-B"}],
                        }
                    ],
                },
            ],
        },
        Protocol.RESPONSES: {
            "model": "m",
            "max_output_tokens": 64,
            "input": [
                {"type": "function_call", "call_id": "c1", "name": "fn", "arguments": "{}"},
                {
                    "type": "function_call_output",
                    "call_id": "c1",
                    "output": [{"type": "input_text", "text": "part-A:"}, {"type": "input_text", "text": "part-B"}],
                },
            ],
        },
    }
    converted = convert_request(requests[source], source, target)
    if target == Protocol.CHAT:
        assert converted["messages"][1] == {"role": "tool", "tool_call_id": "c1", "content": "part-A:part-B"}
    elif target == Protocol.MESSAGES:
        assert converted["messages"][1]["content"][0]["content"] == [
            {"type": "text", "text": "part-A:"},
            {"type": "text", "text": "part-B"},
        ]
    else:
        assert converted["input"][1]["output"] == [
            {"type": "input_text", "text": "part-A:"},
            {"type": "input_text", "text": "part-B"},
        ]


def test_error_tool_results_strict_vs_claude_policy():
    strict_msg = {
        "model": "m",
        "max_tokens": 64,
        "messages": [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "fn", "input": {}}]},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "boom", "is_error": True}],
            },
        ],
    }
    for target in (Protocol.CHAT, Protocol.RESPONSES):
        with pytest.raises(ConversionError, match="tool_result.is_error cannot be preserved"):
            convert_request(strict_msg, Protocol.MESSAGES, target)
        prepared = prepare_claude_request(strict_msg)
        converted = convert_request(prepared, Protocol.MESSAGES, target)
        assert r"[Tool execution error]\nboom" in json.dumps(converted)

    # is_error=False is explicitly permitted in strict mode
    non_err_msg = {
        "model": "m",
        "max_tokens": 64,
        "messages": [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "fn", "input": {}}]},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "ok", "is_error": False}],
            },
        ],
    }
    assert convert_request(non_err_msg, Protocol.MESSAGES, Protocol.CHAT)["messages"][1]["content"] == "ok"


def test_image_tool_results_preserved_messages_responses_and_rejected_on_chat():
    msg_with_img = {
        "model": "m",
        "max_tokens": 64,
        "messages": [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "shot", "input": {}}]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "c1",
                        "content": [
                            {"type": "text", "text": "captured"},
                            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_B64}},
                        ],
                    }
                ],
            },
        ],
    }
    to_resp = convert_request(msg_with_img, Protocol.MESSAGES, Protocol.RESPONSES)
    out_parts = to_resp["input"][1]["output"]
    assert out_parts[0] == {"type": "input_text", "text": "captured"}
    assert out_parts[1]["type"] == "input_image"

    back_to_msg = convert_request(to_resp, Protocol.RESPONSES, Protocol.MESSAGES)
    assert back_to_msg["messages"][1]["content"][0]["content"][1]["type"] == "image"

    with pytest.raises(ConversionError, match="Chat cannot preserve image-bearing tool results"):
        convert_request(msg_with_img, Protocol.MESSAGES, Protocol.CHAT)
    with pytest.raises(ConversionError, match="Chat cannot preserve image-bearing tool results"):
        convert_request(to_resp, Protocol.RESPONSES, Protocol.CHAT)

    chat_with_img_tool = {
        "model": "m",
        "max_tokens": 64,
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "shot", "arguments": "{}"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_B64}"}}],
            },
        ],
    }
    for target in (Protocol.MESSAGES, Protocol.RESPONSES):
        with pytest.raises(ConversionError, match="Chat tool results support text content only"):
            convert_request(chat_with_img_tool, Protocol.CHAT, target)


@pytest.mark.parametrize("source,target", SIX_DIRECTIONS)
def test_pdf_and_resource_link_tool_results_rejected_across_all_six_directions(source, target):
    pdf_requests = {
        Protocol.CHAT: {
            "model": "m",
            "max_tokens": 64,
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "doc", "arguments": "{}"}}],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "content": [{"type": "file", "file": {"file_data": f"data:application/pdf;base64,{PDF_B64}"}}],
                },
            ],
        },
        Protocol.MESSAGES: {
            "model": "m",
            "max_tokens": 64,
            "messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "doc", "input": {}}]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "c1",
                            "content": [
                                {
                                    "type": "document",
                                    "source": {"type": "base64", "media_type": "application/pdf", "data": PDF_B64},
                                }
                            ],
                        }
                    ],
                },
            ],
        },
        Protocol.RESPONSES: {
            "model": "m",
            "max_output_tokens": 64,
            "input": [
                {"type": "function_call", "call_id": "c1", "name": "doc", "arguments": "{}"},
                {
                    "type": "function_call_output",
                    "call_id": "c1",
                    "output": [{"type": "input_file", "file_data": f"data:application/pdf;base64,{PDF_B64}"}],
                },
            ],
        },
    }
    with pytest.raises(ConversionError, match="(PDF tool results|Chat tool results support text content only)"):
        convert_request(pdf_requests[source], source, target)

    resource_link_requests = {
        Protocol.CHAT: {
            "model": "m",
            "max_tokens": 64,
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "mcp", "arguments": "{}"}}],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "content": [{"type": "resource_link", "uri": "file:///workspace/a.txt"}],
                },
            ],
        },
        Protocol.MESSAGES: {
            "model": "m",
            "max_tokens": 64,
            "messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "mcp", "input": {}}]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "c1",
                            "content": [{"type": "resource_link", "uri": "file:///workspace/a.txt"}],
                        }
                    ],
                },
            ],
        },
        Protocol.RESPONSES: {
            "model": "m",
            "max_output_tokens": 64,
            "input": [
                {"type": "function_call", "call_id": "c1", "name": "mcp", "arguments": "{}"},
                {
                    "type": "function_call_output",
                    "call_id": "c1",
                    "output": [{"type": "resource_link", "uri": "file:///workspace/a.txt"}],
                },
            ],
        },
    }
    with pytest.raises(ConversionError):
        convert_request(resource_link_requests[source], source, target)


@pytest.mark.parametrize("source,target", SIX_DIRECTIONS)
def test_mixed_user_text_multiple_and_reordered_parallel_tool_results(source, target):
    # Assistant emits parallel calls c1 and c2; user returns results in reordered sequence [c2, c1] plus user text.
    requests = {
        Protocol.CHAT: {
            "model": "m",
            "max_tokens": 64,
            "messages": [
                {
                    "role": "assistant",
                    "content": "calling both",
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "f1", "arguments": '{"x":1}'}},
                        {"id": "c2", "type": "function", "function": {"name": "f2", "arguments": '{"x":2}'}},
                    ],
                },
                {"role": "tool", "tool_call_id": "c2", "content": "res2"},
                {"role": "tool", "tool_call_id": "c1", "content": "res1"},
                {"role": "user", "content": "follow-up question"},
            ],
        },
        Protocol.MESSAGES: {
            "model": "m",
            "max_tokens": 64,
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "calling both"},
                        {"type": "tool_use", "id": "c1", "name": "f1", "input": {"x": 1}},
                        {"type": "tool_use", "id": "c2", "name": "f2", "input": {"x": 2}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "c2", "content": "res2"},
                        {"type": "text", "text": "interleaved user note"},
                        {"type": "tool_result", "tool_use_id": "c1", "content": "res1"},
                        {"type": "text", "text": "follow-up question"},
                    ],
                },
            ],
        },
        Protocol.RESPONSES: {
            "model": "m",
            "max_output_tokens": 64,
            "input": [
                {
                    "type": "message",
                    "id": "msg_1",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "calling both"}],
                },
                {"type": "function_call", "call_id": "c1", "name": "f1", "arguments": '{"x":1}'},
                {"type": "function_call", "call_id": "c2", "name": "f2", "arguments": '{"x":2}'},
                {"type": "function_call_output", "call_id": "c2", "output": "res2"},
                {"role": "user", "content": [{"type": "input_text", "text": "interleaved user note"}]},
                {"type": "function_call_output", "call_id": "c1", "output": "res1"},
                {"role": "user", "content": [{"type": "input_text", "text": "follow-up question"}]},
            ],
        },
    }
    converted = convert_request(requests[source], source, target)
    serialized = json.dumps(converted)
    assert serialized.index("res2") < serialized.index("res1") < serialized.index("follow-up question")


def test_text_after_tool_call_consistency_across_request_response_and_sse():
    # 1. Request history: Messages allows text after tool_use, but Chat/Responses reject non-empty text after tool_call
    msg_req = {
        "model": "m",
        "max_tokens": 64,
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "c1", "name": "f1", "input": {}},
                    {"type": "text", "text": "post-tool text"},
                ],
            },
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "ok"}]},
        ],
    }
    for target in (Protocol.CHAT, Protocol.RESPONSES):
        with pytest.raises(ConversionError, match="text after a tool call cannot be preserved"):
            convert_request(msg_req, Protocol.MESSAGES, target)

    # Codex blank placeholder after function_call is stripped only under prepare_codex_request targeting Chat
    codex_placeholder_req = {
        "model": "m",
        "max_output_tokens": 64,
        "input": [
            {"type": "function_call", "call_id": "c1", "name": "f1", "arguments": "{}"},
            {"type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "   "}]},
            {"type": "function_call_output", "call_id": "c1", "output": "ok"},
        ],
    }
    with pytest.raises(ConversionError, match="text after a tool call"):
        convert_request(codex_placeholder_req, Protocol.RESPONSES, Protocol.CHAT)
    prepared_codex = prepare_codex_request(codex_placeholder_req, Protocol.CHAT)
    assert convert_request(prepared_codex, Protocol.RESPONSES, Protocol.CHAT)["messages"][1]["content"] == "ok"

    # 2. Completed JSON response: Messages response with text after tool_use rejects when targeting Chat/Responses
    msg_resp = {
        "model": "m",
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": "c1", "name": "f1", "input": {}},
            {"type": "text", "text": "post-tool text"},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 5, "output_tokens": 5},
    }
    for target in (Protocol.CHAT, Protocol.RESPONSES):
        with pytest.raises(ConversionError, match="text after a tool call cannot be preserved"):
            convert_response(msg_resp, Protocol.MESSAGES, target)

    # 3. SSE stream: Messages stream with text block after tool_use block rejects when targeting Chat/Responses
    sse_chunks = (
        encode_sse(
            "message_start",
            json.dumps(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "model": "m",
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 5, "output_tokens": 0},
                    },
                }
            ),
        )
        + encode_sse(
            "content_block_start",
            json.dumps(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "tool_use", "id": "c1", "name": "f1", "input": {}},
                }
            ),
        )
        + encode_sse(
            "content_block_delta",
            json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{}"}}),
        )
        + encode_sse("content_block_stop", json.dumps({"type": "content_block_stop", "index": 0}))
        + encode_sse(
            "content_block_start",
            json.dumps({"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}),
        )
        + encode_sse(
            "content_block_delta",
            json.dumps({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "after"}}),
        )
    )
    for target in (Protocol.CHAT, Protocol.RESPONSES):
        transcoder = StreamTranscoder(Protocol.MESSAGES, target)
        with pytest.raises(StreamError, match="text after a tool call cannot be preserved"):
            transcoder.feed(sse_chunks)


def test_tool_result_and_argument_size_limits_enforced_without_calling_upstream():
    called = []

    def forbidden(req, *, timeout):
        called.append(1)
        return io.BytesIO(b"{}")

    config = GatewayConfig("chat", "https://example.test/v1", "key", "local", max_body_bytes=1024)
    oversized_body = {
        "model": "m",
        "input": [
            {"type": "function_call", "call_id": "c1", "name": "f1", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": "x" * 2048},
        ],
    }
    with listener(config, forbidden) as url, pytest.raises(HTTPError) as caught:
        post(url, oversized_body)
    assert caught.value.code == 413
    caught.value.close()
    assert not called
