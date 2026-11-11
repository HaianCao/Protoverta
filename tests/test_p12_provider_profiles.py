import io
import json
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post

from protoverta import (
    CapabilityRecord,
    CapabilityRegistry,
    ConversionError,
    Protocol,
    convert_request,
)
from protoverta.evidence_identity import route_identity
from protoverta.provider_profiles import ProviderProfile, ProviderProfiles
from protoverta.server import GatewayConfig


def make_profile(**options) -> ProviderProfile:
    base = {
        "base_url": "https://example.test/v1",
        "protocol": "chat",
        "model": "model-p12",
        "checked_at": "2026-11-04",
        "evidence": "synthetic fixture; verifies P12 provider profile invariants",
        "chat_token_field": "max_tokens",
        "token_budget_semantics": "completion_total",
    }
    base.update(options)
    return ProviderProfile(**base)


def test_p12_evidence_and_identity_lock_by_endpoint_protocol_model_and_version():
    # Profiles require verifiable evidence, ISO date, exact model ID, and Chat protocol
    with pytest.raises(ValueError, match="evidence"):
        make_profile(evidence="   ")
    with pytest.raises(ValueError, match="exact model ID"):
        make_profile(model="model with spaces")
    with pytest.raises(ValueError, match="Chat upstream"):
        make_profile(protocol="messages")

    p1 = ProviderProfiles((make_profile(),))
    p2 = ProviderProfiles((make_profile(chat_token_field="max_completion_tokens"),))
    p3 = ProviderProfiles(
        (
            make_profile(
                parameter_aliases={"lookup": {" $schema ": "schema_ref", "file-path": "file_path"}},
            ),
        )
    )
    assert p1.fingerprint() != p2.fingerprint()
    assert p1.fingerprint() != p3.fingerprint()

    cfg1 = GatewayConfig("chat", "https://example.test/v1", "k", "t", provider_profiles=p1)
    cfg2 = GatewayConfig("chat", "https://example.test/v1", "k", "t", provider_profiles=p2)
    cfg3 = GatewayConfig("chat", "https://example.test/v1", "k", "t", provider_profiles=p3)
    assert route_identity(cfg1) != route_identity(cfg2)
    assert route_identity(cfg1) != route_identity(cfg3)


def test_p12_parameter_aliases_are_injective_reversible_and_never_touch_arbitrary_text():
    # Non-injective or overlapping parameter aliases are rejected at profile construction
    with pytest.raises(ValueError, match="injective"):
        make_profile(parameter_aliases={"lookup": {"a": "x", "b": "x"}})
    with pytest.raises(ValueError, match="disjoint"):
        make_profile(parameter_aliases={"lookup": {"a": "b", "b": "c"}})
    with pytest.raises(ValueError, match="distinct"):
        make_profile(parameter_aliases={"lookup": {"a": "a"}})

    prof = make_profile(parameter_aliases={"read_file": {"$path": "file_path", "line-count": "line_count"}})
    profiles = ProviderProfiles((prof,))

    chat_req = {
        "model": "model-p12",
        "max_completion_tokens": 64,
        "messages": [
            {"role": "user", "content": "Please read $path and line-count from disk"},
            {
                "role": "assistant",
                "content": "Calling read_file with $path and line-count in text",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"$path": "/tmp/$path", "line-count": 10, "extra": "keep"}),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "Result containing $path and line-count"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Reads $path with line-count",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "$path": {"type": "string", "description": "Path ($path)"},
                            "line-count": {"type": "integer"},
                            " untouched ": False,
                        },
                        "required": ["$path", "line-count"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
    }

    applied = profiles.apply("https://example.test/v1", "chat", chat_req)
    # 1. Arbitrary text in user/assistant/tool messages and descriptions is untouched
    assert applied["messages"][0]["content"] == "Please read $path and line-count from disk"
    assert applied["messages"][1]["content"] == "Calling read_file with $path and line-count in text"
    assert applied["messages"][2]["content"] == "Result containing $path and line-count"
    assert applied["tools"][0]["function"]["description"] == "Reads $path with line-count"

    # 2. Tool schema properties and required list are mapped; boolean schema & additionalProperties are preserved
    schema = applied["tools"][0]["function"]["parameters"]
    assert set(schema["properties"].keys()) == {"file_path", "line_count", " untouched "}
    assert schema["properties"][" untouched "] is False
    assert schema["required"] == ["file_path", "line_count"]
    assert schema["additionalProperties"] is False

    # 3. Historical assistant tool_call arguments keys are mapped, values untouched
    hist_args = json.loads(applied["messages"][1]["tool_calls"][0]["function"]["arguments"])
    assert hist_args == {"file_path": "/tmp/$path", "line_count": 10, "extra": "keep"}

    # 4. Upstream response tool_calls are reversibly restored to original parameter names
    upstream_resp = {
        "id": "chatcmpl_1",
        "object": "chat.completion",
        "model": "model-p12",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Done with file_path",
                    "tool_calls": [
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"file_path": "/etc/hosts", "line_count": 5}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    restored = profiles.restore("https://example.test/v1", "chat", "model-p12", upstream_resp)
    restored_args = json.loads(restored["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
    assert restored_args == {"$path": "/etc/hosts", "line-count": 5}
    assert restored["choices"][0]["message"]["content"] == "Done with file_path"


def test_p12_gateway_round_trip_with_parameter_aliases_and_no_schema_stripping():
    prof = make_profile(parameter_aliases={"lookup": {"$q": "query_str"}})
    profiles = ProviderProfiles((prof,))
    captured = []

    def upstream(request, *, timeout):
        captured.append(json.loads(request.data))
        return io.BytesIO(
            json.dumps(
                {
                    "id": "c1",
                    "object": "chat.completion",
                    "model": "model-p12",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_9",
                                        "type": "function",
                                        "function": {"name": "lookup", "arguments": '{"query_str":"hello"}'},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                }
            ).encode("utf-8")
        )

    config = GatewayConfig("chat", "https://example.test/v1", "key", "local", provider_profiles=profiles)
    req_body = {
        "model": "model-p12",
        "max_output_tokens": 32,
        "input": "search for hello",
        "tools": [
            {
                "type": "function",
                "name": "lookup",
                "description": "Search",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {"$q": {"type": "string"}, "disabled_prop": False},
                    "required": ["$q"],
                    "additionalProperties": False,
                },
            }
        ],
    }
    with listener(config, upstream) as url, post(url, req_body) as response:
        assert response.status == 200
        resp_data = json.loads(response.read())

    # Upstream received aliased property name and intact boolean schema / required / additionalProperties
    sent_params = captured[0]["tools"][0]["function"]["parameters"]
    assert sent_params == {
        "type": "object",
        "properties": {"query_str": {"type": "string"}, "disabled_prop": False},
        "required": ["query_str"],
        "additionalProperties": False,
    }
    # Client received restored original parameter name '$q'
    call_items = [item for item in resp_data["output"] if item.get("type") == "function_call"]
    assert len(call_items) == 1
    assert json.loads(call_items[0]["arguments"]) == {"$q": "hello"}


def test_p12_gateway_streaming_restores_parameter_aliases():
    from protoverta.sse import SSEParser, encode_sse
    from protoverta.stream_encoder import StreamEncoder
    from protoverta.streaming import Delta

    prof = make_profile(parameter_aliases={"lookup": {"$q": "query_str"}})
    profiles = ProviderProfiles((prof,))

    def upstream(request, *, timeout):
        enc = StreamEncoder(Protocol.CHAT)
        deltas = [
            Delta("start", model="model-p12", input_tokens=4, output_tokens=0),
            Delta("tool_start", index=0, call_id="call_s1", name="lookup"),
            Delta("tool_delta", index=0, text='{"query_str":'),
            Delta("tool_delta", index=0, text='"streamed"}'),
            Delta("tool_end", index=0),
            Delta("finish", reason="tool_calls", input_tokens=4, output_tokens=3),
        ]
        wire = b"".join(encode_sse(ev.event, ev.data) for d in deltas for ev in enc.feed(d))
        return io.BytesIO(wire)

    config = GatewayConfig("chat", "https://example.test/v1", "key", "local", provider_profiles=profiles)
    req_body = {
        "model": "model-p12",
        "max_output_tokens": 32,
        "stream": True,
        "input": "search for streamed",
        "tools": [
            {
                "type": "function",
                "name": "lookup",
                "parameters": {"type": "object", "properties": {"$q": {"type": "string"}}, "required": ["$q"]},
            }
        ],
    }
    with listener(config, upstream) as url, post(url, req_body) as response:
        assert response.status == 200
        raw_sse = response.read()
    events = [json.loads(ev.data) for ev in SSEParser().feed(raw_sse)]
    completed = next(ev["response"] for ev in events if ev["type"] == "response.completed")
    fn_call = next(item for item in completed["output"] if item["type"] == "function_call")
    assert json.loads(fn_call["arguments"]) == {"$q": "streamed"}


def test_p12_token_cap_and_reasoning_effort_never_clamp_or_downgrade_and_unknown_vendor_fields_reject():
    # Capability preflight rejects over-cap token budget and unsupported effort rather than clamping
    reg = CapabilityRegistry(
        (
            CapabilityRecord(
                "https://example.test/v1",
                "chat",
                "model-p12",
                {"text": True, "reasoning_effort": True},
                "2026-11-04",
                "synthetic cap fixture",
                max_output_tokens=128,
                reasoning_efforts=("low", "medium"),
            ),
        )
    )
    prof = make_profile(
        reasoning_field="thinking.level",
        reasoning_values={"low": 1, "medium": 2},
        reasoning_semantics="effort_equivalent",
    )
    config = GatewayConfig(
        "chat",
        "https://example.test/v1",
        "key",
        "local",
        capabilities=reg,
        provider_profiles=ProviderProfiles((prof,)),
    )

    def fail_upstream(request, *, timeout):
        raise AssertionError("upstream must not be called when token cap or effort fails preflight")

    with listener(config, fail_upstream) as url:
        # 1. Over-cap max_output_tokens is rejected (HTTP 422), never clamped to 128
        with pytest.raises(HTTPError) as exc_info:
            post(url, {"model": "model-p12", "input": "hi", "max_output_tokens": 256})
        assert exc_info.value.code == 422
        err_body = json.loads(exc_info.value.read())
        assert "max_output_tokens limit exceeded (128)" in err_body["error"]["message"]
        exc_info.value.close()

        # 2. Uncertified reasoning effort ('high') is rejected (HTTP 422), never downgraded to 'medium'
        with pytest.raises(HTTPError) as exc_info:
            post(url, {"model": "model-p12", "input": "hi", "max_output_tokens": 64, "reasoning": {"effort": "high"}})
        assert exc_info.value.code == 422
        exc_info.value.close()

    # 3. Unknown vendor fields are rejected across protocols, never blindly passed through
    for source, target, payload in (
        (
            Protocol.CHAT,
            Protocol.RESPONSES,
            {"model": "m", "messages": [{"role": "user", "content": "hi"}], "vendor_extra": 123},
        ),
        (Protocol.RESPONSES, Protocol.CHAT, {"model": "m", "input": "hi", "vendor_extra": 123}),
        (
            Protocol.MESSAGES,
            Protocol.CHAT,
            {"model": "m", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}], "vendor_extra": 123},
        ),
    ):
        with pytest.raises(ConversionError, match="unsupported fields: vendor_extra"):
            convert_request(payload, source, target)
