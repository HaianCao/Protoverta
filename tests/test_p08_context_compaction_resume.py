from __future__ import annotations

import copy
import io
import json
import sys
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post

from protoverta import convert_request, convert_response
from protoverta.capabilities import (
    CapabilityRecord,
    CapabilityRegistry,
    RouteCapabilityRecord,
    route_policies,
)
from protoverta.context import TokenCount, assess_context
from protoverta.errors import UnsupportedFeatureError
from protoverta.harness_controls import guard_codex_closed_resume
from protoverta.model_snapshot import ModelSnapshot, SnapshotModel
from protoverta.selection_status import selection_status
from protoverta.server import GatewayConfig
from protoverta.sse import SSEEvent, encode_sse
from protoverta.stream_pipe import StreamValidator
from protoverta.streaming import StreamDecoder, UnsupportedStreamFeatureError
from protoverta.token_counting import ModelTokenCounter, NativeCountError, count_input


def test_preflight_and_ui_distinguish_exact_estimate_and_unavailable():
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 16,
    }
    estimate = assess_context(body, "chat", 4096)
    assert estimate.headers()["X-Protoverta-Context-Count-Kind"] == "utf8-json-byte-estimate"
    assert estimate.headers()["X-Protoverta-Context-Count-Category"] == "estimate"

    exact = assess_context(
        body,
        "chat",
        4096,
        policy="exact",
        counter=lambda payload, protocol: TokenCount(12, exact=True),
    )
    assert exact.headers()["X-Protoverta-Context-Count-Kind"] == "operator-exact"
    assert exact.headers()["X-Protoverta-Context-Count-Category"] == "exact"

    unavailable = assess_context(
        body | {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.test/img.png"}}]}]},
        "chat",
        4096,
    )
    assert unavailable.headers()["X-Protoverta-Context-Count-Kind"] == "unavailable"
    assert unavailable.headers()["X-Protoverta-Context-Count-Category"] == "unavailable"

    gateway = GatewayConfig("chat", "https://fixture.test/v1", "key", "local", client_profile="codex")
    record = CapabilityRecord(
        gateway.upstream_base_url,
        "chat",
        "m",
        {"text": True, "streaming": True},
        "2026-11-04",
        "fixture",
        context_window_tokens=8192,
    )
    route = RouteCapabilityRecord(
        gateway.upstream_base_url,
        "chat",
        "m",
        "responses",
        True,
        "codex",
        route_policies(gateway),
        record.features,
        "2026-11-04",
        "fixture",
        "fixture",
    )
    registry = CapabilityRegistry((record,), (route,))

    off_status = selection_status(
        registry,
        gateway.upstream_base_url,
        "chat",
        "m",
        source_protocol="responses",
        client_profile="codex",
    )
    assert off_status["count_category"] == "unavailable"
    assert off_status["context_window_tokens"] == 8192
    assert "Token count: unavailable; context window: 8192." in off_status["message"]

    est_status = selection_status(
        registry,
        gateway.upstream_base_url,
        "chat",
        "m",
        source_protocol="responses",
        client_profile="codex",
        overrides={"context_preflight": "estimate"},
    )
    assert est_status["count_category"] == "estimate"
    assert "Token count: estimate; context window: 8192." in est_status["message"]

    native_chat_status = selection_status(
        registry,
        gateway.upstream_base_url,
        "chat",
        "m",
        source_protocol="responses",
        client_profile="codex",
        overrides={"token_count_mode": "native"},
    )
    assert native_chat_status["count_category"] == "unavailable"
    assert "Token count: unavailable; context window: 8192." in native_chat_status["message"]

    native_resp_record = CapabilityRecord(
        gateway.upstream_base_url,
        "responses",
        "m",
        {"text": True, "streaming": True},
        "2026-11-04",
        "fixture",
        context_window_tokens=8192,
    )
    native_resp_registry = CapabilityRegistry((native_resp_record,))
    native_status = selection_status(
        native_resp_registry,
        gateway.upstream_base_url,
        "responses",
        "m",
        source_protocol="responses",
        client_profile="codex",
        overrides={"token_count_mode": "native"},
    )
    assert native_status["count_category"] == "exact"
    assert "Token count: exact; context window: 8192." in native_status["message"]


def test_native_responses_compact_preserves_owner_resource_and_stream_validator():
    compact_response = {
        "id": "cmp_provider_owned_123",
        "object": "response.compaction",
        "created_at": 1730000000,
        "output": [
            {
                "id": "cmp_item_1",
                "type": "compaction",
                "encrypted_content": "opaque-provider-owned-blob",
            }
        ],
        "usage": {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
    }
    captured = []

    def upstream(request, timeout):
        captured.append(json.loads(request.data))
        return io.BytesIO(json.dumps(compact_response).encode("utf-8"))

    record = CapabilityRecord(
        "https://example.test/v1",
        "responses",
        "m",
        {"compaction": True},
        "2026-11-04",
        "fixture",
        context_window_tokens=1,
    )
    config = replace(
        GatewayConfig(
            "responses",
            "https://example.test/v1",
            "fixture",
            "local",
            upstream_reasoning="disabled",
            context_preflight="reject_estimate",
            capabilities=CapabilityRegistry((record,)),
        )
    )
    request_body = {
        "model": "m",
        "input": [{"role": "user", "content": "hello"}],
        "previous_response_id": "resp_owner_abc",
    }
    with listener(config, upstream) as url, post(url + "/compact", request_body) as response:
        assert response.status == 200
        assert "X-Protoverta-Context-Verdict" not in response.headers
        returned = json.load(response)
    assert returned == compact_response
    assert captured == [request_body]
    assert "reasoning" not in captured[0]

    # Native StreamValidator("responses") accepts in-stream compaction items
    # while preserving opaque encrypted_content verbatim.
    validator = StreamValidator("responses")
    compaction_item = {
        "id": "cmp_stream_1",
        "type": "compaction",
        "encrypted_content": "opaque-stream-compaction",
    }
    events = [
        (
            "response.created",
            {"type": "response.created", "sequence_number": 0, "response": {"id": "resp_1", "status": "in_progress", "model": "m", "output": []}},
        ),
        ("response.output_item.added", {"type": "response.output_item.added", "sequence_number": 1, "output_index": 0, "item": compaction_item}),
        ("response.output_item.done", {"type": "response.output_item.done", "sequence_number": 2, "output_index": 0, "item": compaction_item}),
        (
            "response.completed",
            {
                "type": "response.completed",
                "sequence_number": 3,
                "response": {
                    "id": "resp_1",
                    "status": "completed",
                    "model": "m",
                    "output": [compaction_item],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
        ),
    ]
    wire = b"".join(encode_sse(name, json.dumps(payload)) for name, payload in events)
    forwarded = validator.feed(wire) + validator.finish()
    assert b"opaque-stream-compaction" in forwarded


def test_cross_protocol_opaque_compaction_rejected_with_remediation():
    with pytest.raises(UnsupportedFeatureError) as exc_info:
        convert_request(
            {"model": "m", "input": [{"type": "compaction", "encrypted_content": "opaque"}]},
            "responses",
            "chat",
        )
    assert exc_info.value.feature == "compaction"
    assert "provider-owned" in str(exc_info.value)

    with pytest.raises(UnsupportedFeatureError) as exc_info:
        convert_request(
            {"model": "m", "input": "hi", "context_management": [{"type": "compact_20260112"}]},
            "responses",
            "chat",
        )
    assert exc_info.value.feature == "compaction"

    with pytest.raises(UnsupportedFeatureError) as exc_info:
        convert_request(
            {
                "model": "m",
                "max_tokens": 32,
                "messages": [{"role": "assistant", "content": [{"type": "compaction", "content": "opaque"}]}],
            },
            "messages",
            "chat",
        )
    assert exc_info.value.feature == "compaction"

    with pytest.raises(UnsupportedFeatureError) as exc_info:
        convert_request(
            {
                "model": "m",
                "max_tokens": 32,
                "context_management": {"edits": []},
                "messages": [{"role": "user", "content": "hi"}],
            },
            "messages",
            "chat",
        )
    assert exc_info.value.feature == "compaction"

    with pytest.raises(UnsupportedFeatureError) as exc_info:
        convert_response(
            {
                "model": "m",
                "status": "completed",
                "output": [{"type": "compaction", "encrypted_content": "opaque"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
            "responses",
            "chat",
        )
    assert exc_info.value.feature == "compaction"

    # StreamDecoder rejects compaction blocks/items with UnsupportedStreamFeatureError("compaction")
    msg_decoder = StreamDecoder("messages")
    msg_decoder.feed(
        SSEEvent(
            "message_start",
            json.dumps({"type": "message_start", "message": {"id": "msg_1", "model": "m", "usage": {"input_tokens": 1, "output_tokens": 0}}}),
        )
    )
    with pytest.raises(UnsupportedStreamFeatureError) as stream_exc:
        msg_decoder.feed(
            SSEEvent(
                "content_block_start",
                json.dumps({"type": "content_block_start", "index": 0, "content_block": {"type": "compaction"}}),
            )
        )
    assert stream_exc.value.feature == "compaction"

    resp_decoder = StreamDecoder("responses")
    resp_decoder.feed(
        SSEEvent(
            "response.created",
            json.dumps({"type": "response.created", "sequence_number": 0, "response": {"id": "resp_1", "model": "m", "status": "in_progress"}}),
        )
    )
    with pytest.raises(UnsupportedStreamFeatureError) as stream_exc:
        resp_decoder.feed(
            SSEEvent(
                "response.output_item.added",
                json.dumps({"type": "response.output_item.added", "sequence_number": 1, "output_index": 0, "item": {"id": "c_1", "type": "compaction"}}),
            )
        )
    assert stream_exc.value.feature == "compaction"

    # Gateway returns HTTP 422 code="unsupported_feature" with x-should-retry: false
    for upstream_proto in ("chat", "messages"):
        config = GatewayConfig(upstream_proto, "https://example.test/v1", "fixture", "local")
        with listener(config, lambda *_: pytest.fail("must not call upstream")) as url:
            with pytest.raises(HTTPError) as http_err:
                post(url + "/compact", {"model": "m", "input": "synthetic"})
            assert http_err.value.code == 422
            assert http_err.value.headers.get("x-should-retry") == "false"
            err_payload = json.loads(http_err.value.read().decode("utf-8"))
            assert err_payload["error"]["code"] == "unsupported_feature"
            assert "provider-owned" in err_payload["error"]["message"]
            http_err.value.close()


def test_mixed_user_text_and_tool_results_preserved_in_chat_and_responses():
    # Claude Code reactive/auto-compaction sends a user message with [tool_result, text]
    body = {
        "model": "m",
        "max_tokens": 128,
        "messages": [
            {"role": "user", "content": "Read file"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "Read", "input": {"file_path": "a.txt"}}]},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call_1", "content": "file contents"},
                    {"type": "text", "text": "CRITICAL: Respond with TEXT ONLY."},
                ],
            },
        ],
    }
    chat_out = convert_request(body, "messages", "chat")
    assert [m["role"] for m in chat_out["messages"]] == ["user", "assistant", "tool", "user"]
    assert chat_out["messages"][2] == {"role": "tool", "tool_call_id": "call_1", "content": "file contents"}
    assert chat_out["messages"][3] == {"role": "user", "content": "CRITICAL: Respond with TEXT ONLY."}


def test_resume_and_child_resume_contracts():
    with pytest.raises(UnsupportedFeatureError) as exc_info:
        guard_codex_closed_resume(
            {"output": [{"type": "function_call", "name": "resume_agent", "namespace": "multi_agent_v1"}]},
            streaming=False,
        )
    assert exc_info.value.feature == "codex_closed_resume"
    assert "send_input" in str(exc_info.value)

    with pytest.raises(UnsupportedStreamFeatureError) as stream_exc:
        guard_codex_closed_resume(
            {"item": {"type": "function_call", "name": "resume_agent", "namespace": "multi_agent_v1"}},
            streaming=True,
        )
    assert stream_exc.value.feature == "codex_closed_resume"


def test_large_tool_output_reserve_count_errors_and_capability_window_smaller_than_catalog():
    large_output = "LINE_DATA_" * 15000  # 150 KB tool output
    body = {
        "model": "m",
        "max_tokens": 500,
        "messages": [
            {"role": "user", "content": "read"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "Read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": large_output},
        ],
    }
    original = copy.deepcopy(body)
    assessment = assess_context(body, "chat", 200000)
    assert body == original  # History is never truncated or rewritten
    assert assessment.input_tokens > 150000
    assert assessment.reserved_output_tokens == 500
    assert assessment.verdict == "within-estimate"

    # Capability record context_window_tokens (2,000) smaller than catalog metadata
    # must reject under reject_estimate without consulting catalog display metadata.
    small_record = CapabilityRecord(
        "https://example.test/v1",
        "chat",
        "m",
        {"text": True},
        "2026-11-04",
        "fixture",
        context_window_tokens=2000,
    )
    snapshot = ModelSnapshot(
        "https://example.test/v1",
        "chat",
        (SnapshotModel("m", "Large Catalog Model 200k"),),
    )
    config = replace(
        GatewayConfig("chat", "https://example.test/v1", "key", "local"),
        context_preflight="reject_estimate",
        capabilities=CapabilityRegistry((small_record,)),
        model_snapshot=snapshot,
    )
    with listener(config, lambda *_: pytest.fail("must reject before upstream I/O")) as url:
        with pytest.raises(HTTPError) as err:
            post(url.replace("/responses", "/chat/completions"), body)
        assert err.value.code == 422
        err.value.close()

    # Count endpoint > 64 KiB response is rejected with 502 NativeCountError and never falls back
    count_cfg = replace(GatewayConfig("messages", "https://example.test/v1", "key", "local"), token_count_mode="native")
    with pytest.raises(NativeCountError) as count_err:
        count_input(
            count_cfg,
            {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            "messages",
            upstream_open=lambda req, timeout: io.BytesIO(b" " * 70000),
        )
    assert count_err.value.status == 502

    # Count HTTP endpoint returns X-Protoverta-Count-Category header
    est_cfg = replace(count_cfg, token_count_mode="estimate")
    with (
        listener(est_cfg, lambda *_: pytest.fail("no upstream")) as url,
        post(
            url.replace("/responses", "/messages/count_tokens"),
            {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        ) as resp,
    ):
        assert resp.headers["X-Protoverta-Count-Kind"] == "utf8-json-byte-estimate"
        assert resp.headers["X-Protoverta-Count-Category"] == "estimate"

    exact_counter = ModelTokenCounter(
        "m",
        "messages",
        render_request=lambda payload: json.dumps(payload["messages"]),
        encode=lambda text: [1, 2, 3],
        exact=True,
    )
    op_cfg = replace(count_cfg, token_count_mode="operator", token_counter=exact_counter)
    with (
        listener(op_cfg, lambda *_: pytest.fail("no upstream")) as url,
        post(
            url.replace("/responses", "/messages/count_tokens"),
            {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        ) as resp,
    ):
        assert resp.headers["X-Protoverta-Count-Kind"] == "operator-exact"
        assert resp.headers["X-Protoverta-Count-Category"] == "exact"


def test_smoke_compaction_resume_classifier():
    scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from smoke_compaction_resume import classify_compaction_request

    codex_flags = classify_compaction_request(
        "codex",
        '{"input": "You are performing a CONTEXT CHECKPOINT COMPACTION."}',
    )
    assert codex_flags == {"is_compact_prompt": True, "has_compacted_summary": False}

    claude_flags = classify_compaction_request(
        "claude",
        '{"messages": [{"role": "user", "content": "Continue the conversation from where it left off without asking the user any further questions."}]}',
    )
    assert claude_flags == {"is_compact_prompt": False, "has_compacted_summary": True}
