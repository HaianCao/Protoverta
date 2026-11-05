import copy
import json

import pytest

from protoverta.capabilities import request_features
from protoverta.core import ConversionError, Protocol, convert_request
from protoverta.namespaces import NamespaceMap
from protoverta.sse import SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.stream_pipe import StreamTranscoder
from protoverta.streaming import Delta, StreamError
from protoverta.tool_names import ToolNameMap
from protoverta.tool_search import ClientToolSearchMap


def test_combined_tool_search_namespace_flatten_and_tool_name_aliasing():
    # Responses request with client tool search, a namespace containing a deferred child and an immediate child,
    # plus a long non-portable function name requiring ToolNameMap aliasing.
    long_name = "mcp__workspace__" + "a" * 80
    body = {
        "model": "m",
        "max_output_tokens": 64,
        "tools": [
            {
                "type": "tool_search",
                "execution": "client",
                "description": "Search available tools",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            },
            {"type": "function", "name": long_name, "parameters": {"type": "object", "properties": {}}},
            {
                "type": "namespace",
                "name": "multi_agent_v1",
                "description": "Agent tools",
                "tools": [
                    {
                        "type": "function",
                        "name": "spawn_agent",
                        "description": "Spawn child",
                        "parameters": {"type": "object", "properties": {"message": {"type": "string"}}},
                    },
                    {
                        "type": "function",
                        "name": "deferred_helper",
                        "description": "Deferred child",
                        "defer_loading": True,
                        "parameters": {"type": "object", "properties": {"id": {"type": "string"}}},
                    },
                ],
            },
        ],
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "start"}]}],
    }

    # Turn 1: deferred_helper is NOT loaded yet; only spawn_agent, long_name (aliased), and pv_client_tool_search appear.
    search_map_1 = ClientToolSearchMap()
    ns_map_1 = NamespaceMap()
    name_map_1 = ToolNameMap()
    p1 = name_map_1.prepare(ns_map_1.prepare(search_map_1.prepare(body)), Protocol.RESPONSES)
    chat_1 = convert_request(p1, Protocol.RESPONSES, Protocol.CHAT)
    names_1 = [t["function"]["name"] for t in chat_1["tools"]]
    assert len(names_1) == 3
    assert "pv_client_tool_search" in names_1
    assert ns_map_1.alias("multi_agent_v1", "spawn_agent") in names_1
    assert any(n.startswith("pv_tool_") for n in names_1)

    # Turn 2: History loads deferred_helper via tool_search_output; partial namespace merges cleanly.
    body_turn2 = copy.deepcopy(body)
    body_turn2["input"].extend(
        [
            {
                "type": "tool_search_call",
                "execution": "client",
                "status": "completed",
                "call_id": "s1",
                "arguments": {"query": "deferred_helper"},
            },
            {
                "type": "tool_search_output",
                "execution": "client",
                "status": "completed",
                "call_id": "s1",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "multi_agent_v1",
                        "description": "Agent tools",
                        "tools": [
                            {
                                "type": "function",
                                "name": "deferred_helper",
                                "description": "Deferred child",
                                "defer_loading": True,
                                "parameters": {"type": "object", "properties": {"id": {"type": "string"}}},
                            }
                        ],
                    }
                ],
            },
        ]
    )
    search_map_2 = ClientToolSearchMap()
    ns_map_2 = NamespaceMap()
    name_map_2 = ToolNameMap()
    p2 = name_map_2.prepare(ns_map_2.prepare(search_map_2.prepare(body_turn2)), Protocol.RESPONSES)
    chat_2 = convert_request(p2, Protocol.RESPONSES, Protocol.CHAT)
    names_2 = [t["function"]["name"] for t in chat_2["tools"]]
    assert len(names_2) == 4
    assert ns_map_2.alias("multi_agent_v1", "deferred_helper") in names_2


def test_mid_turn_schema_conflict_and_incomplete_search_output_rejected():
    base = {
        "model": "m",
        "max_output_tokens": 64,
        "tools": [
            {
                "type": "tool_search",
                "execution": "client",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
            {
                "type": "namespace",
                "name": "ns",
                "tools": [
                    {
                        "type": "function",
                        "name": "fn",
                        "parameters": {"type": "object", "properties": {"a": {"type": "string"}}},
                    }
                ],
            },
        ],
    }

    # 1. Mid-turn schema change in tool_search_output for an already declared namespace child rejects
    conflict = copy.deepcopy(base)
    conflict["input"] = [
        {"type": "tool_search_call", "execution": "client", "status": "completed", "call_id": "s1", "arguments": {"query": "fn"}},
        {
            "type": "tool_search_output",
            "execution": "client",
            "status": "completed",
            "call_id": "s1",
            "tools": [
                {
                    "type": "namespace",
                    "name": "ns",
                    "tools": [
                        {
                            "type": "function",
                            "name": "fn",
                            "parameters": {"type": "object", "properties": {"a": {"type": "integer"}}},
                        }
                    ],
                }
            ],
        },
    ]
    with pytest.raises(ConversionError, match="conflicting loaded tool definitions"):
        ClientToolSearchMap().prepare(conflict)

    # 2. Incomplete tool_search_output status rejects
    incomplete = copy.deepcopy(base)
    incomplete["input"] = [
        {"type": "tool_search_call", "execution": "client", "status": "completed", "call_id": "s1", "arguments": {"query": "fn"}},
        {"type": "tool_search_output", "execution": "client", "status": "incomplete", "call_id": "s1", "tools": []},
    ]
    with pytest.raises(ConversionError, match="only completed explicit client tool search is supported"):
        ClientToolSearchMap().prepare(incomplete)


def test_claude_tool_reference_is_rejected_until_mapping_contract_exists():
    # Claude tool_reference blocks in tool_result or messages have no cross-protocol contract;
    # ENABLE_TOOL_SEARCH=false remains enforced in claudapter, and raw tool_reference blocks fail closed.
    msg_with_tool_ref = {
        "model": "m",
        "max_tokens": 64,
        "messages": [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "search", "input": {}}]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "c1",
                        "content": [{"type": "tool_reference", "tool_name": "mcp__server__tool"}],
                    }
                ],
            },
        ],
    }
    for target in (Protocol.CHAT, Protocol.RESPONSES):
        with pytest.raises(ConversionError, match="unsupported Messages block: tool_reference"):
            convert_request(msg_with_tool_ref, Protocol.MESSAGES, target)


def test_client_vs_hosted_tools_distinction_in_capabilities_and_converter():
    client_search_body = {
        "model": "m",
        "input": "hi",
        "tools": [
            {"type": "tool_search", "execution": "client", "parameters": {"type": "object"}},
            {"type": "function", "name": "f1", "parameters": {"type": "object"}},
            {"type": "custom", "name": "c1", "format": {"type": "text"}},
        ],
    }
    # Client-executed tool_search, function, and custom tools are client tools, NOT hosted_tools
    assert "hosted_tools" not in request_features(client_search_body, Protocol.RESPONSES)
    assert {"tools", "custom_tools"} <= request_features(client_search_body, Protocol.RESPONSES)

    # Server-executed tool_search or web_search_preview IS classified as hosted_tools
    server_search_body = {
        "model": "m",
        "input": "hi",
        "tools": [{"type": "tool_search", "execution": "server"}],
    }
    assert "hosted_tools" in request_features(server_search_body, Protocol.RESPONSES)

    web_search_body = {
        "model": "m",
        "input": "hi",
        "tools": [{"type": "web_search_preview"}],
    }
    assert "hosted_tools" in request_features(web_search_body, Protocol.RESPONSES)
    with pytest.raises(ConversionError, match="only function tools are supported"):
        convert_request(web_search_body, Protocol.RESPONSES, Protocol.CHAT)


def test_streamed_client_tool_search_holds_until_valid_json_object_and_rejects_truncated():
    search_map = ClientToolSearchMap()
    search_map.prepare(
        {
            "model": "m",
            "input": "hi",
            "tools": [{"type": "tool_search", "execution": "client", "parameters": {"type": "object"}}],
        }
    )
    chat_encoder = StreamEncoder(Protocol.CHAT)
    valid_deltas = [
        Delta("start", model="m", input_tokens=2, output_tokens=0),
        Delta("tool_start", index=0, call_id="s1", name="pv_client_tool_search"),
        Delta("tool_delta", index=0, text='{"query":'),
        Delta("tool_delta", index=0, text='"agent"}'),
        Delta("tool_end", index=0),
        Delta("finish", reason="tool_calls", input_tokens=2, output_tokens=4),
    ]
    wire = b"".join(encode_sse(ev.event, ev.data) for d in valid_deltas for ev in chat_encoder.feed(d))
    pipe = StreamTranscoder(Protocol.CHAT, Protocol.RESPONSES, delta_transform=search_map.stream_delta)
    out = pipe.feed(wire) + pipe.finish()
    events = [json.loads(ev.data) for ev in SSEParser().feed(out)]
    types = [ev["type"] for ev in events]
    assert "response.function_call_arguments.delta" not in types
    added = next(ev["item"] for ev in events if ev["type"] == "response.output_item.added" and ev["item"].get("type") == "tool_search_call")
    assert added == {
        "id": added["id"],
        "type": "tool_search_call",
        "status": "completed",
        "call_id": "s1",
        "execution": "client",
        "arguments": {"query": "agent"},
    }

    # Truncated/non-object JSON in streamed search call fails closed
    bad_encoder = StreamEncoder(Protocol.CHAT)
    bad_deltas = [
        Delta("start", model="m", input_tokens=2, output_tokens=0),
        Delta("tool_start", index=0, call_id="s1", name="pv_client_tool_search"),
        Delta("tool_delta", index=0, text='["not-an-object"]'),
    ]
    bad_wire = (
        b"".join(encode_sse(ev.event, ev.data) for d in bad_deltas for ev in bad_encoder.feed(d))
        + encode_sse(
            "message",
            json.dumps(
                {
                    "id": "1",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "m",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                }
            ),
        )
        + encode_sse("message", "[DONE]")
    )
    bad_pipe = StreamTranscoder(Protocol.CHAT, Protocol.RESPONSES, delta_transform=search_map.stream_delta)
    with pytest.raises(StreamError):
        bad_pipe.feed(bad_wire)
