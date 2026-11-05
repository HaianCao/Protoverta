import copy

import pytest

from protoverta import ConversionError, convert_request, convert_response
from protoverta.capabilities import request_features


def message_result():
    return {
        "model": "m",
        "max_tokens": 64,
        "messages": [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call_one", "name": "screenshot", "input": {}}]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_one",
                        "content": [
                            {"type": "text", "text": "before"},
                            {"type": "image", "source": {"type": "url", "url": "https://example.test/image.png"}},
                            {"type": "text", "text": "after"},
                        ],
                    }
                ],
            },
        ],
    }


def test_rich_tool_results_preserve_role_identity_order_and_content():
    body = message_result()
    original = copy.deepcopy(body)
    responses = convert_request(body, "messages", "responses")
    result = responses["input"][1]
    assert result["call_id"] == "call_one"
    assert [part["type"] for part in result["output"]] == ["input_text", "input_image", "input_text"]
    restored = convert_request(responses, "responses", "messages")
    assert restored["messages"] == body["messages"]
    assert body == original
    assert {"images", "image_tool_results"} <= request_features(responses, "responses")


def test_rich_tool_results_keep_base64_and_do_not_relocate_to_chat_user():
    body = message_result()
    body["messages"][1]["content"][0]["content"][1]["source"] = {"type": "base64", "media_type": "image/png", "data": "aW1hZ2U="}
    responses = convert_request(body, "messages", "responses")
    assert convert_request(responses, "responses", "messages")["messages"] == body["messages"]
    with pytest.raises(ConversionError, match="relocating"):
        convert_request(responses, "responses", "chat")


@pytest.mark.parametrize("source,target", [("chat", "messages"), ("messages", "chat")])
def test_stop_sequences_are_mapped_without_mutating_input(source, target):
    body = {"model": "m", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]}
    body["stop" if source == "chat" else "stop_sequences"] = ["END", "STOP"]
    original = copy.deepcopy(body)
    out = convert_request(body, source, target)
    assert out["stop" if target == "chat" else "stop_sequences"] == ["END", "STOP"]
    assert "stop_sequences" in request_features(out, target)
    assert body == original
    with pytest.raises(ConversionError, match="stop-sequence"):
        convert_request(body, source, "responses")


@pytest.mark.parametrize("value", [True, [], [""], ["a"] * 5, [1], {"x": "y"}])
def test_invalid_stops_are_rejected(value):
    with pytest.raises(ConversionError):
        convert_request({"model": "m", "messages": [], "max_tokens": 64, "stop": value}, "chat", "messages")


@pytest.mark.parametrize("source,target", [("chat", "responses"), ("responses", "chat")])
@pytest.mark.parametrize("strict", [True, False])
def test_explicit_strict_tool_flag_and_schema_are_preserved(source, target, strict):
    fn = {"name": "fn", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}, "strict": strict}
    body = (
        {"model": "m", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}], "tools": [{"type": "function", "function": fn}]}
        if source == "chat"
        else {"model": "m", "max_output_tokens": 64, "input": "hi", "tools": [{"type": "function", **fn}]}
    )
    out = convert_request(body, source, target)
    tool = out["tools"][0]["function"] if target == "chat" else out["tools"][0]
    assert tool["parameters"] == fn["parameters"] and tool["strict"] is strict
    assert ("strict_tools" in request_features(out, target)) == strict
    if strict:
        assert convert_request(body, source, "messages")["tools"][0]["strict"] is True


def test_matched_stop_sequence_metadata_still_fails_closed():
    with pytest.raises(ConversionError):
        convert_response(
            {"model": "m", "content": [{"type": "text", "text": "done"}], "stop_reason": "stop_sequence", "stop_sequence": "END"}, "messages", "chat"
        )


def test_nonstrict_chat_function_does_not_enable_responses_schema_normalization():
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "fn", "parameters": {"type": "object", "properties": {}}}}],
    }
    assert convert_request(body, "chat", "responses")["tools"][0]["strict"] is False
