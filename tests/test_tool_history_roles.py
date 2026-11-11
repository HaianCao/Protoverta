"""Reject tool fields in invalid roles before dropping or sending any content."""

import copy
import io
import json
from urllib.error import HTTPError

import pytest
from test_json_wire_boundaries import RESULT, encode_json, listener, request
from test_semantic_tool_contracts import EDGES, history

from protoverta import ConversionError, convert_request, convert_response

CHAT_CALL = {"id": "c", "type": "function", "function": {"name": "fixture", "arguments": "{}"}}
MESSAGE_CALL = {"type": "tool_use", "id": "c", "name": "fixture", "input": {}}
MESSAGE_RESULT = {"type": "tool_result", "tool_use_id": "c", "content": "synthetic result"}


@pytest.mark.parametrize("target", ["messages", "responses"])
@pytest.mark.parametrize("role", ["user", "system", "developer"])
@pytest.mark.parametrize("calls", [[CHAT_CALL], [], None, False])
def test_chat_tool_calls_on_nonassistant_roles_are_never_ignored(target, role, calls):
    body = {"model": "m", "max_tokens": 20, "messages": [{"role": role, "content": "synthetic", "tool_calls": calls}]}
    original = copy.deepcopy(body)
    with pytest.raises(ConversionError, match="assistant role"):
        convert_request(body, "chat", target)
    assert body == original
    assert convert_request(body, "chat", "chat") == body


def bad_messages(role, kind):
    messages = []
    if kind == "tool_result":
        messages.append({"role": "assistant", "content": [copy.deepcopy(MESSAGE_CALL)]})
    messages.append({"role": role, "content": [copy.deepcopy(MESSAGE_CALL if kind == "tool_use" else MESSAGE_RESULT)]})
    return {"model": "m", "max_tokens": 20, "messages": messages}


@pytest.mark.parametrize("target", ["chat", "responses"])
@pytest.mark.parametrize(
    "role,kind", [("user", "tool_use"), ("system", "tool_use"), ("assistant", "tool_result"), ("system", "tool_result")]
)
def test_messages_tool_blocks_require_the_correct_role(target, role, kind):
    body = bad_messages(role, kind)
    original = copy.deepcopy(body)
    with pytest.raises(ConversionError, match="role"):
        convert_request(body, "messages", target)
    assert body == original
    assert convert_request(body, "messages", "messages") == body


@pytest.mark.parametrize("source,target", EDGES)
def test_correct_tool_history_still_round_trips_on_all_six_routes(source, target):
    body = history(source, ids=("first", "second"))
    original = copy.deepcopy(body)
    converted = convert_request(body, source, target)
    restored = convert_request(converted, target, source)
    assert "first" in json.dumps(restored) and "second" in json.dumps(restored)
    assert convert_request(restored, source, target) == converted
    assert body == original


@pytest.mark.parametrize(
    "source,target", [("chat", "messages"), ("chat", "responses"), ("messages", "chat"), ("messages", "responses")]
)
@pytest.mark.parametrize("stream", [False, True])
def test_wrong_tool_roles_return_422_without_upstream_dispatch_and_valid_request_recovers(source, target, stream):
    calls = []

    def upstream(req, *, timeout):
        calls.append(req)
        return io.BytesIO(encode_json(convert_response(RESULT, "chat", target)))

    if source == "chat":
        invalid = [
            {
                "model": "m",
                "max_tokens": 20,
                "messages": [{"role": "user", "content": "synthetic", "tool_calls": [CHAT_CALL]}],
            }
        ]
    else:
        invalid = [bad_messages("user", "tool_use"), bad_messages("assistant", "tool_result")]
    with listener(target, upstream) as origin:
        for body in invalid:
            body["stream"] = stream
            with pytest.raises(HTTPError) as error:
                request(origin, source, encode_json(body))
            with error.value as response:
                assert response.code == 422
                assert response.headers["x-should-retry"] == "false"
                assert isinstance(json.load(response)["error"], dict)
        assert calls == []
        with request(origin, source, encode_json(history(source))) as response:
            assert response.status == 200
        assert len(calls) == 1
