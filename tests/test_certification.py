import io
import json
import re

import pytest

from protoverta.capabilities import CapabilityRegistry
from protoverta.certification import check_route, evidence_report
from protoverta.core import convert_request, convert_response
from protoverta.server import GatewayConfig
from protoverta.sse import encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta


@pytest.mark.parametrize("source", ["chat", "messages", "responses"])
@pytest.mark.parametrize("upstream_protocol", ["chat", "messages", "responses"])
def test_route_doctor_checks_real_local_gateway_all_protocol_pairs(source, upstream_protocol):
    config = GatewayConfig(
        upstream_protocol, "https://example.invalid/v1", "fixture", "local", messages_usage_policy="deferred"
    )

    def upstream(request, timeout):
        body = convert_request(json.loads(request.data), upstream_protocol, "chat")
        nonce = re.search(r"nonce_[a-f0-9]+", json.dumps(body)).group() if "nonce_" in json.dumps(body) else "unused"
        forced = isinstance(body.get("tool_choice"), dict)
        if forced:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_fixture", "type": "function", "function": {"name": "fixture_nonce", "arguments": "{}"}}
                ],
            }
            finish = "tool_calls"
        else:
            message, finish = {"role": "assistant", "content": nonce}, "stop"
        if body.get("stream"):
            encoder = StreamEncoder(upstream_protocol)
            deltas = [Delta("start", model="m", input_tokens=1, output_tokens=0)]
            if forced:
                deltas += [
                    Delta("tool_start", index=0, call_id="call_fixture", name="fixture_nonce"),
                    Delta("tool_delta", index=0, text="{}"),
                    Delta("tool_end", index=0),
                ]
            else:
                deltas += [Delta("text", index=0, text=nonce)]
            deltas += [Delta("finish", reason=finish, input_tokens=1, output_tokens=1)]
            return io.BytesIO(
                b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta))
            )
        result = {
            "model": "m",
            "choices": [{"message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        return io.BytesIO(json.dumps(convert_response(result, "chat", upstream_protocol)).encode())

    report = check_route(config, "m", source, upstream_open=upstream)
    assert all(item["passed"] for item in report), report
    assert sum(item["requests"] for item in report) == 8
    evidence = evidence_report(config, "m", source, report, verified_with="local synthetic fixtures")
    CapabilityRegistry.from_dict(evidence)
    assert len(evidence["routes"]) == 2
    assert "nonce_" not in json.dumps(report)


def test_route_doctor_requires_explicit_live_opt_in_and_failed_checks_certify_nothing():
    config = GatewayConfig("chat", "https://example.invalid/v1", "fixture", "local")
    with pytest.raises(ValueError, match="allow_live"):
        check_route(config, "m", "messages")

    def failed(request, timeout):
        raise OSError("private provider/key details")

    result = check_route(config, "m", "responses", cases=("text",), upstream_open=failed)
    assert result[0]["passed"] is False
    assert "private" not in json.dumps(result)
    assert evidence_report(config, "m", "responses", result, verified_with="fixture")["routes"] == []


@pytest.mark.parametrize("source", ["chat", "messages", "responses"])
@pytest.mark.parametrize("streaming", [False, True])
def test_truncated_outputs_never_certify_success(source, streaming):
    config = GatewayConfig("chat", "https://example.invalid/v1", "fixture", "local", messages_usage_policy="deferred")

    def upstream(request, timeout):
        nonce = re.search(r"nonce_[a-f0-9]+", request.data.decode()).group()
        if streaming:
            encoder = StreamEncoder("chat")
            deltas = [
                Delta("start", model="m", input_tokens=1, output_tokens=0),
                Delta("text", index=0, text=nonce),
                Delta("finish", reason="length", input_tokens=1, output_tokens=1),
            ]
            return io.BytesIO(
                b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta))
            )
        return io.BytesIO(
            json.dumps(
                {
                    "model": "m",
                    "choices": [{"message": {"role": "assistant", "content": nonce}, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
            ).encode()
        )

    report = check_route(config, "m", source, cases=("streaming" if streaming else "text",), upstream_open=upstream)
    assert report[0]["passed"] is False
    assert report[0]["outcome"] == "incomplete"
    assert evidence_report(config, "m", source, report, verified_with="fixture")["routes"] == []


@pytest.mark.parametrize("source", ["chat", "responses"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_doctor_obeys_public_reasoning_policy_without_certifying_reasoning(source, streaming, enabled):
    config = GatewayConfig("chat", "https://example.invalid/v1", "fixture", "local", public_reasoning=enabled)

    def upstream(request, timeout):
        nonce = re.search(r"nonce_[a-f0-9]+", request.data.decode()).group()
        if streaming:
            encoder = StreamEncoder("chat", public_reasoning=True)
            deltas = [
                Delta("start", model="m", input_tokens=1, output_tokens=0),
                Delta("reasoning_text", index=0, text="synthetic public reasoning"),
                Delta("text", index=0, text=nonce),
                Delta("finish", reason="stop", input_tokens=1, output_tokens=1),
            ]
            return io.BytesIO(
                b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta))
            )
        return io.BytesIO(
            json.dumps(
                {
                    "model": "m",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": nonce,
                                "reasoning_content": "synthetic public reasoning",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                }
            ).encode()
        )

    report = check_route(config, "m", source, cases=("streaming" if streaming else "text",), upstream_open=upstream)
    assert report[0]["passed"] is enabled
    evidence = evidence_report(config, "m", source, report, verified_with="fixture")
    assert all("reasoning" not in feature for route in evidence["routes"] for feature in route["features"])


def test_public_reasoning_doctor_still_rejects_opaque_reasoning():
    config = GatewayConfig("responses", "https://example.invalid/v1", "fixture", "local", public_reasoning=True)

    def upstream(request, timeout):
        nonce = re.search(r"nonce_[a-f0-9]+", request.data.decode()).group()
        return io.BytesIO(
            json.dumps(
                {
                    "model": "m",
                    "status": "completed",
                    "output": [
                        {"id": "rs_fixture", "type": "reasoning", "summary": [], "encrypted_content": "opaque-fixture"},
                        {
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": nonce}],
                        },
                    ],
                }
            ).encode()
        )

    report = check_route(config, "m", "responses", cases=("text",), upstream_open=upstream)
    assert report[0]["passed"] is False
    assert evidence_report(config, "m", "responses", report, verified_with="fixture")["routes"] == []
