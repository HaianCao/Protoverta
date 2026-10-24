import copy
import io
import json
from itertools import permutations

import pytest
from test_gateway_capabilities import listener, post

from protoverta import ConversionError, convert_request
from protoverta.server import GatewayConfig
from protoverta.sse import SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta


def body(protocol):
    return {"model": "m", "max_output_tokens": 32, "input": "Hi"} if protocol == "responses" else {"model": "m", "max_tokens": 32, "messages": [{"role": "user", "content": "Hi"}]}


@pytest.mark.parametrize("source,target", tuple(permutations(("chat", "messages", "responses"), 2)))
@pytest.mark.parametrize("value", [0, 0.25, 1])
def test_top_p_survives_all_routes_without_mutation(source, target, value):
    original = dict(body(source), top_p=value)
    before = copy.deepcopy(original)
    converted = convert_request(original, source, target)
    assert converted["top_p"] == value
    assert original == before
    assert convert_request(converted, target, source)["top_p"] == value


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("value", [True, False, -0.1, 1.1, "0.5", {}, float("nan"), float("inf")])
def test_invalid_top_p_is_rejected(protocol, value):
    target = "messages" if protocol == "chat" else "chat"
    with pytest.raises(ConversionError, match="top_p"):
        convert_request(dict(body(protocol), top_p=value), protocol, target)


@pytest.mark.parametrize("source,target", tuple(permutations(("chat", "messages", "responses"), 2)))
def test_absent_and_null_top_p_do_not_invent_a_setting(source, target):
    assert "top_p" not in convert_request(body(source), source, target)
    assert "top_p" not in convert_request(dict(body(source), top_p=None), source, target)


@pytest.mark.parametrize("stream", [False, True])
def test_gateway_keeps_sampling_in_outbound_and_generated_responses_snapshots(stream):
    def upstream(request, *, timeout):
        outbound = json.loads(request.data)
        assert outbound["top_p"] == 0.25 and outbound["temperature"] == 0.5
        if stream:
            encoder = StreamEncoder("chat")
            deltas = [Delta("start", model="m"), Delta("text", text="Hi"), Delta("finish", reason="stop", input_tokens=1, output_tokens=1)]
            return io.BytesIO(b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta)))
        return io.BytesIO(b'{"model":"m","choices":[{"message":{"role":"assistant","content":"Hi"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}')

    config = GatewayConfig("chat", "https://example.test/v1", "key", "local")
    with listener(config, upstream) as url, post(url, dict(body("responses"), stream=stream, top_p=0.25, temperature=0.5)) as response:
        wire = response.read()
    snapshots = [json.loads(event.data)["response"] for event in SSEParser().feed(wire) if "response" in json.loads(event.data)] if stream else [json.loads(wire)]
    assert snapshots and all(item["top_p"] == 0.25 and item["temperature"] == 0.5 for item in snapshots)
