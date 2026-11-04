import copy
import io
import json
from urllib.error import HTTPError

import pytest
from test_effective_tool_policy import reply
from test_json_wire_boundaries import request
from test_response_snapshot_consistency import listener

from protoverta import (
    ConversionError,
    StreamEncoder,
    StreamTranscoder,
    convert_request,
    convert_response,
)
from protoverta.server import GatewayConfig
from protoverta.sse import SSEParser, encode_sse
from protoverta.streaming import Delta, StreamError


def token(text, raw=None):
    return {"token": text, "bytes": list(text.encode()) if raw is None else raw, "logprob": -0.25,
            "top_logprobs": [{"token": "x", "bytes": [120], "logprob": -1.5}]}


def response(source, text="hello", probabilities=None):
    probabilities = probabilities if probabilities is not None else [token(text)]
    body = json.loads(reply("chat", text=text))
    body["choices"][0]["logprobs"] = {"content": probabilities, "refusal": None}
    return body if source == "chat" else convert_response(body, "chat", "responses", text_logprobs=True)


@pytest.mark.parametrize("source,target", [("chat", "responses"), ("responses", "chat")])
@pytest.mark.parametrize("text", ["hello", "Tiếng Việt 🙂", "a\n\tb"])
def test_completed_text_probability_round_trip_is_lossless(source, target, text):
    original = response(source, text)
    before = copy.deepcopy(original)
    result = convert_response(original, source, target, text_logprobs=True)
    back = convert_response(result, target, source, text_logprobs=True)
    if source == "chat":
        assert back["choices"][0]["logprobs"] == original["choices"][0]["logprobs"]
    else:
        assert back["output"][-1]["content"] == original["output"][-1]["content"]
    assert original == before


@pytest.mark.parametrize("source", ["chat", "responses"])
def test_opt_in_does_not_weaken_default_rejection_or_messages_boundary(source):
    target = "responses" if source == "chat" else "chat"
    with pytest.raises(ConversionError):
        convert_response(response(source), source, target)
    with pytest.raises(ConversionError):
        convert_response(response(source), source, "messages", text_logprobs=True)


@pytest.mark.parametrize("mutation", ["bytes_null", "bytes_bool", "bytes_range", "unknown", "positive", "nan", "huge_integer", "top_unknown", "top_missing", "misaligned"])
def test_malformed_or_unalignable_probabilities_never_get_repaired(mutation):
    value = token("hello")
    if mutation == "bytes_null":
        value["bytes"] = None
    elif mutation == "bytes_bool":
        value["bytes"] = [True]
    elif mutation == "bytes_range":
        value["bytes"] = [256]
    elif mutation == "unknown":
        value["vendor"] = "secret"
    elif mutation == "positive":
        value["logprob"] = 0.1
    elif mutation == "nan":
        value["logprob"] = float("nan")
    elif mutation == "huge_integer":
        value["logprob"] = -(10 ** 1000)
    elif mutation == "top_unknown":
        value["top_logprobs"][0]["vendor"] = True
    elif mutation == "top_missing":
        value.pop("top_logprobs")
    else:
        value["bytes"] = [120]
    with pytest.raises(ConversionError):
        convert_response(response("chat", probabilities=[value]), "chat", "responses", text_logprobs=True)


def stream(source, *, split_unicode=False):
    encoder = StreamEncoder(source, text_logprobs=True)
    text = "🙂" if split_unicode else "hello"
    probabilities = [token("�", [240, 159]), token("�", [153, 130])] if split_unicode else [token(text)]
    deltas = [Delta("start", model="m", input_tokens=2, output_tokens=0),
              Delta("text", text=text, logprobs=probabilities),
              Delta("finish", reason="stop", input_tokens=2, output_tokens=1)]
    return b"".join(encode_sse(e.event, e.data) for d in deltas for e in encoder.feed(d)), probabilities


@pytest.mark.parametrize("source,target", [("chat", "responses"), ("responses", "chat")])
@pytest.mark.parametrize("chunk_size", [1, 7, 65536])
@pytest.mark.parametrize("split_unicode", [False, True])
def test_fragmented_stream_preserves_actual_token_bytes_and_terminal_probabilities(source, target, chunk_size, split_unicode):
    raw, expected = stream(source, split_unicode=split_unicode)
    pipe = StreamTranscoder(source, target, text_logprobs=True)
    output = b"".join(pipe.feed(raw[i:i + chunk_size]) for i in range(0, len(raw), chunk_size)) + pipe.finish()
    events = [json.loads(e.data) for e in SSEParser().feed(output) if e.data != "[DONE]"]
    if target == "responses":
        assert events[-1]["response"]["output"][-1]["content"][0]["logprobs"] == expected
    else:
        assert [r for e in events for c in e.get("choices", []) for r in (c.get("logprobs") or {}).get("content", [])] == expected


@pytest.mark.parametrize("where", ["response.output_text.done", "response.content_part.done", "response.output_item.done", "response.completed"])
def test_responses_probability_snapshot_tampering_fails_before_success(where):
    raw, _ = stream("responses")
    events = SSEParser().feed(raw)
    output = []
    for event in events:
        body = json.loads(event.data)
        if body["type"] == where:
            if where == "response.output_text.done":
                body["logprobs"] = []
            elif where == "response.content_part.done":
                body["part"]["logprobs"] = []
            elif where == "response.output_item.done":
                body["item"]["content"][0]["logprobs"] = []
            else:
                body["response"]["output"][0]["content"][0]["logprobs"] = []
        output.append(encode_sse(event.event, json.dumps(body)))
    with pytest.raises(StreamError):
        StreamTranscoder("responses", "chat", text_logprobs=True).feed(b"".join(output))


def prompt(protocol, *, top=1):
    body = {"model": "m", "input": "synthetic", "max_output_tokens": 20}
    body = convert_request(body, "responses", protocol)
    if protocol == "chat":
        body["logprobs"] = True
    else:
        body["include"] = ["message.output_text.logprobs"]
    if top is not None:
        body["top_logprobs"] = top
    return body


@pytest.mark.parametrize("source,target", [("chat", "responses"), ("responses", "chat")])
@pytest.mark.parametrize("top", [None, 0, 1, 20])
def test_enabled_request_controls_round_trip_and_selected_only_gets_explicit_zero(source, target, top):
    body = prompt(source, top=top)
    before = copy.deepcopy(body)
    converted = convert_request(body, source, target, text_logprobs=True)
    assert converted["top_logprobs"] == (top if top is not None else 0)
    assert converted.get("logprobs") is True if target == "chat" else converted["include"] == ["message.output_text.logprobs"]
    assert body == before
    with pytest.raises(ConversionError):
        convert_request(body, source, "messages", text_logprobs=True)


@pytest.mark.parametrize("source,target", [("chat", "responses"), ("responses", "chat")])
@pytest.mark.parametrize("streaming", [False, True])
def test_gateway_automatically_preserves_probabilities_only_for_explicit_requests(source, target, streaming):
    seen = []

    def upstream(req, *, timeout):
        seen.append(json.loads(req.data))
        raw = stream(source)[0] if streaming else json.dumps(response(source)).encode()
        return io.BytesIO(raw)

    cfg = GatewayConfig(source, "https://example.invalid/v1", "synthetic", "local", client_profile="codex" if target == "responses" else "strict")
    body = {**prompt(target), "stream": streaming}
    with listener(cfg, upstream) as url, request(url, target, json.dumps(body).encode()) as result:
        raw = result.read()
    assert b"top_logprobs" in raw and b"-0.25" in raw
    assert len(seen) == 1 and seen[0]["top_logprobs"] == 1
    assert seen[0].get("logprobs") is True if source == "chat" else seen[0]["include"] == ["message.output_text.logprobs"]


@pytest.mark.parametrize("streaming", [False, True])
def test_gateway_rejects_provider_exceeding_the_requested_top_k(streaming):
    raw = stream("chat")[0] if streaming else json.dumps(response("chat")).encode()
    cfg = GatewayConfig("chat", "https://example.invalid/v1", "synthetic", "local")
    with listener(cfg, lambda *a, **k: io.BytesIO(raw)) as url:
        with pytest.raises(HTTPError) as caught:
            request(url, "responses", json.dumps({**prompt("responses", top=0), "stream": streaming}).encode())
        assert caught.value.code == 502
        caught.value.close()


@pytest.mark.parametrize("source", ["chat", "responses"])
def test_bare_json_and_stream_context_limits_cannot_be_exceeded(source):
    target = "responses" if source == "chat" else "chat"
    with pytest.raises(ConversionError, match="top-k"):
        convert_response(response(source), source, target, text_logprobs=True, max_top_logprobs=0)
    with pytest.raises(StreamError, match="top-k"):
        StreamTranscoder(source, target, text_logprobs=True, max_top_logprobs=0).feed(stream(source)[0])


@pytest.mark.parametrize("source", ["chat", "responses"])
def test_explicit_mode_rejects_missing_measurements_for_nonempty_text(source):
    target = "responses" if source == "chat" else "chat"
    with pytest.raises(ConversionError, match="omitted requested"):
        convert_response(json.loads(reply(source)), source, target, text_logprobs=True)
    pipe = StreamTranscoder(source, target, text_logprobs=True)
    with pytest.raises(StreamError, match="omitted requested"):
        pipe.feed(reply(source, stream=True))


@pytest.mark.parametrize("streaming", [False, True])
def test_gateway_cannot_claim_requested_probabilities_when_provider_ignores_controls(streaming):
    cfg = GatewayConfig("chat", "https://example.invalid/v1", "synthetic", "local")
    raw = reply("chat", stream=streaming)
    with listener(cfg, lambda *a, **k: io.BytesIO(raw)) as url:
        with pytest.raises(HTTPError) as caught:
            request(url, "responses", json.dumps({**prompt("responses"), "stream": streaming}).encode())
        assert caught.value.code == 502
        caught.value.close()


@pytest.mark.parametrize("include", [[{}], [[]]])
def test_malformed_codex_include_is_422_without_provider_io(include):
    seen = []
    cfg = GatewayConfig("chat", "https://example.invalid/v1", "synthetic", "local", client_profile="codex")
    with listener(cfg, lambda *a, **k: seen.append(1)) as url:
        with pytest.raises(HTTPError) as caught:
            request(url, "responses", json.dumps({"model": "m", "input": "synthetic", "include": include}).encode())
        assert caught.value.code == 422
        caught.value.close()
    assert seen == []


@pytest.mark.parametrize("where", ["response.output_text.done", "response.content_part.done", "response.output_item.done", "response.completed"])
def test_snapshot_comparison_cannot_hide_float_bytes_that_compare_equal_to_integers(where):
    raw, _ = stream("responses")
    output = []
    for frame in SSEParser().feed(raw):
        body = json.loads(frame.data)
        if body["type"] == where:
            if where == "response.output_text.done":
                probabilities = body["logprobs"]
            elif where == "response.content_part.done":
                probabilities = body["part"]["logprobs"]
            elif where == "response.output_item.done":
                probabilities = body["item"]["content"][0]["logprobs"]
            else:
                probabilities = body["response"]["output"][0]["content"][0]["logprobs"]
            probabilities[0]["bytes"][0] = float(probabilities[0]["bytes"][0])
        output.append(encode_sse(frame.event, json.dumps(body)))
    with pytest.raises(StreamError, match="byte arrays"):
        StreamTranscoder("responses", "chat", text_logprobs=True).feed(b"".join(output))
