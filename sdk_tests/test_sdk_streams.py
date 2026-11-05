"""Independent official SDK consumers. No vendor calls, keys or SDK fallbacks."""

import io
import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import anthropic
import openai
import pytest
from wire_cases import CALLS, MODEL, TEXT, event, fragments, source_wire

from protoverta import convert_request
from protoverta import server as gateway
from protoverta.server import GatewayConfig, make_handler
from protoverta.sse import SSEParser, encode_sse

PROTOCOLS = ("chat", "messages", "responses")
ROUTES = [(source, target) for source in PROTOCOLS for target in PROTOCOLS if source != target]
# Messages does not report a reasoning-token breakdown. A strict Responses SDK
# requires that measurement; do not manufacture zero to satisfy its schema.
SDK_SCHEMA_ROUTES = [route for route in ROUTES if route != ("messages", "responses")]


class Fragmented(io.BytesIO):
    def __init__(self, raw):
        super().__init__()
        self.chunks = iter(fragments(raw, 72))

    def read1(self, size):
        return next(self.chunks, b"")


@contextmanager
def listener(source, *, tools=True, length=False, wire=None, **options):
    raw = source_wire(source, tools=tools, length=length) if wire is None else wire
    config = GatewayConfig(source, "https://example.invalid/v1", "synthetic-upstream", "local", messages_usage_policy="deferred", **options)
    calls = []

    def upstream(request, *, timeout):
        # All HTTP requests made by the SDK terminate at the loopback handler.
        # The provider transport is this callback and cannot access the network.
        calls.append(json.loads(request.data))
        return Fragmented(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=upstream))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@contextmanager
def client_for(target, origin):
    library = anthropic if target == "messages" else openai
    constructor = library.Anthropic if target == "messages" else library.OpenAI
    base = origin.removesuffix("/v1") if target == "messages" else origin
    with constructor(
        api_key="local",
        base_url=base,
        max_retries=0,
        timeout=5,
        default_headers={"Authorization": "Bearer local"},
        http_client=library.DefaultHttpxClient(trust_env=False),
        _strict_response_validation=True,
    ) as client:
        yield client


def response_policy():
    # Positive SDK cases supply the request policy instead of relying on
    # fabricated response defaults. Fixture tools match the emitted calls.
    return {
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [
            {"type": "function", "name": name, "parameters": {"type": "object", "properties": {}}, "strict": False}
            for name in sorted({name for _, name, _ in CALLS})
        ],
    }


def portable_tool_policy(target):
    request = convert_request({"model": MODEL, "input": "synthetic", "max_output_tokens": 32, **response_policy()}, "responses", target)
    return {key: request[key] for key in ("tools", "tool_choice", "parallel_tool_calls") if key in request}


def probability_wire(source):
    """Handwritten token observations layered onto the independent wire fixture."""
    output, probabilities = [], []
    for frame in SSEParser().feed(source_wire(source, tools=False)):
        if frame.data == "[DONE]":
            output.append(encode_sse(frame.event, frame.data))
            continue
        body = json.loads(frame.data)
        if source == "chat":
            for choice in body.get("choices", []):
                text = choice.get("delta", {}).get("content")
                if text:
                    value = {"token": text, "bytes": list(text.encode()), "logprob": -0.25, "top_logprobs": [{"token": "x", "bytes": [120], "logprob": -1.5}]}
                    probabilities.append(value)
                    choice["logprobs"] = {"content": [value], "refusal": None}
        else:
            kind = body["type"]
            if kind == "response.output_text.delta" and body["delta"]:
                value = {
                    "token": body["delta"],
                    "bytes": list(body["delta"].encode()),
                    "logprob": -0.25,
                    "top_logprobs": [{"token": "x", "bytes": [120], "logprob": -1.5}],
                }
                probabilities.append(value)
                body["logprobs"] = [value]
            elif kind == "response.output_text.done":
                body["logprobs"] = probabilities.copy()
            elif kind == "response.content_part.done":
                body["part"]["logprobs"] = probabilities.copy()
            elif kind == "response.output_item.done":
                body["item"]["content"][0]["logprobs"] = probabilities.copy()
            elif kind == "response.completed":
                body["response"]["output"][0]["content"][0]["logprobs"] = probabilities.copy()
        output.append(encode_sse(frame.event, json.dumps(body)))
    return b"".join(output), probabilities


def verify_probability_sdk(source, target):
    raw, expected = probability_wire(source)
    with listener(source, wire=raw) as (origin, calls), client_for(target, origin) as client:
        if target == "responses":
            with client.responses.stream(
                model=MODEL, input="synthetic", max_output_tokens=32, include=["message.output_text.logprobs"], top_logprobs=2, **response_policy()
            ) as stream:
                list(stream)
                observed = stream.get_final_response().output[0].content[0].logprobs
        else:
            chunks = list(
                client.chat.completions.create(
                    model=MODEL,
                    messages=[{"role": "user", "content": "synthetic"}],
                    stream=True,
                    stream_options={"include_usage": True},
                    logprobs=True,
                    top_logprobs=2,
                )
            )
            observed = [token for chunk in chunks for choice in chunk.choices for token in (choice.logprobs.content if choice.logprobs else [])]
    assert [(p.token, p.bytes, p.logprob, [(c.token, c.bytes, c.logprob) for c in p.top_logprobs]) for p in observed] == [
        (p["token"], p["bytes"], p["logprob"], [(c["token"], c["bytes"], c["logprob"]) for c in p["top_logprobs"]]) for p in expected
    ]
    assert len(calls) == 1 and calls[0]["top_logprobs"] == 2
    if source == "chat":
        completed = {
            "model": MODEL,
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": TEXT}, "finish_reason": "stop", "logprobs": {"content": expected, "refusal": None}}
            ],
            "usage": {
                "prompt_tokens": 19,
                "completion_tokens": 7,
                "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
    else:
        completed = next(json.loads(e.data)["response"] for e in SSEParser().feed(raw) if e.event == "response.completed")
    with listener(source, wire=json.dumps(completed).encode()) as (origin, calls), client_for(target, origin) as client:
        if target == "responses":
            result = client.responses.create(
                model=MODEL, input="synthetic", max_output_tokens=32, include=["message.output_text.logprobs"], top_logprobs=2, **response_policy()
            )
            observed = result.output[0].content[0].logprobs
        else:
            result = client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": "synthetic"}], logprobs=True, top_logprobs=2)
            observed = result.choices[0].logprobs.content
    assert [(p.token, p.bytes, p.logprob) for p in observed] == [(p["token"], p["bytes"], p["logprob"]) for p in expected]
    assert len(calls) == 1


def consume(client, target):
    if target == "messages":
        with client.messages.stream(model=MODEL, max_tokens=32, messages=[{"role": "user", "content": "synthetic"}], **portable_tool_policy(target)) as stream:
            events = list(stream)
            result = stream.get_final_message()
        return {
            "text": "".join(block.text for block in result.content if block.type == "text"),
            "calls": [(block.id, block.name, block.input) for block in result.content if block.type == "tool_use"],
            "reason": result.stop_reason,
            "usage": (result.usage.input_tokens, result.usage.output_tokens),
            "events": events,
        }
    if target == "responses":
        with client.responses.stream(model=MODEL, input="synthetic", max_output_tokens=32, **response_policy()) as stream:
            try:
                events = list(stream)
            except openai.APIResponseValidationError as exc:
                cause = exc.__cause__
                kinds = {"ResponseCompletedEvent", "ResponseIncompleteEvent", "ResponseCreatedEvent", "ResponseFunctionCallArgumentsDoneEvent"}
                details = [
                    error for error in cause.errors(include_input=False, include_url=False) if error["loc"][0] in kinds and error["type"] != "literal_error"
                ]
                raise AssertionError(json.dumps(details, default=str)) from None
            if events[-1].type == "response.incomplete":
                # The pinned SDK accumulator only exposes completed responses
                # through this helper. Do NOT fake response.completed to appease
                # it: inspect the schema-validated incomplete terminal event.
                with pytest.raises(RuntimeError, match="response.completed"):
                    stream.get_final_response()
                result = events[-1].response
            else:
                result = stream.get_final_response()
        return {
            "text": result.output_text,
            "calls": [(item.call_id, item.name, json.loads(item.arguments)) for item in result.output if item.type == "function_call"],
            "reason": result.status,
            "usage": (result.usage.input_tokens, result.usage.output_tokens),
            "events": events,
        }
    content, tools, reason, usage = [], {}, None, None
    events = []
    with client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "synthetic"}],
        max_completion_tokens=32,
        stream=True,
        stream_options={"include_usage": True},
        **portable_tool_policy(target),
    ) as stream:
        for chunk in stream:
            events.append(chunk)
            if chunk.usage is not None:
                usage = chunk.usage.prompt_tokens, chunk.usage.completion_tokens
            for choice in chunk.choices:
                if choice.delta.content:
                    content.append(choice.delta.content)
                for tool in choice.delta.tool_calls or []:
                    value = tools.setdefault(tool.index, {"id": "", "name": "", "arguments": ""})
                    if tool.id:
                        value["id"] = tool.id
                    if tool.function is not None:
                        value["name"] += tool.function.name or ""
                        value["arguments"] += tool.function.arguments or ""
                if choice.finish_reason is not None:
                    reason = choice.finish_reason
    return {
        "text": "".join(content),
        "calls": [(value["id"], value["name"], json.loads(value["arguments"])) for value in tools.values()],
        "reason": reason,
        "usage": usage,
        "events": events,
    }


@pytest.mark.parametrize(("source", "target"), SDK_SCHEMA_ROUTES)
def test_official_sdk_accepts_post_payload_keepalives_before_verified_eof(monkeypatch, source, target):
    original_read = Fragmented.read1
    original_writer = gateway.StreamHeartbeatWriter
    keepalives = []
    keepalive_sent = threading.Event()

    def delayed_eof(self, size):
        raw = original_read(self, size)
        if not raw:
            # Hold EOF until one keepalive is on the wire. A fixed sleep raced
            # the 15.6 ms Windows monotonic clock on Python < 3.13. The bound
            # keeps a missing heartbeat a failure; no provider/network I/O.
            keepalive_sent.wait(2.0)
        return raw

    def writer(send, *args, **kwargs):
        def record(raw, timeout):
            keepalive = raw == b": protoverta keepalive\n\n" or raw == b'event: ping\ndata: {"type":"ping"}\n\n'
            send(raw, timeout)
            if keepalive:
                keepalives.append(True)
                keepalive_sent.set()

        return original_writer(record, *args, **kwargs)

    monkeypatch.setattr(Fragmented, "read1", delayed_eof)
    monkeypatch.setattr(gateway, "StreamHeartbeatWriter", writer)
    monkeypatch.setattr(gateway, "STREAM_HEARTBEAT_INTERVAL", 0.02)
    with listener(source) as (origin, calls), client_for(target, origin) as client:
        result = consume(client, target)
    assert keepalives
    assert result["text"] == TEXT and result["calls"] == CALLS
    assert result["usage"] == (19, 7)
    assert len(calls) == 1


@pytest.mark.parametrize(("source", "target"), SDK_SCHEMA_ROUTES)
def test_sdk_accumulates_text_and_tools_without_losing_opening_arguments(source, target):
    with listener(source) as (origin, calls), client_for(target, origin) as client:
        result = consume(client, target)
    assert result["text"] == TEXT and result["calls"] == CALLS
    assert result["usage"] == (19, 7)
    assert result["reason"] == {"chat": "tool_calls", "messages": "tool_use", "responses": "completed"}[target]
    assert len(calls) == 1
    if (source, target) in {("chat", "responses"), ("responses", "chat")}:
        verify_probability_sdk(source, target)
    if target == "responses":
        for item in result["events"]:
            if item.type.startswith(("response.content_part.", "response.output_text.")):
                assert item.content_index == 0


@pytest.mark.parametrize(("source", "target"), SDK_SCHEMA_ROUTES)
def test_sdk_sees_max_tokens_as_incomplete_not_full_success(source, target):
    with listener(source, tools=False, length=True) as (origin, calls), client_for(target, origin) as client:
        result = consume(client, target)
    assert result["text"] == TEXT and result["calls"] == []
    assert result["usage"] == (19, 7)
    assert result["reason"] == {"chat": "length", "messages": "max_tokens", "responses": "incomplete"}[target]
    assert len(calls) == 1


@pytest.mark.parametrize("length", [False, True])
def test_strict_responses_sdk_rejects_unknown_messages_reasoning_usage(length):
    with listener("messages", tools=not length, length=length) as (origin, calls), client_for("responses", origin) as client:
        with (
            client.responses.stream(model=MODEL, input="synthetic", max_output_tokens=32, **response_policy()) as stream,
            pytest.raises(openai.APIResponseValidationError) as caught,
        ):
            list(stream)
        terminal = "ResponseIncompleteEvent" if length else "ResponseCompletedEvent"
        errors = caught.value.__cause__.errors(include_input=False, include_url=False)
        relevant = [error for error in errors if error["loc"][0] == terminal]
        assert [(error["type"], error["loc"][1:]) for error in relevant] == [("missing", ("response", "usage", "output_tokens_details"))]
    assert len(calls) == 1


@pytest.mark.parametrize("source", ["chat", "messages"])
def test_strict_responses_sdk_accepts_gateway_owned_default_tool_policy(source):
    with (
        listener(source, tools=False) as (origin, calls),
        client_for("responses", origin) as client,
        client.responses.stream(model=MODEL, input="synthetic", max_output_tokens=32) as stream,
    ):
        # This tests the created policy snapshot; unknown Messages reasoning
        # usage is still rejected separately at the terminal event above.
        created = next(event for event in stream if event.type == "response.created").response
        assert created.tools == [] and created.tool_choice == "none" and created.parallel_tool_calls is False
    assert len(calls) == 1


@pytest.mark.parametrize(("source", "target"), ROUTES)
def test_sdk_does_not_receive_success_on_truncated_upstream(source, target):
    # Cut the final frame itself. The gateway must reject EOF or emit an error,
    # not repair a missing upstream terminal marker into successful completion.
    raw = source_wire(source, tools=False)
    with (
        listener(source, wire=raw[:-10]) as (origin, calls),
        client_for(target, origin) as client,
        pytest.raises((openai.APIError, anthropic.APIError, RuntimeError)),
    ):
        consume(client, target)
    assert len(calls) == 1  # SDK retries disabled; never replay generation.


def test_sdk_reasoning_prefix_does_not_shift_per_item_content_index():
    prefix = event(
        "message",
        {
            "id": "chatcmpl_fixture",
            "created": 1,
            "object": "chat.completion.chunk",
            "model": MODEL,
            "choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": "public fixture reasoning"}, "finish_reason": None}],
        },
    )
    raw = prefix + source_wire("chat", tools=False).replace(b'"reasoning_tokens": 0', b'"reasoning_tokens": 2')
    with listener("chat", wire=raw, public_reasoning=True) as (origin, calls), client_for("responses", origin) as client:
        result = consume(client, "responses")
    assert result["text"] == TEXT and len(calls) == 1
    text_events = [item for item in result["events"] if item.type == "response.output_text.delta"]
    assert text_events and all(item.output_index == 1 and item.content_index == 0 for item in text_events)
    reasoning = [item.item for item in result["events"] if item.type == "response.output_item.done" and item.item.type == "reasoning"]
    assert len(reasoning) == 1 and reasoning[0].content[0].text == "public fixture reasoning"
