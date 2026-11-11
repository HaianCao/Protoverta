import io
import json

import pytest
from test_gateway_capabilities import listener, post
from test_tool_search import body

from protoverta.resources import StreamLimitError, StreamLimits
from protoverta.server import GatewayConfig
from protoverta.sse import SSEEvent, SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.stream_pipe import StreamTranscoder, StreamValidator
from protoverta.streaming import Delta, StreamError
from protoverta.tool_search import ClientToolSearchMap


def wire(protocol, arguments='{"goal":"lookup"}'):
    encoder = StreamEncoder(protocol)
    deltas = [
        Delta("start", model="m", input_tokens=1, output_tokens=0),
        Delta("text", text="Looking up tools"),
        Delta("tool_start", index=0, call_id="search_1", name="pv_client_tool_search"),
        Delta("tool_delta", index=0, text=arguments[:5]),
        Delta("tool_delta", index=0, text=arguments[5:]),
        Delta("tool_end", index=0),
        Delta("finish", reason="tool_calls", input_tokens=1, output_tokens=5),
    ]
    return b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta))


@pytest.mark.parametrize("source", ["chat", "messages"])
@pytest.mark.parametrize("chunk_size", [1, 7, 100000])
def test_search_stream_uses_only_documented_item_lifecycle(source, chunk_size):
    request = body()
    request["stream"] = True
    mapper = ClientToolSearchMap()
    mapper.prepare(request)
    pipe = StreamTranscoder(source, "responses", delta_transform=mapper.stream_delta)
    upstream = wire(source)
    pieces = [pipe.feed(upstream[i : i + chunk_size]) for i in range(0, len(upstream), chunk_size)]
    before_eof = b"".join(pieces)
    assert b"response.completed" not in before_eof
    output = before_eof + pipe.finish()
    native = StreamValidator("responses")
    native.feed(output)
    native.finish()
    events = [json.loads(event.data) for event in SSEParser().feed(output)]
    assert not any("function_call_arguments" in event["type"] for event in events)
    calls = [event["item"] for event in events if event.get("item", {}).get("type") == "tool_search_call"]
    assert len(calls) == 2  # added + done, no invented argument-delta wire
    assert all(
        call["arguments"] == {"goal": "lookup"} and call["call_id"] == "search_1" and call["execution"] == "client"
        for call in calls
    )
    assert events[-1]["response"]["output"][-1] == calls[-1]
    assert events[-1]["type"] == "response.completed"


@pytest.mark.parametrize("arguments", ["[]", "null", '"text"', "1"])
def test_non_object_search_arguments_cannot_complete(arguments):
    mapper = ClientToolSearchMap()
    mapper.prepare(body())
    pipe = StreamTranscoder("chat", "responses", delta_transform=mapper.stream_delta)
    with pytest.raises(StreamError, match="object"):
        pipe.feed(wire("chat", arguments))


def test_search_arguments_remain_resource_bounded():
    mapper = ClientToolSearchMap()
    mapper.prepare(body())
    pipe = StreamTranscoder("chat", "responses", delta_transform=mapper.stream_delta, max_tool_argument_bytes=4)
    with pytest.raises(StreamError, match="byte limit"):
        pipe.feed(wire("chat"))


def test_native_completed_search_arguments_count_toward_byte_budget():
    limits = StreamLimits(max_argument_bytes=4)
    event = SSEEvent(
        "response.output_item.added",
        json.dumps(
            {
                "type": "response.output_item.added",
                "item": {"type": "tool_search_call", "arguments": {"goal": "lookup"}},
            }
        ),
    )
    with pytest.raises(StreamLimitError):
        limits.observe(event)


@pytest.mark.parametrize("protocol", ["chat", "messages"])
def test_gateway_search_sse_round_trip_and_replay(protocol):
    captured = []

    def upstream(request, *, timeout):
        captured.append(json.loads(request.data))
        return io.BytesIO(wire(protocol))

    request = body()
    request["stream"] = True
    config = GatewayConfig(protocol, "https://example.test/v1", "key", "local", client_tool_search="function")
    with listener(config, upstream) as url, post(url, request) as response:
        events = [json.loads(event.data) for event in SSEParser().feed(response.read())]
    call = events[-1]["response"]["output"][-1]
    assert call["type"] == "tool_search_call"
    request["input"] = [
        call,
        {
            "type": "tool_search_output",
            "status": "completed",
            "execution": "client",
            "call_id": call["call_id"],
            "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
        },
    ]
    prepared = ClientToolSearchMap().prepare(request)
    assert prepared["tools"][0]["name"] == "lookup"
    assert len(captured) == 1
