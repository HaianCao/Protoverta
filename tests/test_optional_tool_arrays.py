"""Only absent/null optional arrays may normalize to empty arrays."""

import copy
import io
import json
from urllib.error import HTTPError

import pytest
from test_json_wire_boundaries import RESULT, VALID, encode_json, listener, request
from test_semantic_tool_contracts import EDGES

from protoverta import ConversionError, convert_request, convert_response

INVALID = [False, True, 0, 1, 0.0, "", "fixture", {}, {"name": "fixture"}]


def text_request(source):
    body = copy.deepcopy(VALID)
    body.pop("tools")
    return convert_request(body, "chat", source)


@pytest.mark.parametrize("source,target", EDGES)
@pytest.mark.parametrize("value", INVALID)
def test_tools_rejects_nonarrays_on_every_translated_route(source, target, value):
    body = text_request(source)
    body["tools"] = value
    before = copy.deepcopy(body)
    with pytest.raises(ConversionError, match="tools must be an array"):
        convert_request(body, source, target)
    assert body == before
    assert convert_request(body, source, source) == body


@pytest.mark.parametrize("source,target", EDGES)
@pytest.mark.parametrize("form", ["missing", "null", "empty"])
def test_absent_null_and_empty_tools_remain_compatible(source, target, form):
    body = text_request(source)
    if form != "missing":
        body["tools"] = None if form == "null" else []
    assert convert_request(body, source, target) == convert_request(text_request(source), source, target)


@pytest.mark.parametrize("target", ["messages", "responses"])
@pytest.mark.parametrize("value", INVALID)
def test_chat_history_tool_calls_rejects_nonarrays(target, value):
    body = {
        "model": "m",
        "max_tokens": 20,
        "messages": [{"role": "assistant", "content": "synthetic", "tool_calls": value}],
    }
    before = copy.deepcopy(body)
    with pytest.raises(ConversionError, match="tool_calls must be an array"):
        convert_request(body, "chat", target)
    assert body == before


@pytest.mark.parametrize("target", ["messages", "responses"])
@pytest.mark.parametrize("form", ["missing", "null", "empty"])
def test_optional_chat_history_tool_calls_still_allows_text(target, form):
    body = {"model": "m", "max_tokens": 20, "messages": [{"role": "assistant", "content": "synthetic"}]}
    expected = convert_request(body, "chat", target)
    if form != "missing":
        body["messages"][0]["tool_calls"] = None if form == "null" else []
    # Responses assigns fresh message IDs; compare decoded history semantics.
    assert convert_request(convert_request(body, "chat", target), target, "chat") == convert_request(
        expected, target, "chat"
    )


@pytest.mark.parametrize("target", ["messages", "responses"])
@pytest.mark.parametrize("value", INVALID)
def test_completed_chat_tool_calls_rejects_nonarrays(target, value):
    body = copy.deepcopy(RESULT)
    body["choices"][0]["message"]["tool_calls"] = value
    before = copy.deepcopy(body)
    with pytest.raises(ConversionError, match="tool_calls must be an array"):
        convert_response(body, "chat", target)
    assert body == before
    assert convert_response(body, "chat", "chat") == body


@pytest.mark.parametrize("target", ["messages", "responses"])
@pytest.mark.parametrize("form", ["missing", "null", "empty"])
def test_optional_completed_chat_tool_calls_still_allows_text(target, form):
    body = copy.deepcopy(RESULT)
    if form != "missing":
        body["choices"][0]["message"]["tool_calls"] = None if form == "null" else []
    assert "valid 🌏" in json.dumps(convert_response(body, "chat", target), ensure_ascii=False)


@pytest.mark.parametrize("source,target", EDGES)
@pytest.mark.parametrize("stream", [False, True])
def test_invalid_request_arrays_never_dispatch_and_next_request_recovers(source, target, stream):
    calls = []

    def upstream(req, *, timeout):
        calls.append(req)
        return io.BytesIO(encode_json(convert_response(RESULT, "chat", target)))

    with listener(target, upstream) as origin:
        for value in INVALID:
            bodies = [{**text_request(source), "tools": value, "stream": stream}]
            if source == "chat":
                bodies.append(
                    {
                        "model": "m",
                        "max_tokens": 20,
                        "stream": stream,
                        "messages": [{"role": "assistant", "content": "synthetic", "tool_calls": value}],
                    }
                )
            for body in bodies:
                with pytest.raises(HTTPError) as error:
                    request(origin, source, encode_json(body))
                with error.value as response:
                    assert response.code == 422
                    assert response.headers["x-should-retry"] == "false"
                    assert isinstance(json.load(response)["error"], dict)
        assert calls == []
        with request(origin, source, encode_json(text_request(source))) as response:
            assert response.status == 200
        assert len(calls) == 1


@pytest.mark.parametrize("target", ["messages", "responses"])
def test_invalid_upstream_tool_arrays_return_502_and_valid_response_recovers(target):
    bodies = []
    for value in INVALID:
        body = copy.deepcopy(RESULT)
        body["choices"][0]["message"]["tool_calls"] = value
        bodies.append(encode_json(body))
    replies = iter([*bodies, encode_json(RESULT)])
    with listener("chat", lambda req, timeout: io.BytesIO(next(replies))) as origin:
        for _ in INVALID:
            with pytest.raises(HTTPError) as error:
                request(origin, target, encode_json(text_request(target)))
            with error.value as response:
                assert response.code == 502
                assert isinstance(json.load(response)["error"], dict)
        with request(origin, target, encode_json(text_request(target))) as response:
            assert response.status == 200
