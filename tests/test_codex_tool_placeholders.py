import copy

import pytest

from protoverta.core import ConversionError, Protocol, convert_request
from protoverta.profiles import prepare_codex_request


def request(text):
    return {"model": "m", "input": [
        {"type": "function_call", "call_id": "call_1", "name": "get_goal", "arguments": "{}"},
        {"role": "assistant", "content": [{"type": "output_text", "text": text}]},
        {"type": "function_call_output", "call_id": "call_1", "output": "No active goal"},
    ]}


@pytest.mark.parametrize("text", ["", "\n", " \t\r\n"])
def test_only_explicit_codex_profile_omits_blank_post_tool_placeholder(text):
    body = request(text)
    original = copy.deepcopy(body)
    with pytest.raises(ConversionError, match="text after"):
        convert_request(body, "responses", "chat")
    converted = convert_request(prepare_codex_request(body, Protocol.CHAT), "responses", "chat")
    assert [message["role"] for message in converted["messages"]] == ["assistant", "tool"]
    assert converted["messages"][1]["tool_call_id"] == "call_1"
    assert body == original


@pytest.mark.parametrize("extra", [{"text": "Meaningful commentary"}, {"annotations": [{"type": "url_citation"}]}, {"unknown": True}])
def test_profile_never_discards_meaningful_or_unsupported_post_tool_content(extra):
    body = request("\n")
    body["input"][1]["content"][0].update(extra)
    prepared = prepare_codex_request(body, Protocol.CHAT)
    assert len(prepared["input"]) == 3
    with pytest.raises(ConversionError):
        convert_request(prepared, "responses", "chat")
