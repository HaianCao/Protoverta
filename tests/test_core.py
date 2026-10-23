import pytest

from protoverta import ConversionError, Protocol, convert_request, convert_response


@pytest.mark.parametrize("source,target", [(a, b) for a in Protocol for b in Protocol if a != b])
def test_all_six_text_request_edges(source, target):
    request = {
        Protocol.CHAT: {"model": "example", "messages": [{"role": "user", "content": "hello"}], "max_completion_tokens": 64},
        Protocol.MESSAGES: {"model": "example", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 64},
        Protocol.RESPONSES: {"model": "example", "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}], "max_output_tokens": 64},
    }[source]
    output = convert_request(request, source, target)
    assert output["model"] == "example"
    assert "hello" in str(output)


@pytest.mark.parametrize("source,target", [(a, b) for a in Protocol for b in Protocol if a != b])
def test_all_six_text_response_edges(source, target):
    response = {
        Protocol.CHAT: {"model": "example", "choices": [{"message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 2, "completion_tokens": 1}},
        Protocol.MESSAGES: {"model": "example", "content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn", "usage": {"input_tokens": 2, "output_tokens": 1}},
        Protocol.RESPONSES: {"model": "example", "status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hello", "annotations": []}]}], "usage": {"input_tokens": 2, "output_tokens": 1}},
    }[source]
    output = convert_response(response, source, target)
    assert output["model"] == "example"
    assert "hello" in str(output)
    if target == Protocol.RESPONSES:
        assert output["tools"] == [] and output["tool_choice"] == "auto"
        assert output["usage"]["input_tokens_details"] == {"cached_tokens": 0}
        assert output["usage"]["output_tokens_details"] == {"reasoning_tokens": 0}


def test_tool_call_and_result_chat_to_messages():
    body = {"model": "m", "max_tokens": 128, "messages": [
        {"role": "user", "content": "calculate"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "add", "arguments": '{"a":1,"b":2}'}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "3"},
    ]}
    result = convert_request(body, "chat", "messages")
    assert result["messages"][1]["content"][0]["type"] == "tool_use"
    assert result["messages"][2]["content"][0]["tool_use_id"] == "call_1"


@pytest.mark.parametrize("source,target", [(a, b) for a in Protocol for b in Protocol if a != b])
def test_all_six_function_request_edges(source, target):
    requests = {
        Protocol.CHAT: {"model": "m", "max_tokens": 64, "messages": [
            {"role": "user", "content": "calculate"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "add", "arguments": '{"a":1}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "2"},
        ]},
        Protocol.MESSAGES: {"model": "m", "max_tokens": 64, "messages": [
            {"role": "user", "content": "calculate"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "add", "input": {"a": 1}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "2"}]},
        ]},
        Protocol.RESPONSES: {"model": "m", "max_output_tokens": 64, "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "calculate"}]},
            {"type": "function_call", "call_id": "call_1", "name": "add", "arguments": '{"a":1}'},
            {"type": "function_call_output", "call_id": "call_1", "output": "2"},
        ]},
    }
    converted = convert_request(requests[source], source, target)
    assert "call_1" in str(converted)
    assert "add" in str(converted)
    assert "2" in str(converted)


@pytest.mark.parametrize("source,target", [(a, b) for a in Protocol for b in Protocol if a != b])
def test_all_six_function_response_edges(source, target):
    responses = {
        Protocol.CHAT: {"model": "m", "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "add", "arguments": '{"a":1}'}}]}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}},
        Protocol.MESSAGES: {"model": "m", "content": [{"type": "tool_use", "id": "call_1", "name": "add", "input": {"a": 1}}], "stop_reason": "tool_use", "usage": {"input_tokens": 5, "output_tokens": 3}},
        Protocol.RESPONSES: {"model": "m", "status": "completed", "output": [{"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "add", "arguments": '{"a":1}', "status": "completed"}], "usage": {"input_tokens": 5, "output_tokens": 3}},
    }
    converted = convert_response(responses[source], source, target)
    assert "call_1" in str(converted)
    assert "add" in str(converted)


def test_no_fake_reasoning_or_server_state():
    with pytest.raises(ConversionError, match="unsupported fields"):
        convert_request({"model": "m", "input": "hello", "previous_response_id": "resp_old"}, "responses", "chat")
    with pytest.raises(ConversionError, match="unsupported Messages block"):
        convert_request({"model": "m", "max_tokens": 64, "messages": [{"role": "assistant", "content": [{"type": "thinking", "thinking": "private", "signature": "abc"}]}]}, "messages", "chat")


def test_developer_role_not_silently_rewritten():
    with pytest.raises(ConversionError, match="developer"):
        convert_request({"model": "m", "max_tokens": 64, "messages": [{"role": "developer", "content": "policy"}]}, "chat", "messages")


def test_no_fabricated_usage():
    response = {"model": "m", "content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn"}
    assert "usage" not in convert_response(response, "messages", "chat")
    with pytest.raises(ConversionError, match="real upstream token usage"):
        convert_response({"model": "m", "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}]}, "chat", "messages")


def test_length_finish_reason_is_preserved():
    source = {"model": "m", "choices": [{"message": {"role": "assistant", "content": "truncated"}, "finish_reason": "length"}], "usage": {"prompt_tokens": 3, "completion_tokens": 5}}
    assert convert_response(source, "chat", "messages")["stop_reason"] == "max_tokens"
    target = convert_response(source, "chat", "responses")
    assert target["status"] == "incomplete"
    assert target["incomplete_details"] == {"reason": "max_output_tokens"}
    assert target["output"][0]["status"] == "incomplete"


def test_same_protocol_keeps_extensions():
    body = {"model": "m", "input": "hello", "vendor_extension": {"x": 1}}
    assert convert_request(body, "responses", "responses") == body


def test_invalid_tool_json_rejected():
    body = {"model": "m", "messages": [{"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{"}}]}]}
    with pytest.raises(ConversionError, match="valid JSON"):
        convert_request(body, "chat", "responses")


def test_interleaved_text_and_tool_order_is_not_rewritten():
    response = {"model": "m", "content": [
        {"type": "tool_use", "id": "call_1", "name": "f", "input": {}},
        {"type": "text", "text": "after"},
    ], "stop_reason": "tool_use", "usage": {"input_tokens": 2, "output_tokens": 2}}
    with pytest.raises(ConversionError, match="text after a tool call"):
        convert_response(response, "messages", "chat")


def test_responses_preserves_tool_result_before_user_text():
    body = {"model": "m", "max_tokens": 64, "messages": [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "f", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "done"},
            {"type": "text", "text": "continue"},
        ]},
    ]}
    items = convert_request(body, "messages", "responses")["input"]
    assert [item.get("type", "message") for item in items] == ["function_call", "function_call_output", "message"]


def test_mismatched_tool_finish_rejected():
    body = {"model": "m", "choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "tool_calls"}]}
    with pytest.raises(ConversionError, match="does not match"):
        convert_response(body, "chat", "responses")


def test_assistant_history_is_a_completed_responses_item():
    chat = {"model": "m", "messages": [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
    ], "max_tokens": 64}
    converted = convert_request(chat, "chat", "responses")
    assistant = converted["input"][1]
    assert assistant["type"] == "message"
    assert assistant["status"] == "completed"
    assert assistant["content"][0]["type"] == "output_text"
    assert "reply" in str(convert_request(converted, "responses", "chat"))
