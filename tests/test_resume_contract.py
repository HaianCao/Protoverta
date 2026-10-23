import copy
import json

import pytest

from protoverta.core import ConversionError, Protocol, convert_request
from protoverta.profiles import prepare_codex_request


def history():
    return {"model": "model-new", "max_output_tokens": 256, "input": [
        {"role": "user", "content": "Original question"},
        {"type": "function_call", "name": "lookup", "call_id": "old_call", "arguments": '{"x":1}'},
        {"type": "function_call_output", "call_id": "old_call", "output": "Original tool result"},
        {"role": "assistant", "content": "Original answer"},
        {"role": "user", "content": "Follow-up question"},
    ]}


@pytest.mark.parametrize("source,target", [(a, b) for a in Protocol for b in Protocol if a != b])
def test_complete_text_tool_history_replays_statelessly_with_selected_model(source, target):
    # Prepare wire fixtures through public conversion, then replay all prior turns.
    body = convert_request(history(), "responses", source)
    original = copy.deepcopy(body)
    converted = convert_request(body, source, target)
    replayed = convert_request(converted, target, "responses")
    assert replayed["model"] == "model-new"
    inputs = replayed["input"]
    call = next(item for item in inputs if item.get("type") == "function_call")
    result = next(item for item in inputs if item.get("type") == "function_call_output")
    assert call["call_id"] == result["call_id"] == "old_call"
    assert json.loads(call["arguments"]) == {"x": 1}
    assert result["output"] == "Original tool result"
    assert "Original answer" in str(inputs)
    assert "Follow-up question" in str(inputs)
    assert body == original


@pytest.mark.parametrize("target", ["chat", "messages"])
def test_encrypted_reasoning_history_is_not_silently_replayed(target):
    body = history()
    body["input"].insert(1, {"type": "reasoning", "encrypted_content": "opaque-state", "summary": []})
    with pytest.raises(ConversionError):
        convert_request(body, "responses", target)


def test_messages_signed_thinking_history_is_not_silently_replayed():
    body = {"model": "m", "max_tokens": 128, "messages": [{"role": "assistant", "content": [{"type": "thinking", "thinking": "private", "signature": "opaque-signature"}]}]}
    with pytest.raises(ConversionError):
        convert_request(body, "messages", "chat")


def test_server_side_response_id_is_not_a_portable_history():
    body = history()
    body["previous_response_id"] = "resp_private_to_old_provider"
    with pytest.raises(ConversionError):
        convert_request(body, "responses", "chat")


def test_codex_resume_namespaced_tools_have_actionable_error_and_are_not_removed():
    body = history()
    body["tools"] = [{"type": "namespace", "name": "functions", "tools": []}]
    original = copy.deepcopy(body)
    with pytest.raises(ConversionError, match="namespace_tools='flatten'"):
        prepare_codex_request(body, Protocol.CHAT)
    assert body == original
