import io
import itertools
import json

import pytest
from test_gateway_capabilities import listener, post

from protoverta import StreamDecoder, convert_request, convert_response
from protoverta.server import GatewayConfig
from protoverta.sse import SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta


@pytest.mark.parametrize("source,target", list(itertools.permutations(("chat", "messages", "responses"), 2)))
@pytest.mark.parametrize("stream", [False, True])
def test_user_image_reaches_upstream_and_text_answer_completes(source, target, stream):
    captured = []
    url = "https://images.example.test/synthetic.png"

    def upstream(request, *, timeout):
        body = json.loads(request.data)
        captured.append(body)
        blocks = body["input" if target == "responses" else "messages"][0]["content"]
        assert len(blocks) == 3
        assert blocks[0]["text"] == "before" and blocks[2]["text"] == "after"
        image = blocks[1]
        assert (image["source"]["url"] if target == "messages" else image["image_url"]["url"] if target == "chat" else image["image_url"]) == url
        if stream:
            encoder = StreamEncoder(target)
            deltas = [Delta("start", model="m", input_tokens=8, output_tokens=0), Delta("text", text="image accepted"), Delta("finish", reason="stop", input_tokens=8, output_tokens=2)]
            return io.BytesIO(b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta)))
        result = {"model": "m", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "image accepted"}}], "usage": {"prompt_tokens": 8, "completion_tokens": 2}}
        return io.BytesIO(json.dumps(convert_response(result, "chat", target)).encode())

    config = GatewayConfig(target, "https://example.test/v1", "key", "local", messages_usage_policy="deferred" if source == "messages" else "strict")
    template = {"model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "before"}, {"type": "image_url", "image_url": {"url": url}}, {"type": "text", "text": "after"}]}], "max_tokens": 32, "stream": stream}
    with listener(config, upstream) as origin:
        endpoint = {"chat": "chat/completions", "messages": "messages", "responses": "responses"}[source]
        with post(origin.removesuffix("responses") + endpoint, convert_request(template, "chat", source)) as response:
            wire = response.read()
    assert len(captured) == 1
    if stream:
        decoder = StreamDecoder(source)
        events = SSEParser().feed(wire)
        text = "".join(delta.text for event in events for delta in decoder.feed(event) if delta.kind == "text")
        decoder.finish()
    else:
        result = convert_response(json.loads(wire), source, "chat")
        text = result["choices"][0]["message"]["content"]
    assert text == "image accepted"
