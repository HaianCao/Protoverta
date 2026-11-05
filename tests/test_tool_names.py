import copy
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor
from itertools import permutations

import pytest
from test_gateway_capabilities import listener, post

from protoverta import ConversionError, convert_request, convert_response
from protoverta.server import GatewayConfig
from protoverta.sse import SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.stream_pipe import StreamTranscoder
from protoverta.streaming import Delta, StreamError
from protoverta.tool_names import ToolNameMap


def request_body(name, protocol="messages"):
    common = {"model": "m", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 32}
    if protocol == "messages":
        return dict(common, tools=[{"name": name, "input_schema": {"type": "object"}}], tool_choice={"type": "tool", "name": name})
    if protocol == "chat":
        return dict(
            common,
            tools=[{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}],
            tool_choice={"type": "function", "function": {"name": name}},
        )
    return {
        "model": "m",
        "input": "Hi",
        "max_output_tokens": 32,
        "tools": [{"type": "function", "name": name, "parameters": {"type": "object"}}],
        "tool_choice": {"type": "function", "name": name},
    }


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("name", ["mcp__" + "x" * 97, "mcp.server/read file", "tool_\u5de5\u5177"])
def test_aliases_are_deterministic_reversible_and_request_local(protocol, name):
    original = request_body(name, protocol)
    before = copy.deepcopy(original)
    mapper = ToolNameMap()
    mapped = mapper.prepare(original, protocol)
    alias = next(iter(mapper.aliases))
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", alias)
    assert mapper.decode(alias) == name
    assert ToolNameMap().prepare(original, protocol) == mapped
    assert original == before


def test_history_forced_choice_and_collision_are_preserved_without_string_replacement():
    name = "x" * 102
    original = request_body(name, "responses")
    original["input"] = [
        {"type": "function_call", "name": name, "call_id": "c", "arguments": json.dumps({"name": name})},
        {"type": "function_call_output", "call_id": "c", "output": name},
    ]
    first = ToolNameMap()
    alias = first.prepare(original, "responses")["tools"][0]["name"]
    original["tools"].append({"type": "function", "name": alias})
    mapper = ToolNameMap()
    mapped = mapper.prepare(original, "responses")
    second_alias = mapped["tools"][0]["name"]
    assert second_alias != alias
    assert mapped["tools"][1]["name"] == alias
    assert mapped["input"][0]["name"] == mapped["tool_choice"]["name"] == second_alias
    assert json.loads(mapped["input"][0]["arguments"])["name"] == name
    assert mapped["input"][1]["output"] == name
    restored = mapper.restore({"output": [{"type": "function_call", "name": second_alias, "arguments": json.dumps({"value": second_alias})}]}, "responses")
    assert restored["output"][0]["name"] == name
    assert json.loads(restored["output"][0]["arguments"])["value"] == second_alias
    with pytest.raises(ConversionError, match="unknown function alias"):
        mapper.decode("pv_tool_unknown")


def test_strict_conversion_rejects_long_names_before_upstream():
    with pytest.raises(ConversionError, match="tool_names='alias'"):
        convert_request(request_body("x" * 102), "messages", "chat")


@pytest.mark.parametrize("target", ["chat", "responses"])
def test_strict_json_and_stream_responses_reject_nonportable_upstream_names(target):
    body = {
        "model": "m",
        "content": [{"type": "tool_use", "id": "c", "name": "x" * 102, "input": {}}],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    with pytest.raises(ConversionError, match="function names"):
        convert_response(body, "messages", target)
    encoder = StreamEncoder("messages")
    deltas = [Delta("start", model="m", input_tokens=1, output_tokens=0), Delta("tool_start", index=0, call_id="c", name="x" * 102)]
    wire = b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta))
    with pytest.raises(StreamError, match="not portable"):
        StreamTranscoder("messages", target).feed(wire)


@pytest.mark.parametrize("profile,expected", [("strict", "reject"), ("codex", "alias"), ("claude", "alias")])
def test_explicit_client_profiles_enable_aliases_but_reject_override_wins(profile, expected):
    assert GatewayConfig("chat", "https://example.test/v1", "key", "local", client_profile=profile).tool_names == expected
    assert GatewayConfig("chat", "https://example.test/v1", "key", "local", client_profile=profile, tool_names="reject").tool_names == "reject"


@pytest.mark.parametrize("stream", [False, True])
def test_gateway_alias_json_stream_and_concurrent_isolation(stream):
    def upstream(request, *, timeout):
        outbound = json.loads(request.data)
        name = outbound["tools"][0]["function"]["name"]
        assert outbound["tool_choice"]["function"]["name"] == name
        if stream:
            encoder = StreamEncoder("chat")
            deltas = [
                Delta("start", model="m"),
                Delta("tool_start", index=0, call_id="c", name=name),
                Delta("tool_delta", index=0, text="{}"),
                Delta("tool_end", index=0),
                Delta("finish", reason="tool_calls", input_tokens=2, output_tokens=3),
            ]
            return io.BytesIO(b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta)))
        return io.BytesIO(
            json.dumps(
                {
                    "model": "m",
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [{"id": "c", "type": "function", "function": {"name": name, "arguments": "{}"}}],
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3},
                }
            ).encode()
        )

    config = GatewayConfig("chat", "https://example.test/v1", "key", "local", tool_names="alias", messages_usage_policy="deferred")
    with listener(config, upstream) as url:

        def run(name):
            body = dict(request_body(name), stream=stream)
            with post(url.removesuffix("responses") + "messages", body) as response:
                wire = response.read()
            if stream:
                events = [json.loads(event.data) for event in SSEParser().feed(wire)]
                assert events[-1]["type"] == "message_stop"
                assert (
                    next(
                        event["content_block"]["name"]
                        for event in events
                        if event["type"] == "content_block_start" and event["content_block"]["type"] == "tool_use"
                    )
                    == name
                )
            else:
                assert json.loads(wire)["content"][0]["name"] == name

        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(run, ["mcp_" + "a" * 100, "mcp_" + "b" * 100, "mcp_" + "a" * 100]))


@pytest.mark.parametrize("source,target", tuple(permutations(("chat", "messages", "responses"), 2)))
@pytest.mark.parametrize("stream", [False, True])
def test_alias_gateway_all_six_routes_restore_names_and_snapshots(source, target, stream):
    name = "mcp_" + "x" * 100
    body = dict(request_body(name, source), stream=stream)

    def upstream(request, *, timeout):
        outbound = json.loads(request.data)
        alias = outbound["tools"][0]["function"]["name"] if target == "chat" else outbound["tools"][0]["name"]
        if stream:
            encoder = StreamEncoder(target)
            deltas = [
                Delta("start", model="m", input_tokens=1, output_tokens=0),
                Delta("tool_start", index=0, call_id="c", name=alias),
                Delta("tool_delta", index=0, text="{}"),
                Delta("tool_end", index=0),
                Delta("finish", reason="tool_calls", input_tokens=1, output_tokens=1),
            ]
            return io.BytesIO(b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta)))
        chat = {
            "model": "m",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{"type": "function", "id": "c", "function": {"name": alias, "arguments": "{}"}}],
                    },
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        return io.BytesIO(json.dumps(convert_response(chat, "chat", target)).encode())

    config = GatewayConfig(target, "https://example.test/v1", "key", "local", tool_names="alias", messages_usage_policy="deferred")
    with listener(config, upstream) as origin:
        endpoint = {"chat": "chat/completions", "messages": "messages", "responses": "responses"}[source]
        with post(origin.removesuffix("responses") + endpoint, body) as response:
            wire = response.read()
    if stream:
        events = [json.loads(event.data) for event in SSEParser().feed(wire) if event.data != "[DONE]"]
        if source == "responses":
            calls = [event["item"] for event in events if isinstance(event.get("item"), dict) and event["item"]["type"] == "function_call"]
            calls += [item for item in events[-1]["response"]["output"] if item["type"] == "function_call"]
        elif source == "messages":
            calls = [event["content_block"] for event in events if event.get("content_block", {}).get("type") == "tool_use"]
        else:
            calls = [
                call["function"]
                for event in events
                for choice in event.get("choices", [])
                for call in choice.get("delta", {}).get("tool_calls", [])
                if "name" in call.get("function", {})
            ]
    else:
        result = json.loads(wire)
        calls = (
            result["output"]
            if source == "responses"
            else result["content"]
            if source == "messages"
            else [call["function"] for call in result["choices"][0]["message"]["tool_calls"]]
        )
    assert calls and all(call["name"] == name for call in calls)


def test_alias_map_does_not_reuse_empty_request_and_keeps_native_gateway_unchanged():
    mapper = ToolNameMap()
    mapper.prepare({"model": "m", "input": "Hi"}, "responses")
    with pytest.raises(ConversionError, match="reused"):
        mapper.prepare(request_body("x" * 100), "messages")
    body = request_body("x" * 100, "chat")
    captured = []

    def upstream(request, *, timeout):
        captured.append(json.loads(request.data))
        return io.BytesIO(b'{"native":true}')

    config = GatewayConfig("chat", "https://example.test/v1", "key", "local", tool_names="alias")
    with listener(config, upstream) as origin, post(origin.removesuffix("responses") + "chat/completions", body) as response:
        assert json.load(response) == {"native": True}
    assert captured == [body]
