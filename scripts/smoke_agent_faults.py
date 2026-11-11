"""Real child timeout/truncated/invalid stream, scripted loopback upstream only."""

from __future__ import annotations

import argparse
import io
import json
import threading
from pathlib import Path
from urllib.parse import urlsplit

import smoke_parallel_agents as parallel
import smoke_subagents as single

from protoverta.core import Protocol
from protoverta.sse import SSEParser, encode_sse

MAX_FAULT_ATTEMPTS = 6


def fault_prefix(protocol):
    """Keep start + visible partial text, never a successful terminal."""
    raw = single.encode(protocol, "p04-fixture-model", text="P05_PARTIAL_NOT_SUCCESS").read()
    events = SSEParser().feed(raw)
    terminal = {
        Protocol.CHAT: lambda event: (
            event.data == "[DONE]"
            or any(choice.get("finish_reason") is not None for choice in json.loads(event.data).get("choices", []))
        ),
        Protocol.MESSAGES: lambda event: event.event in {"message_delta", "message_stop"},
        Protocol.RESPONSES: lambda event: (
            event.event
            in {
                "response.output_text.done",
                "response.content_part.done",
                "response.output_item.done",
                "response.completed",
            }
        ),
    }[protocol]
    prefix = []
    for event in events:
        if terminal(event):
            break
        prefix.append(encode_sse(event.event, event.data))
    return b"".join(prefix)


def verified_trace(records, fault):
    category, operation = (
        ("upstream_timeout", "read")
        if fault == "timeout"
        else ("invalid_stream", "feed" if fault == "invalid" else "finish")
    )
    return isinstance(records, list) and any(
        isinstance(item, dict) and item.get("stream_error") == category and item.get("operation") == operation
        for item in records
    )


def native_partial_failure(text):
    return (
        "Agent terminated early due to an API error" in text
        and "agent did NOT finish its task" in text
        and "P05_PARTIAL_NOT_SUCCESS" in text
        and parallel.FINALS[0] not in text
    )


def verified_lifecycle(client, stdout, scenario):
    if not (client == "claude" and scenario.native_partial_handback):
        return parallel.completed_lifecycle(client, stdout, failure=True, failure_match=scenario.expected_failure)
    try:
        result = json.loads(stdout)
    except ValueError:
        return False
    if not isinstance(result, dict):
        return False
    stats = result.get("subagent_stats", {})
    # Native Claude counts a recovered PARTIAL hand-back as completed. Require
    # explicit early-termination evidence in A's own result, never accept the
    # counters or the partial model text alone as proof of safe failure delivery.
    return (
        isinstance(stats, dict)
        and result.get("is_error") is False
        and result.get("permission_denials") == []
        and all(type(stats.get(key)) is int for key in ("spawned", "completed", "failed"))
        and stats.get("spawned") == stats.get("completed") == 2
        and stats.get("failed") == 0
        and scenario.parent_failure_correlated
    )


class FaultReply(io.BytesIO):
    def __init__(self, scenario, request):
        super().__init__()
        self.scenario, self.request = scenario, request

    def write_to(self, handler):
        state = self.scenario
        streaming = json.loads(self.request.data).get("stream") is True
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
        handler.send_header("x-should-retry", "false")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.close_connection = True
        handler.wfile.write(fault_prefix(state.protocol) if streaming else b'{"id":')
        if state.fault == "invalid":
            handler.wfile.write(b"event: invalid\ndata: {not-json}\n\n" if streaming else b"invalid-json}")
        handler.wfile.flush()
        state.fault_sent.set()
        if state.fault == "timeout":
            self.request.fixture_connection.settimeout(10)
            try:
                if self.request.fixture_connection.recv(1):
                    raise ValueError("Unexpected data on stalled upstream")
            except ConnectionResetError:
                pass
            state.timeout_disconnected.set()


class Scenario(parallel.Scenario):
    def __init__(self, client, protocol, fault):
        super().__init__(client, protocol, failure=True)
        self.fault = fault
        self.fault_sent = threading.Event()
        self.timeout_disconnected = threading.Event()
        self.failure_seen = False
        self.capture_trace = True
        self.disable_claude_retries = True
        self.trace_data = []
        self.retry_shapes = []
        self.parent_failure_correlated = False
        self.native_partial_handback = False

    def open(self, request, *, timeout):
        body = json.loads(request.data)
        expected_path = {
            Protocol.CHAT: "/v1/chat/completions",
            Protocol.MESSAGES: "/v1/messages",
            Protocol.RESPONSES: "/v1/responses",
        }[self.protocol]
        if body.get("model") != "p04-fixture-model" or urlsplit(request.full_url).path != expected_path:
            raise ValueError("Unexpected fault scenario endpoint/model")
        text = single.user_text(body, self.protocol)
        if self.client == "claude" and not any(task in text for task in parallel.TASKS):
            records = dict(single.tool_records(body, self.protocol))
            if self.protocol == Protocol.MESSAGES:
                for message in body.get("messages", []):
                    if isinstance(message.get("content"), list):
                        for block in message["content"]:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                records[block["tool_use_id"]] = block.get("content")
            self.parent_failure_correlated |= self.expected_failure(
                json.dumps(records.get("spawn_0"))
            ) and parallel.FINALS[1] in json.dumps(records.get("spawn_1"))
            self.native_partial_handback |= self.protocol == Protocol.MESSAGES and native_partial_failure(
                json.dumps(records.get("spawn_0"))
            )
        if parallel.TASKS[0] in text and self.stages["a"]:
            if self.stages["a"] >= MAX_FAULT_ATTEMPTS:
                raise ValueError("Faulting child exceeded bounded retry allowance")
            self.stages["a"] += 1
            self.retry_shapes.append({"stream": body.get("stream") is True, "attempt": self.stages["a"]})
            return self.reject_child(request)
        return super().open(request, timeout=timeout)

    def reject_child(self, request):
        return FaultReply(self, request)

    def expected_failure(self, text):
        # The actual harness spelling is checked in the acceptance gate too.
        markers = (
            "progress_timeout",
            "progress deadline",
            "timed out",
            "stream closed",
            "disconnected before completion",
            "API Error: 502",
            "API Error: 504",
            "upstream stream error",
            "invalid_stream",
        )
        found = (
            any(marker.lower() in text.lower() for marker in markers)
            or self.protocol == Protocol.MESSAGES
            and native_partial_failure(text)
        )
        self.failure_seen |= found
        return found


def run(client, upstream, adapter_source, fault):
    scenario = Scenario(client, upstream, fault)
    driver = parallel.drive(
        scenario, adapter_source, gateway_limits={"timeout": 15, "progress_timeout": 2, "total_timeout": 30}
    )
    result = scenario.result
    lifecycle = result is not None and verified_lifecycle(client, result.stdout, scenario)
    trace_verified = verified_trace(scenario.trace_data, fault)
    passed = (
        result is not None
        and result.returncode == 0
        and parallel.DONE in result.stdout
        and lifecycle
        and scenario.failure_seen
        and trace_verified
        and scenario.fault_sent.is_set()
        and scenario.peak == 2
        and 1 <= scenario.stages["a"] <= MAX_FAULT_ATTEMPTS
        and scenario.stages["b"] == 2
        and not scenario.errors
        and (fault != "timeout" or scenario.timeout_disconnected.is_set())
        and (scenario.audit_count == 1 if client == "codex" else scenario.parent_failure_correlated)
    )
    return {
        "passed": passed,
        "client": client,
        "upstream": upstream.value,
        "fault": fault,
        "cli_version": driver["cli_version"],
        "lifecycle_verified": lifecycle,
        "failure_seen": scenario.failure_seen,
        "fault_sent": scenario.fault_sent.is_set(),
        "timeout_disconnected": scenario.timeout_disconnected.is_set(),
        "fixture_errors": scenario.errors,
        "peak_overlapping_child_inference": scenario.peak,
        "mcp_audit_count": scenario.audit_count,
        "fault_attempts": scenario.stages["a"],
        "retry_shapes": scenario.retry_shapes,
        "trace": scenario.trace_data,
        "trace_verified": trace_verified,
        "parent_failure_correlated": scenario.parent_failure_correlated,
        "native_partial_handback": scenario.native_partial_handback,
        "observed": scenario.observed,
        "returncode": result.returncode if result else None,
        "stdout": result.stdout if result else "",
        "stderr": result.stderr if result else "",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex", "claude"], required=True)
    parser.add_argument("--upstream", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--fault", choices=["timeout", "truncated", "invalid"], required=True)
    parser.add_argument("--adapter-source", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = run(args.client, args.upstream, args.adapter_source, args.fault)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"stdout", "stderr", "trace", "observed", "parent_records"}
            },
            indent=2,
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
