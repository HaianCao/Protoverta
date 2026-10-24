import copy
import io
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from protoverta import ConversionError, convert_request, convert_response
from protoverta.server import GatewayConfig, make_handler
from protoverta.tool_search import ClientToolSearchMap


def body():
    return {"model": "m", "input": "find a tool", "max_output_tokens": 64, "tools": [
        {"type": "tool_search", "execution": "client", "description": "Search tools", "parameters": {"type": "object", "properties": {"goal": {"type": "string"}}}},
        {"type": "function", "name": "deferred", "defer_loading": True, "parameters": {"type": "object"}}]}


def loaded():
    return {"type": "function", "name": "lookup", "parameters": {"type": "object"}, "defer_loading": True}


def history():
    return [{"type": "tool_search_call", "execution": "client", "status": "completed", "call_id": "search1", "arguments": {"goal": "lookup"}},
            {"type": "tool_search_output", "execution": "client", "status": "completed", "call_id": "search1", "tools": [loaded()]}]


def upstream_call(target, name="pv_client_tool_search"):
    if target == "chat":
        return {"model": "m", "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "s2", "type": "function", "function": {"name": name, "arguments": '{"goal":"next"}'}}]}, "finish_reason": "tool_calls"}]}
    return {"model": "m", "content": [{"type": "tool_use", "id": "s2", "name": name, "input": {"goal": "next"}}], "stop_reason": "tool_use", "usage": {"input_tokens": 4, "output_tokens": 5}}


@pytest.mark.parametrize("target", ["chat", "messages"])
def test_search_request_and_returned_call_are_explicit_and_reversible(target):
    original = body()
    before = copy.deepcopy(original)
    mapper = ClientToolSearchMap()
    prepared = mapper.prepare(original)
    assert len(prepared["tools"]) == 1  # Unloaded schemas must not leak eagerly.
    converted = convert_request(prepared, "responses", target)
    tool = converted["tools"][0].get("function", converted["tools"][0])
    assert tool["name"] == "pv_client_tool_search"
    assert tool.get("parameters", tool.get("input_schema")) == original["tools"][0]["parameters"]
    result = mapper.restore(convert_response(upstream_call(target), target, "responses"))
    call = result["output"][0]
    assert call["type"] == "tool_search_call" and call["execution"] == "client"
    assert call["arguments"] == {"goal": "next"} and call["call_id"] == "s2"
    assert "name" not in call
    assert original == before


def test_paired_history_loads_definitions_and_preserves_result_identity():
    request = body()
    request["input"] = history()
    original = copy.deepcopy(request)
    mapper = ClientToolSearchMap()
    prepared = mapper.prepare(request)
    assert [tool["name"] for tool in prepared["tools"]] == ["lookup", "pv_client_tool_search"]
    chat = convert_request(prepared, "responses", "chat")
    assert chat["messages"][1]["tool_call_id"] == "search1"
    assert json.loads(chat["messages"][1]["content"])["tools"] == [loaded()]
    assert request == original


@pytest.mark.parametrize("mutation", ["stream", "hosted", "missing_execution", "missing_output", "orphan", "duplicate", "conflict", "reserved", "schema", "unknown"])
def test_ambiguous_lossy_or_unverified_search_shapes_fail_closed(mutation):
    request = body()
    request["input"] = history()
    if mutation == "stream":
        request["stream"] = True
    elif mutation == "hosted":
        request["tools"][0]["execution"] = "server"
    elif mutation == "missing_execution":
        request["input"][0].pop("execution")
    elif mutation == "missing_output":
        request["input"].pop()
    elif mutation == "orphan":
        request["input"].pop(0)
    elif mutation == "duplicate":
        request["input"].append(copy.deepcopy(request["input"][1]))
    elif mutation == "conflict":
        request["tools"].append({"type": "function", "name": "lookup", "parameters": {"type": "object", "properties": {"other": {"type": "string"}}}})
    elif mutation == "reserved":
        request["tools"].append({"type": "function", "name": "pv_client_tool_search"})
    elif mutation == "schema":
        request["tools"][0]["parameters"] = {"type": "array"}
    else:
        request["input"][1]["extra"] = True
    with pytest.raises(ConversionError):
        ClientToolSearchMap().prepare(request)


def test_loaded_custom_grammar_and_native_passthrough_remain_intact():
    request = body()
    request["input"] = history()
    custom = {"type": "custom", "name": "exec", "format": {"type": "grammar", "syntax": "regex", "definition": "[a-z]+"}}
    request["input"][1]["tools"] = [custom]
    prepared = ClientToolSearchMap().prepare(request)
    assert prepared["tools"][0] == custom
    assert convert_request(prepared, "responses", "chat")["tools"][0]["custom"]["format"]["grammar"]["definition"] == "[a-z]+"
    assert convert_request(request, "responses", "responses") == request


def test_namespace_deferred_children_require_explicit_search_output():
    from protoverta.namespaces import NamespaceMap

    request = body()
    namespace = {"type": "namespace", "name": "ns", "tools": [
        {"type": "function", "name": "immediate", "parameters": {"type": "object"}},
        {"type": "function", "name": "not_loaded", "parameters": {"type": "object"}, "defer_loading": True}]}
    request["tools"].append(namespace)
    before = copy.deepcopy(request)
    prepared = ClientToolSearchMap().prepare(request)
    assert [child["name"] for child in prepared["tools"][0]["tools"]] == ["immediate"]
    wire = convert_request(NamespaceMap().prepare(prepared), "responses", "chat")
    assert len(wire["tools"]) == 2  # one immediate child plus client search
    request["input"] = history()
    request["input"][1]["tools"] = [{"type": "namespace", "name": "ns", "tools": [namespace["tools"][1]]}]
    prepared = ClientToolSearchMap().prepare(request)
    assert [child["name"] for child in prepared["tools"][0]["tools"]] == ["immediate", "not_loaded"]
    assert before["tools"] == request["tools"]


def test_namespace_with_only_unloaded_children_is_omitted():
    request = body()
    request["tools"].append({"type": "namespace", "name": "ns", "tools": [loaded()]})
    assert len(ClientToolSearchMap().prepare(request)["tools"]) == 1


@pytest.mark.parametrize("policy", ["reject", "function"])
def test_gateway_opt_in_json_search_round_trip(policy):
    captured = []

    def upstream(request, *, timeout):
        captured.append(json.loads(request.data))
        return io.BytesIO(json.dumps(upstream_call("chat")).encode())

    config = GatewayConfig("chat", "https://example.test/v1", "key", "local", client_tool_search=policy)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=upstream))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(f"http://127.0.0.1:{server.server_port}/v1/responses", data=json.dumps(body()).encode(), headers={"Authorization": "Bearer local"})
        if policy == "reject":
            with pytest.raises(HTTPError) as error:
                urlopen(request, timeout=3)
            assert error.value.code == 422 and not captured
            error.value.close()
        else:
            with urlopen(request, timeout=3) as response:
                result = json.load(response)
            assert captured[0]["tools"][0]["function"]["name"] == "pv_client_tool_search"
            assert result["output"][0]["type"] == "tool_search_call"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
