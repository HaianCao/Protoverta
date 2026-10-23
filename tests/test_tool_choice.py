import copy

import pytest

from protoverta import ConversionError, Protocol, convert_request
from protoverta.namespaces import NamespaceMap


def request(protocol):
    return {
        Protocol.CHAT: {"model": "m", "messages": [{"role": "user", "content": "Run add"}], "max_completion_tokens": 64, "tools": [{"type": "function", "function": {"name": "add", "parameters": {"type": "object"}}}], "tool_choice": {"type": "function", "function": {"name": "add"}}, "parallel_tool_calls": False},
        Protocol.MESSAGES: {"model": "m", "messages": [{"role": "user", "content": "Run add"}], "max_tokens": 64, "tools": [{"name": "add", "input_schema": {"type": "object"}}], "tool_choice": {"type": "tool", "name": "add", "disable_parallel_tool_use": True}},
        Protocol.RESPONSES: {"model": "m", "input": "Run add", "max_output_tokens": 64, "tools": [{"type": "function", "name": "add", "parameters": {"type": "object"}}], "tool_choice": {"type": "function", "name": "add"}, "parallel_tool_calls": False},
    }[protocol]


@pytest.mark.parametrize("source,target", [(a, b) for a in Protocol for b in Protocol if a != b])
def test_forced_function_all_six_routes_and_round_trip(source, target):
    body = request(source)
    original = copy.deepcopy(body)
    converted = convert_request(body, source, target)
    expected = request(target)["tool_choice"]
    assert converted["tool_choice"] == expected
    assert convert_request(converted, target, source)["tool_choice"] == body["tool_choice"]
    assert body == original


@pytest.mark.parametrize("choice", [{"type": "function", "name": "absent"}, {"type": "function", "name": "add", "extra": True}, {"type": "web_search"}, {"type": "function", "name": ""}])
def test_invalid_or_undeclared_forced_tools_fail(choice):
    body = request(Protocol.RESPONSES)
    body["tool_choice"] = choice
    with pytest.raises(ConversionError):
        convert_request(body, "responses", "chat")


def test_duplicate_definitions_and_required_without_tools_fail():
    body = request(Protocol.RESPONSES)
    body["tools"] *= 2
    with pytest.raises(ConversionError, match="duplicate"):
        convert_request(body, "responses", "chat")
    body.update(tools=[], tool_choice="required")
    with pytest.raises(ConversionError, match="requires function tools"):
        convert_request(body, "responses", "messages")


@pytest.mark.parametrize("target", ["chat", "messages"])
def test_forced_namespace_resolves_exact_definition(target):
    body = request(Protocol.RESPONSES)
    body["tools"] = [{"type": "namespace", "name": ns, "tools": [{"type": "function", "name": "add", "parameters": {"type": "object"}}]} for ns in ("one", "two")]
    body["tool_choice"] = {"type": "function", "namespace": "two", "name": "add"}
    mapper = NamespaceMap()
    prepared = mapper.prepare(body)
    converted = convert_request(prepared, "responses", target)
    selected = converted["tool_choice"].get("function", converted["tool_choice"])["name"]
    assert selected == prepared["tools"][1]["name"]
    assert selected != prepared["tools"][0]["name"]
    body["tool_choice"]["namespace"] = "missing"
    with pytest.raises(ConversionError, match="declared function"):
        NamespaceMap().prepare(body)
