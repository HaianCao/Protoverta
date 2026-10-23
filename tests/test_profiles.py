import copy

import pytest

from protoverta.core import ConversionError, Protocol, convert_request
from protoverta.profiles import prepare_codex_request


def codex_request():
    return {
        "model": "example-model", "store": False, "stream": True,
        "input": [{"role": "developer", "content": "Project instructions"}, {"role": "user", "content": "Hello"}],
        "client_metadata": {"client": "codex"}, "prompt_cache_key": "opaque-key",
        "include": ["reasoning.encrypted_content"],
        "reasoning": {"summary": "none", "effort": "medium"},
        "parallel_tool_calls": True,
    }


def test_codex_profile_converts_only_explicit_hints_and_preserves_source():
    body = codex_request()
    original = copy.deepcopy(body)
    with pytest.raises(ConversionError, match="unsupported fields"):
        convert_request(body, "responses", "chat")
    prepared = prepare_codex_request(body, Protocol.CHAT)
    converted = convert_request(prepared, "responses", "chat")
    assert converted["reasoning_effort"] == "medium"
    assert converted["parallel_tool_calls"] is True
    assert converted["messages"][0]["role"] == "developer"
    assert body == original


def test_messages_profile_applies_budget_and_initial_instruction_policy():
    prepared = prepare_codex_request(codex_request(), Protocol.MESSAGES, default_max_output_tokens=8192)
    converted = convert_request(prepared, "responses", "messages")
    assert converted["max_tokens"] == 8192
    assert converted["output_config"] == {"effort": "medium"}
    assert converted["system"] == [{"type": "text", "text": "Project instructions"}]
    body = codex_request()
    body["max_output_tokens"] = 256
    assert prepare_codex_request(body, Protocol.MESSAGES, default_max_output_tokens=8192)["max_output_tokens"] == 256


@pytest.mark.parametrize("field,value", [
    ("include", ["web_search_call.action.sources"]),
    ("reasoning", {"summary": "auto"}),
    ("text", {"format": {"type": "json_object"}}),
])
def test_codex_profile_rejects_unmapped_behavior(field, value):
    body = codex_request()
    body[field] = value
    with pytest.raises(ConversionError):
        prepare_codex_request(body, Protocol.CHAT)


def test_codex_profile_does_not_relocate_later_instructions():
    body = codex_request()
    body["input"].append({"role": "developer", "content": "Later instructions"})
    with pytest.raises(ConversionError, match="mid-conversation"):
        prepare_codex_request(body, Protocol.MESSAGES, default_max_output_tokens=4096)


def test_multiple_response_tool_calls_stay_in_one_assistant_turn():
    body = {
        "model": "m", "input": [
            {"type": "function_call", "call_id": f"call_{index}", "name": "lookup", "arguments": "{}"}
            for index in range(2)
        ] + [
            {"type": "function_call_output", "id": f"output_{index}", "call_id": f"call_{index}", "output": [{"type": "input_text", "text": f"result_{index}"}]}
            for index in range(2)
        ],
    }
    converted = convert_request(body, "responses", "chat")
    assert [item["role"] for item in converted["messages"]] == ["assistant", "tool", "tool"]
    assert len(converted["messages"][0]["tool_calls"]) == 2
    assert converted["messages"][2]["content"] == "result_1"


def test_effort_and_sequential_tool_choice_round_trip():
    body = {"model": "m", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 16,
            "reasoning_effort": "low", "parallel_tool_calls": False,
            "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]}
    messages = convert_request(body, "chat", "messages")
    assert messages["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    result = convert_request(messages, "messages", "chat")
    assert result["reasoning_effort"] == "low"
    assert result["parallel_tool_calls"] is False
    body["tool_choice"] = "none"
    assert convert_request(body, "chat", "messages")["tool_choice"] == {"type": "none"}
