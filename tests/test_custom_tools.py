import copy

import pytest

from protoverta import ConversionError, convert_request, convert_response
from protoverta.capabilities import (
    CapabilityRecord,
    CapabilityRegistry,
    request_features,
)
from protoverta.tool_names import ToolNameMap


def chat_request():
    return {
        "model": "m",
        "messages": [
            {"role": "user", "content": "run"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "type": "custom", "custom": {"name": "execute", "input": "print('xin chào')\nnot JSON"}},
                    {"id": "c2", "type": "function", "function": {"name": "lookup", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
            {"role": "tool", "tool_call_id": "c2", "content": "found"},
        ],
        "tools": [
            {
                "type": "custom",
                "custom": {
                    "name": "execute",
                    "description": "execute code",
                    "format": {"type": "grammar", "grammar": {"syntax": "lark", "definition": "start: /.+/"}},
                },
            },
            {"type": "function", "function": {"name": "lookup", "parameters": {"type": "object", "properties": {}}}},
        ],
        "tool_choice": {"type": "custom", "custom": {"name": "execute"}},
    }


def test_request_round_trip_preserves_raw_input_grammar_identity_and_order():
    body = chat_request()
    original = copy.deepcopy(body)
    responses = convert_request(body, "chat", "responses")
    assert responses["tools"][0]["format"] == {"type": "grammar", "syntax": "lark", "definition": "start: /.+/"}
    assert [item.get("type", "message") for item in responses["input"]] == [
        "message",
        "custom_tool_call",
        "function_call",
        "custom_tool_call_output",
        "function_call_output",
    ]
    restored = convert_request(responses, "responses", "chat")
    assert restored["messages"] == body["messages"]
    assert restored["tools"][0] == body["tools"][0]
    assert restored["tool_choice"] == body["tool_choice"]
    assert body == original


@pytest.mark.parametrize("form", [None, {"type": "text"}, {"type": "grammar", "grammar": {"syntax": "regex", "definition": "[a-z]+"}}])
def test_optional_formats_are_preserved(form):
    body = chat_request()
    tool = body["tools"][0]["custom"]
    tool.pop("description")
    tool.pop("format")
    if form is not None:
        tool["format"] = form
    responses = convert_request(body, "chat", "responses")
    assert convert_request(responses, "responses", "chat")["tools"][0] == body["tools"][0]


def chat_response():
    return {
        "model": "m",
        "choices": [{"index": 0, "message": chat_request()["messages"][1], "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
    }


def test_completed_response_round_trip_keeps_custom_and_function_calls():
    body = chat_response()
    original = copy.deepcopy(body)
    response = convert_response(body, "chat", "responses")
    assert response["output"][0]["type"] == "custom_tool_call"
    assert response["output"][0]["input"] == body["choices"][0]["message"]["tool_calls"][0]["custom"]["input"]
    restored = convert_response(response, "responses", "chat")
    assert restored["choices"] == body["choices"]
    assert restored["usage"] == {**body["usage"], "total_tokens": 7}
    assert body == original


def test_messages_and_unverified_custom_streaming_fail_explicitly():
    body = chat_request()
    with pytest.raises(ConversionError, match="Messages cannot preserve native custom"):
        convert_request(body, "chat", "messages")
    with pytest.raises(ConversionError, match="Messages cannot preserve native custom"):
        convert_response(chat_response(), "chat", "messages")
    body["stream"] = True
    with pytest.raises(ConversionError, match="streaming is not verified"):
        convert_request(body, "chat", "responses")


@pytest.mark.parametrize(
    "form",
    [
        {},
        {"type": "grammar"},
        {"type": "text", "grammar": {}},
        {"type": "grammar", "grammar": {"syntax": [], "definition": "a"}},
        {"type": "grammar", "grammar": {"syntax": "regex", "definition": "a", "extra": True}},
    ],
)
def test_invalid_grammar_is_not_dropped_or_rewritten(form):
    body = chat_request()
    body["tools"][0]["custom"]["format"] = form
    with pytest.raises(ConversionError):
        convert_request(body, "chat", "responses")


def test_type_mismatch_and_incomplete_history_are_rejected():
    response = convert_request(chat_request(), "chat", "responses")
    response["input"][3]["type"] = "function_call_output"
    with pytest.raises(ConversionError, match="type does not match"):
        convert_request(response, "responses", "chat")
    response["input"][3]["type"] = "custom_tool_call_output"
    response["input"][1]["status"] = "incomplete"
    with pytest.raises(ConversionError, match="incomplete"):
        convert_request(response, "responses", "chat")
    body = chat_request()
    body["tool_choice"] = {"type": "function", "function": {"name": "execute"}}
    with pytest.raises(ConversionError, match="type does not match"):
        convert_request(body, "chat", "responses")


def test_custom_capability_is_not_inferred_from_function_support():
    body = chat_request()
    assert {"tools", "custom_tools", "forced_tool_choice"} <= request_features(body, "chat")
    record = CapabilityRecord("https://example.test/v1", "chat", "m", {"text": True, "tools": True, "custom_tools": False}, "2026-10-24", "fixture")
    with pytest.raises(ConversionError, match="custom_tools"):
        CapabilityRegistry((record,)).preflight("https://example.test/v1", "chat", body)
    assert "custom_tools" in request_features({"messages": body["messages"]}, "chat")
    assert "custom_tools" not in request_features({"messages": [{"role": "assistant", "tool_calls": None}]}, "chat")


def test_custom_aliases_only_change_names_not_raw_input():
    body = chat_request()
    for value in (body["tools"][0]["custom"], body["tool_choice"]["custom"], body["messages"][1]["tool_calls"][0]["custom"]):
        value["name"] = "tool.with.dots"
    mapping = ToolNameMap()
    prepared = mapping.prepare(body, "chat")
    converted = convert_request(prepared, "chat", "responses")
    assert converted["tools"][0]["name"].startswith("pv_tool_")
    response = chat_response()
    response["choices"][0]["message"]["tool_calls"][0]["custom"]["name"] = converted["tools"][0]["name"]
    restored = mapping.restore(response, "chat")
    assert restored["choices"][0]["message"]["tool_calls"][0]["custom"]["name"] == "tool.with.dots"
    assert prepared["messages"][1]["tool_calls"][0]["custom"]["input"] == body["messages"][1]["tool_calls"][0]["custom"]["input"]
