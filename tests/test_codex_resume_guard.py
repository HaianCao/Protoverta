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
def test_closed_resume_is_rejected_before_harness_execution_without_rewriting_other_tools(
    monkeypatch, source, streaming, guarded
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    fixture = importlib.import_module("smoke_parallel_agents")
    config = GatewayConfig(
        source,
        "https://example.invalid/v1",
        "fake",
        "local",
        namespace_tools="flatten",
        codex_model_safe_resume=guarded,
    )

    def upstream(request, *, timeout):
        body = json.loads(request.data)
        name = (
            body["tools"][0]["function"]["name"]
            if source == Protocol.CHAT
            else body["tools"][0]["name"]
            if source == Protocol.MESSAGES
            else "resume_agent"
        )
        tool = {"name": name, **({"namespace": "multi_agent_v1"} if source == Protocol.RESPONSES else {})}
        if streaming:
            return fixture.calls_wire(source, "main", [(tool, {"id": "synthetic-child"}, "resume-control")])
        item = {
            "type": "function_call",
            "name": name,
            "namespace": "multi_agent_v1",
            "arguments": '{"id":"synthetic-child"}',
            "call_id": "resume-control",
        }
        if source == Protocol.RESPONSES:
            value = {"model": "main", "output": [item]}
        elif source == Protocol.CHAT:
            value = {
                "model": "main",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "resume-control",
                                    "type": "function",
                                    "function": {"name": name, "arguments": item["arguments"]},
                                }
                            ],
                        },
                    }
                ],
            }
        else:
            value = {
                "model": "main",
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "resume-control", "name": name, "input": {"id": "synthetic-child"}}
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        return io.BytesIO(json.dumps(value).encode())

    body = {
        "model": "main",
        "input": "synthetic",
        "max_output_tokens": 32,
        "stream": streaming,
        "tools": [
            {
                "type": "namespace",
                "name": "multi_agent_v1",
                "tools": [{"type": "function", "name": "resume_agent", "parameters": {"type": "object"}}],
            }
        ],
    }
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


@pytest.mark.parametrize(
    "namespace,name",
    [("unrelated", "resume_agent"), ("multi_agent_v1", "send_input"), ("multi_agent_v1", "spawn_agent")],
)
def test_guard_leaves_unrelated_functions_and_safe_continuation_unchanged(namespace, name):
    guard_codex_closed_resume({"output": [{"type": "function_call", "namespace": namespace, "name": name}]})


def _responses_text_wire(trailing_done):
    def sse(data):
        return f"event: {data['type']}\ndata: {json.dumps(data)}\n\n".encode()

    base = {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "status": "in_progress",
        "model": "main",
        "output": [],
    }
    part = {"type": "output_text", "text": "", "annotations": []}
    item = {"id": "msg_1", "type": "message", "role": "assistant", "status": "in_progress", "content": []}
    final_item = dict(item, status="completed", content=[dict(part, text="hi")])
    position = {"item_id": "msg_1", "output_index": 0, "content_index": 0}
    events = [
        {"type": "response.created", "response": base},
        {"type": "response.output_item.added", "output_index": 0, "item": item},
        {"type": "response.content_part.added", **position, "part": part},
        {"type": "response.output_text.delta", **position, "delta": "hi", "logprobs": []},
        {"type": "response.output_text.done", **position, "text": "hi", "logprobs": []},
        {"type": "response.content_part.done", **position, "part": dict(part, text="hi")},
        {"type": "response.output_item.done", "output_index": 0, "item": final_item},
        {
            "type": "response.completed",
            "response": dict(
                base,
                status="completed",
                output=[final_item],
                usage={"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
            ),
        },
    ]
    wire = b"".join(sse({**event, "sequence_number": index}) for index, event in enumerate(events))
    return wire + (b"data: [DONE]\n\n" if trailing_done else b"")


@pytest.mark.parametrize("trailing_done", [False, True])
def test_guarded_native_stream_accepts_trailing_done_marker(trailing_done):
    config = GatewayConfig(
        Protocol.RESPONSES, "https://example.invalid/v1", "fake", "local", codex_model_safe_resume=True
    )
    upstream = lambda request, *, timeout: io.BytesIO(_responses_text_wire(trailing_done))
    with (
        listener(config, upstream) as url,
        post(url, {"model": "main", "input": "synthetic", "stream": True}) as response,
    ):
        assert response.status == 200
        raw = response.read()
    events = SSEParser().feed(raw)
    completed = [event for event in events if event.event == "response.completed"]
    assert len(completed) == 1 and b'"type": "error"' not in raw
    assert (events[-1].data == "[DONE]") == trailing_done


def test_native_guard_value_errors_stay_stream_errors():
    from protoverta.stream_pipe import StreamValidator
    from protoverta.streaming import StreamError

    def faulty(event):
        raise ValueError("not json")

    with pytest.raises(StreamError, match="guard rejected"):
        StreamValidator(Protocol.RESPONSES, event_guard=faulty).feed(_responses_text_wire(False))


def test_guard_checks_native_completed_snapshot_too():
    with pytest.raises(UnsupportedFeatureError, match="Closed child resume"):
        guard_codex_closed_resume(
            {"response": {"output": [{"type": "function_call", "namespace": "multi_agent_v1", "name": "resume_agent"}]}}
        )
