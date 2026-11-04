import io
import json
from dataclasses import asdict
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from test_observability import Segments, chat_chunk, observed_server, post

from protoverta.errors import UnsupportedFeatureError
from protoverta.failures import gateway_failure
from protoverta.observability import RequestTrace
from protoverta.session_diagnostics import diagnostic_journal, latest_diagnostics
from protoverta.sse import SSEEvent, encode_sse
from protoverta.streaming import StreamDecoder, UnsupportedStreamFeatureError


def test_gateway_failure_carries_structured_phase_feature_path_and_policy_refusal():
    refusal = gateway_failure(
        422,
        "refusal cannot be preserved with secret-key",
        code="unsupported_feature",
        phase="response",
        feature="refusal",
        path="/v1/responses",
        secrets=("secret-key",),
    )
    assert refusal.code == "policy_refusal"
    assert refusal.phase == "response"
    assert refusal.feature == "refusal"
    assert refusal.path == "/v1/responses"
    assert "secret-key" not in refusal.message
    payload = refusal.payload("responses")
    assert payload == {
        "error": {
            "type": "invalid_request_error",
            "message": "refusal cannot be preserved with [REDACTED]",
            "code": "policy_refusal",
            "phase": "response",
            "feature": "refusal",
            "path": "/v1/responses",
        }
    }
    stream_event = refusal.stream_payload("responses", sequence_number=4)
    assert stream_event == {
        "type": "error",
        "code": "policy_refusal",
        "message": "refusal cannot be preserved with [REDACTED]",
        "param": "/v1/responses",
        "sequence_number": 4,
        "phase": "response",
        "feature": "refusal",
        "path": "/v1/responses",
    }


def test_unknown_feature_or_phase_is_not_trusted_in_gateway_failure():
    with pytest.raises(ValueError, match="unknown unsupported-feature category"):
        UnsupportedFeatureError("made_up_feature")
    failure = gateway_failure(
        422,
        "bad",
        code="arbitrary_code",
        phase="arbitrary_phase",  # type: ignore[arg-type]
        feature="arbitrary_feature",
        path="/v1/\r\ninjected",
    )
    assert failure.code == "invalid_request"
    assert failure.phase is None
    assert failure.feature is None
    assert failure.path is None


def test_pause_turn_is_rejected_with_pause_turn_feature_in_json_and_sse():
    messages_pause_json = json.dumps(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "m",
            "content": [{"type": "text", "text": "partial"}],
            "stop_reason": "pause_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }
    ).encode()

    from protoverta.core import convert_response

    with pytest.raises(UnsupportedFeatureError) as exc_info:
        convert_response(json.loads(messages_pause_json), "messages", "responses")
    assert exc_info.value.feature == "pause_turn"

    decoder = StreamDecoder("messages")
    decoder.feed(
        SSEEvent(
            "message_start",
            json.dumps(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "model": "m",
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 4, "output_tokens": 0},
                    },
                }
            ),
        )
    )
    with pytest.raises(UnsupportedStreamFeatureError) as stream_exc:
        decoder.feed(
            SSEEvent(
                "message_delta",
                json.dumps(
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "pause_turn", "stop_sequence": None},
                        "usage": {"output_tokens": 2},
                    }
                ),
            )
        )
    assert stream_exc.value.feature == "pause_turn"


@pytest.mark.parametrize("visible", [False, True])
def test_policy_refusal_stream_distinguishes_pre_header_status_and_post_header_sse_error(visible):
    records = []
    chunks = [
        chat_chunk({"role": "assistant", "content": "partial" if visible else ""}),
        chat_chunk({"refusal": "policy refusal secret-key"}),
    ]
    with observed_server(lambda req, timeout: Segments(chunks), records.append) as origin:
        body = {"model": "m", "input": "hi", "stream": True}
        if visible:
            with post(origin, body, path="/v1/responses") as response:
                wire = response.read().decode("utf-8")
                status = response.status
            assert status == 200
            assert '"code": "policy_refusal"' in wire
            assert '"phase": "stream"' in wire
            assert '"feature": "refusal"' in wire
            assert "response.completed" not in wire
        else:
            with pytest.raises(HTTPError) as caught:
                post(origin, body, path="/v1/responses")
            assert caught.value.code == 422
            assert caught.value.headers.get("x-should-retry") == "false"
            err_body = json.loads(caught.value.read().decode("utf-8"))
            caught.value.close()
            assert err_body["error"]["code"] == "policy_refusal"
            assert err_body["error"]["phase"] == "stream"
            assert err_body["error"]["feature"] == "refusal"
            assert err_body["error"]["path"] == "/v1/responses"
    assert len(records) == 1
    assert records[0].outcome == "policy_refusal"
    assert records[0].phase == "stream"
    assert records[0].feature == "refusal"
    assert records[0].status == (200 if visible else 422)


def test_truncated_output_is_distinguished_in_json_and_sse_observations():
    records = []
    truncated_json = io.BytesIO(
        b'{"model":"m","choices":[{"index":0,"message":{"role":"assistant","content":"cut"},"finish_reason":"length"}],"usage":{"prompt_tokens":4,"completion_tokens":8,"total_tokens":12}}'
    )
    with observed_server(lambda req, timeout: truncated_json, records.append) as origin, post(origin, {"model": "m", "input": "hi"}, path="/v1/responses") as response:
        assert response.headers.get("X-Protoverta-Finish-Category") == "truncated_output"
        data = json.loads(response.read().decode("utf-8"))
        assert data["status"] == "incomplete"
        assert data["incomplete_details"] == {"reason": "max_output_tokens"}
    assert len(records) == 1
    assert records[0].outcome == "truncated_output"
    assert records[0].phase == "response"
    assert records[0].status == 200

    stream_records = []
    chunks = [
        chat_chunk({"role": "assistant", "content": "cut"}),
        chat_chunk({}, "length"),
        encode_sse(
            "message",
            json.dumps(
                {
                    "object": "chat.completion.chunk",
                    "model": "m",
                    "choices": [],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 8, "total_tokens": 12},
                }
            ),
        ),
        encode_sse("message", "[DONE]"),
    ]
    with observed_server(lambda req, timeout: Segments(chunks), stream_records.append) as origin, post(origin, {"model": "m", "input": "hi", "stream": True}, path="/v1/responses") as response:
        wire = response.read().decode("utf-8")
        assert "response.incomplete" in wire
    assert len(stream_records) == 1
    assert stream_records[0].outcome == "truncated_output"
    assert stream_records[0].phase == "stream"


def test_agent_and_chain_correlation_is_authenticated_bounded_hashed_and_not_forwarded():
    records = []
    outbound_headers = []

    def upstream(request, timeout):
        outbound_headers.append({k.lower(): v for k, v in request.header_items()})
        return io.BytesIO(
            b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}'
        )

    with observed_server(upstream, records.append) as origin:
        # 1. Unauthenticated request with correlation headers must NOT record digests or reach upstream.
        with pytest.raises(HTTPError) as unauth:
            urlopen(
                Request(
                    origin + "/v1/chat/completions",
                    data=b"{}",
                    headers={
                        "Authorization": "Bearer wrong",
                        "X-Protoverta-Agent-ID": "agent-secret-raw",
                        "X-Protoverta-Chain-ID": "chain-secret-raw",
                    },
                ),
                timeout=5,
            )
        assert unauth.value.code == 401
        unauth.value.close()

        # 2. Authenticated request with valid correlation headers records 64-hex HMAC digests, never forwards raw headers.
        with post(
            origin,
            headers={
                "X-Protoverta-Agent-ID": "agent-worker-1",
                "X-Protoverta-Chain-ID": "chain-root-42",
            },
        ) as resp:
            resp.read()

        # 3. Oversized or control-char correlation headers are ignored safely.
        with post(
            origin,
            headers={
                "X-Protoverta-Agent-ID": "x" * 200,
                "X-Protoverta-Chain-ID": "bad value with spaces",
            },
        ) as resp:
            resp.read()

    assert len(outbound_headers) == 2
    for hdrs in outbound_headers:
        assert "x-protoverta-agent-id" not in hdrs
        assert "x-protoverta-chain-id" not in hdrs

    assert records[0].outcome == "authentication"
    assert records[0].agent_digest is None
    assert records[0].chain_digest is None

    assert records[1].outcome == "success"
    assert records[1].agent_digest is not None and len(records[1].agent_digest) == 64
    assert records[1].chain_digest is not None and len(records[1].chain_digest) == 64
    serialized = json.dumps(asdict(records[1]))
    assert "agent-worker-1" not in serialized
    assert "chain-root-42" not in serialized

    assert records[2].outcome == "success"
    assert records[2].agent_digest is None
    assert records[2].chain_digest is None


def test_journal_schema_version_compatibility_for_old_and_new_readers(tmp_path):
    # Write a v2 journal via diagnostic_journal
    with diagnostic_journal(tmp_path) as sink:
        trace = RequestTrace("responses", "chat")
        trace.status = 422
        trace.outcome = "policy_refusal"
        trace.phase = "response"
        trace.feature = "refusal"
        trace.correlation("agent-1", "chain-1", b"x" * 32)
        trace.emit(sink)

    report_v2 = latest_diagnostics(tmp_path)
    assert report_v2["schema_version"] == 2
    assert len(report_v2["records"]) == 1
    rec = report_v2["records"][0]
    assert rec["outcome"] == "policy_refusal"
    assert rec["phase"] == "response"
    assert rec["feature"] == "refusal"
    assert len(rec["agent_digest"]) == 64

    # Write a legacy schema_version 1 journal without the P09 fields
    v1_dir = tmp_path / "legacy"
    v1_dir.mkdir()
    v1_path = v1_dir / ("session-" + "f" * 32 + ".jsonl")
    legacy_row = {
        "request_id": "a" * 32,
        "protocol": "chat",
        "upstream_protocol": "chat",
        "status": 200,
        "outcome": "success",
        "duration_ms": 15,
        "streaming": False,
    }
    v1_path.write_text(
        json.dumps({"kind": "protoverta-session", "schema_version": 1})
        + "\n"
        + json.dumps(legacy_row)
        + "\n",
        encoding="utf-8",
    )
    report_v1 = latest_diagnostics(v1_dir)
    assert report_v1["schema_version"] == 1
    assert len(report_v1["records"]) == 1
    assert report_v1["records"][0]["phase"] is None
    assert report_v1["records"][0]["feature"] is None
    assert report_v1["records"][0]["agent_digest"] is None
    assert report_v1["records"][0]["chain_digest"] is None

