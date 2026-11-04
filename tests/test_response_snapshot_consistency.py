"""Generated Responses metadata has one truthful JSON/SSE contract."""

import copy
import io
import json
import threading
from contextlib import contextmanager

import pytest
from test_json_wire_boundaries import request
from wire_cases import TEXT, event, source_wire

from protoverta import StreamEncoder, StreamTranscoder, convert_response
from protoverta.response_context import CONTEXT_FIELDS
from protoverta.server import GatewayConfig, GatewayHTTPServer, make_handler
from protoverta.sse import SSEParser
from protoverta.streaming import Delta

KNOWN = {
    "instructions": None, "max_output_tokens": None, "max_tool_calls": None,
    "previous_response_id": None, "reasoning": None, "store": False,
    "background": False, "safety_identifier": None, "prompt_cache_key": None,
    "truncation": "disabled",
}
UNKNOWN = {"tools", "tool_choice", "parallel_tool_calls", "text", "temperature", "top_p"}
EXPLICIT = {
    "instructions": "", "max_output_tokens": 17, "tools": [],
    "tool_choice": "none", "parallel_tool_calls": False, "temperature": 0,
    "top_p": 0, "text": {"format": {"type": "text"}},
}


def chat_result(*, length=False):
    return {"model": "fixture-model", "choices": [{
        "message": {"role": "assistant", "content": TEXT},
        "finish_reason": "length" if length else "stop",
    }], "usage": {"prompt_tokens": 19, "completion_tokens": 7,
                  "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                  "completion_tokens_details": {"reasoning_tokens": 0}}}


def snapshots(raw):
    return [payload["response"] for item in SSEParser().feed(raw)
            if isinstance((payload := json.loads(item.data)).get("response"), dict)]


@pytest.mark.parametrize("source", ["chat", "messages"])
@pytest.mark.parametrize("length", [False, True])
@pytest.mark.parametrize("context", [None, {}, EXPLICIT, {name: None for name in CONTEXT_FIELDS}])
def test_json_and_every_stream_snapshot_share_known_facts_and_explicit_settings(source, length, context):
    before = copy.deepcopy(context)
    completed = convert_response(convert_response(chat_result(length=length), "chat", source), source, "responses", request_context=context)
    pipe = StreamTranscoder(source, "responses", request_context=context)
    wire = pipe.feed(source_wire(source, tools=False, length=length)) + pipe.finish()
    streamed = snapshots(wire)
    assert streamed[0]["status"] == "in_progress"
    assert streamed[-1]["status"] == completed["status"] == ("incomplete" if length else "completed")
    expected = {**KNOWN, **{k: v for k, v in (context or {}).items() if v is not None}}
    for snapshot in [completed, *streamed]:
        assert {key: snapshot[key] for key in expected} == expected
        for key in UNKNOWN - expected.keys():
            assert key not in snapshot
        assert "service_tier" not in snapshot
        assert not {"frequency_penalty", "presence_penalty", "top_logprobs"} & snapshot.keys()
    assert streamed[-1]["usage"] == completed["usage"]
    if source == "messages":
        assert "output_tokens_details" not in completed["usage"]
    assert context == before


@contextmanager
def listener(config, upstream):
    server = GatewayHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=upstream))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)
        assert not worker.is_alive()


@pytest.mark.parametrize("source", ["chat", "messages"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("explicit_cap", [None, 11])
def test_gateway_snapshot_reports_only_the_output_cap_actually_applied(source, stream, explicit_cap):
    calls = []
    config = GatewayConfig(source, "https://example.invalid/v1", "synthetic", "local", client_profile="codex", default_max_output_tokens=32)

    def upstream(req, *, timeout):
        calls.append(json.loads(req.data))
        raw = source_wire(source, tools=False) if stream else json.dumps(convert_response(chat_result(), "chat", source)).encode()
        return io.BytesIO(raw)

    body = {"model": "fixture-model", "input": "synthetic", "stream": stream, "text": {"format": {"type": "text"}}}
    if explicit_cap is not None:
        body["max_output_tokens"] = explicit_cap
    before = copy.deepcopy(body)
    with listener(config, upstream) as url, request(url, "responses", json.dumps(body).encode()) as response:
        raw = response.read()
    expected = explicit_cap if explicit_cap is not None else (32 if source == "messages" else None)
    assert len(calls) == 1
    assert calls[0].get("max_tokens" if source == "messages" else "max_completion_tokens") == expected
    for snapshot in snapshots(raw) if stream else [json.loads(raw)]:
        assert snapshot["max_output_tokens"] == expected
        assert snapshot["text"] == body["text"]
        assert snapshot["truncation"] == "disabled"
        assert snapshot["tools"] == [] and snapshot["tool_choice"] == "none" and snapshot["parallel_tool_calls"] is False
        assert not {"temperature", "top_p", "service_tier"} & snapshot.keys()
    assert body == before


@pytest.mark.parametrize("stream", [False, True])
def test_native_responses_metadata_and_truncation_are_never_rewritten(stream):
    native = {"id": "resp_native", "object": "response", "status": "completed", "model": "m", "output": [],
              "truncation": "auto", "store": True, "background": True,
              "previous_response_id": "resp_prior", "temperature": 0.7, "top_p": 0.9,
              "service_tier": "upstream-tier", "text": {"format": {"type": "text"}}}
    assert convert_response(native, "responses", "responses") == native
    calls = []
    wire = (event("response.created", {"type": "response.created", "sequence_number": 0, "response": {**native, "status": "in_progress"}})
            + event("response.completed", {"type": "response.completed", "sequence_number": 1, "response": native}))
    raw = wire if stream else json.dumps(native).encode()

    def upstream(req, *, timeout):
        calls.append(json.loads(req.data))
        return io.BytesIO(raw)

    body = {"model": "m", "input": "synthetic", "stream": stream, "truncation": "auto", "store": True}
    config = GatewayConfig("responses", "https://example.invalid/v1", "synthetic", "local", client_profile="codex", default_max_output_tokens=32)
    with listener(config, upstream) as url, request(url, "responses", json.dumps(body).encode()) as response:
        result = response.read()
    assert calls == [body]
    assert (result == raw) if stream else (json.loads(result) == native)


@pytest.mark.parametrize("source", ["chat", "messages"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("text", [None, {}, {"format": {"type": "text"}}, {
    "format": {"type": "json_schema", "name": "answer", "strict": True,
               "schema": {"type": "object", "properties": {"answer": {"type": "string"}},
                          "required": ["answer"], "additionalProperties": False}},
}])
def test_gateway_knows_unstructured_mode_but_preserves_explicit_json_schema(source, stream, text):
    calls = []
    structured = bool(text and text.get("format", {}).get("type") == "json_schema")
    answer = '{"answer":"ok"}' if structured else TEXT
    result = chat_result()
    result["choices"][0]["message"]["content"] = answer
    encoder = StreamEncoder(source)
    deltas = [Delta("start", model="fixture-model", input_tokens=19, output_tokens=0),
              Delta("text", text=answer), Delta("finish", reason="stop", input_tokens=19, output_tokens=7)]
    raw = (b"".join(event(item.event, item.data) for delta in deltas for item in encoder.feed(delta))
           if stream else json.dumps(convert_response(result, "chat", source)).encode())

    def upstream(req, *, timeout):
        calls.append(json.loads(req.data))
        return io.BytesIO(raw)

    body = {"model": "fixture-model", "input": "synthetic", "max_output_tokens": 20, "stream": stream}
    if text is not None:
        body["text"] = copy.deepcopy(text)
    before = copy.deepcopy(body)
    config = GatewayConfig(source, "https://example.invalid/v1", "synthetic", "local")
    with listener(config, upstream) as url, request(url, "responses", json.dumps(body).encode()) as response:
        received = response.read()
    actual = calls[0].get("response_format") if source == "chat" else calls[0].get("output_config", {}).get("format")
    assert (actual is not None and actual["type"] == "json_schema") if structured else actual is None
    for snapshot in snapshots(received) if stream else [json.loads(received)]:
        assert snapshot["text"] == (text if structured else {"format": {"type": "text"}})
        assert not {"temperature", "top_p", "service_tier"} & snapshot.keys()
    assert body == before
