import importlib
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post

from protoverta.core import Protocol
from protoverta.errors import UnsupportedFeatureError
from protoverta.harness_controls import guard_codex_closed_resume
from protoverta.server import GatewayConfig
from protoverta.sse import SSEParser


@pytest.mark.parametrize("source", list(Protocol))
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("guarded", [False, True])
def test_closed_resume_is_rejected_before_harness_execution_without_rewriting_other_tools(monkeypatch, source, streaming, guarded):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    fixture = importlib.import_module("smoke_parallel_agents")
    config = GatewayConfig(source, "https://example.invalid/v1", "fake", "local", namespace_tools="flatten", codex_model_safe_resume=guarded)
    def upstream(request, *, timeout):
        body = json.loads(request.data)
        name = body["tools"][0]["function"]["name"] if source == Protocol.CHAT else body["tools"][0]["name"] if source == Protocol.MESSAGES else "resume_agent"
        tool = {"name": name, **({"namespace": "multi_agent_v1"} if source == Protocol.RESPONSES else {})}
        if streaming:
            return fixture.calls_wire(source, "main", [(tool, {"id": "synthetic-child"}, "resume-control")])
        item = {"type": "function_call", "name": name, "namespace": "multi_agent_v1", "arguments": '{"id":"synthetic-child"}', "call_id": "resume-control"}
        if source == Protocol.RESPONSES:
            value = {"model": "main", "output": [item]}
        elif source == Protocol.CHAT:
            value = {"model": "main", "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "resume-control", "type": "function", "function": {"name": name, "arguments": item["arguments"]}}]}}]}
        else:
            value = {"model": "main", "role": "assistant", "content": [{"type": "tool_use", "id": "resume-control", "name": name, "input": {"id": "synthetic-child"}}], "stop_reason": "tool_use", "usage": {"input_tokens": 1, "output_tokens": 1}}
        return io.BytesIO(json.dumps(value).encode())
    body = {"model": "main", "input": "synthetic", "max_output_tokens": 32, "stream": streaming,
            "tools": [{"type": "namespace", "name": "multi_agent_v1", "tools": [{"type": "function", "name": "resume_agent", "parameters": {"type": "object"}}]}]}
    with listener(config, upstream) as url:
        try:
            with post(url, body) as response:
                raw = response.read()
        except HTTPError as error:
            assert guarded and error.code == 422
            raw = error.read()
            error.close()
        else:
            if guarded:
                assert streaming and b"Closed child resume" in raw
                events = [json.loads(event.data) for event in SSEParser().feed(raw)]
                assert not any(event.get("type") == "response.completed" for event in events)
                assert not any(event.get("item", {}).get("name") == "resume_agent" for event in events)
            else:
                assert b"resume_agent" in raw
        if guarded:
            assert b"send_input" in raw and b"unsupported_feature" in raw


@pytest.mark.parametrize("namespace,name", [("unrelated", "resume_agent"), ("multi_agent_v1", "send_input"), ("multi_agent_v1", "spawn_agent")])
def test_guard_leaves_unrelated_functions_and_safe_continuation_unchanged(namespace, name):
    guard_codex_closed_resume({"output": [{"type": "function_call", "namespace": namespace, "name": name}]})


def test_guard_checks_native_completed_snapshot_too():
    with pytest.raises(UnsupportedFeatureError, match="Closed child resume"):
        guard_codex_closed_resume({"response": {"output": [{"type": "function_call", "namespace": "multi_agent_v1", "name": "resume_agent"}]}})
