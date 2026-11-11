"""Two real harness children against a keyless loopback fixture, isolated state."""

from __future__ import annotations

import argparse
import io
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import Request

import smoke_clients
import smoke_subagents as single
from smoke_tool_search import functions

from protoverta.core import Protocol
from protoverta.namespaces import NamespaceMap
from protoverta.sse import encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta

TASKS = ("P05_CHILD_A_TASK", "P05_CHILD_B_TASK")
FINALS = ("P05_CHILD_A_VERIFIED", "P05_CHILD_B_VERIFIED")
DONE = "P05_PARALLEL_PARENT_VERIFIED"
FAILURE = "P05_EXPECTED_CHILD_FAILURE"


def expected_rejection(text):
    # Codex 0.166.0 reports the status but replaces the upstream 429 body.
    return FAILURE in text or "429 Too Many Requests" in text


class ExpectedRejection(Exception):
    pass


@contextmanager
def fixture_server(scenario):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            if (
                self.headers.get("Authorization") != "Bearer fake-upstream-key"
                and self.headers.get("x-api-key") != "fake-upstream-key"
            ):
                self.send_error(401)
                return
            try:
                length = int(self.headers["Content-Length"])
                if not 0 <= length <= 4 * 1024 * 1024:
                    raise ValueError("Fixture request size limit")
                request = Request("http://127.0.0.1" + self.path, data=self.rfile.read(length))
                request.fixture_connection = self.connection
                with scenario.open(request, timeout=10) as stream:
                    if hasattr(stream, "write_to"):
                        stream.write_to(self)
                        return
                    wire = stream.read()
                status = 200
            except ExpectedRejection:
                scenario.rejections += 1
                wire = json.dumps({"error": {"message": FAILURE, "type": "rate_limit_error"}}).encode()
                status = 429
            except Exception as exc:  # noqa: BLE001 -- report only the fixture error shape
                scenario.errors.append(type(exc).__name__)
                wire = b'{"error":{"message":"Synthetic fixture rejected an unexpected request"}}'
                status = 422
            try:
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream" if status == 200 else "application/json")
                self.send_header("Content-Length", str(len(wire)))
                self.send_header("x-should-retry", "false")
                self.end_headers()
                self.wfile.write(wire)
            except OSError:
                pass

    listener = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    listener.daemon_threads = True
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{listener.server_port}"
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(2)


def calls_wire(protocol, model, calls):
    encoder = StreamEncoder(protocol)
    deltas = [Delta("start", model=model, input_tokens=1, output_tokens=0)]
    namespaces = {}
    for index, (tool, args, call_id) in enumerate(calls):
        if tool.get("namespace"):
            namespaces[call_id] = tool["namespace"]
        deltas += [
            Delta("tool_start", index=index, name=tool["name"], call_id=call_id),
            Delta("tool_delta", index=index, text=json.dumps(args)),
            Delta("tool_end", index=index),
        ]
    deltas.append(Delta("finish", reason="tool_calls", input_tokens=1, output_tokens=1))
    wire = []
    for delta in deltas:
        for event in encoder.feed(delta):
            data = event.data
            if protocol == Protocol.RESPONSES:
                data = json.loads(data)
                for item in [data.get("item", {}), *data.get("response", {}).get("output", [])]:
                    if item.get("type") == "function_call" and item.get("call_id") in namespaces:
                        item["namespace"] = namespaces[item["call_id"]]
                data = json.dumps(data)
            wire.append(encode_sse(event.event, data))
    return io.BytesIO(b"".join(wire))


def completed_lifecycle(client, stdout, *, failure=False, failure_match=expected_rejection):
    if client == "claude":
        try:
            result = json.loads(stdout)
        except ValueError:
            return False
        if not isinstance(result, dict):
            return False
        stats = result.get("subagent_stats", {})
        return (
            isinstance(stats, dict)
            and all(type(stats.get(key)) is int for key in ("spawned", "completed", "failed"))
            and result.get("is_error") is False
            and isinstance(result.get("permission_denials"), list)
            and not result["permission_denials"]
            and stats.get("spawned") == 2
            and stats.get("completed") == (1 if failure else 2)
            and stats.get("failed") == (1 if failure else 0)
        )
    children, assigned, waited, failed = set(), {}, {}, set()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if (
            not isinstance(item, dict)
            or event.get("type") != "item.completed"
            or item.get("type") != "collab_tool_call"
            or item.get("status") not in ({"completed", "failed"} if failure else {"completed"})
        ):
            continue
        if item.get("tool") == "spawn_agent":
            receivers = item.get("receiver_thread_ids", [])
            if not isinstance(receivers, list) or not all(isinstance(value, str) for value in receivers):
                return False
            children.update(receivers)
            for task in TASKS:
                if task in str(item.get("prompt")) and len(receivers) == 1:
                    assigned[task] = receivers[0]
        if item.get("tool") == "wait" and isinstance(item.get("agents_states"), dict):
            for key, state in item["agents_states"].items():
                if (
                    isinstance(state, dict)
                    and state.get("status") == "completed"
                    and isinstance(state.get("message"), str)
                ):
                    waited[key] = state.get("message")
                elif (
                    isinstance(state, dict)
                    and state.get("status") == "errored"
                    and failure_match(str(state.get("message")))
                ):
                    failed.add(key)
    if len(assigned) != 2 or len(children) != 2 or children != set(assigned.values()):
        return False
    if failure:
        return failed == {assigned[TASKS[0]]} and waited == {assigned[TASKS[1]]: FINALS[1]}
    return waited == {assigned[task]: marker for task, marker in zip(TASKS, FINALS, strict=True)}


class Scenario:
    def __init__(self, client, protocol, failure=False):
        self.client, self.protocol = client, protocol
        self.failure, self.errors = failure, []
        self.rejections = 0
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(2)
        self.stages = {"parent": 0, "a": 0, "b": 0}
        self.active, self.peak = set(), 0
        self.observed = []
        self.root = None
        self.result = None
        self.audit_count = 0

    def expected_failure(self, text):
        return expected_rejection(text)

    def reject_child(self, request):
        raise ExpectedRejection

    def open(self, request, *, timeout):
        expected_path = {
            Protocol.CHAT: "/v1/chat/completions",
            Protocol.RESPONSES: "/v1/responses",
            Protocol.MESSAGES: "/v1/messages",
        }[self.protocol]
        if urlsplit(request.full_url).path != expected_path:
            raise ValueError("Unexpected upstream endpoint")
        body = json.loads(request.data)
        if body.get("model") != "p04-fixture-model":
            raise ValueError("Harness changed the selected model")
        text = single.user_text(body, self.protocol)
        role = "a" if TASKS[0] in text else "b" if TASKS[1] in text else "parent"
        tools = list(functions(body, self.protocol))
        records = dict(single.tool_records(body, self.protocol))
        if role == "parent" and self.protocol == Protocol.MESSAGES:
            # Retain explicit failed Agent results for this negative scenario;
            # they are never treated as successful child read results.
            for message in body.get("messages", []):
                if isinstance(message.get("content"), list):
                    for block in message["content"]:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            records[block["tool_use_id"]] = block.get("content")
        with self.lock:
            self.stages[role] += 1
            stage = self.stages[role]
            self.observed.append(
                {"role": role, "stage": stage, "model": body["model"], "result_call_ids": sorted(records)}
            )
            if len(self.observed) > 16:
                raise ValueError("Bounded fixture inference budget exhausted")
        if role != "parent":
            index = 0 if role == "a" else 1
            if stage == 1:
                with self.lock:
                    self.active.add(role)
                    self.peak = max(self.peak, len(self.active))
                try:
                    self.barrier.wait(timeout=5)  # Serial children must fail, never look concurrent.
                    if self.failure and role == "a":
                        return self.reject_child(request)
                    alias = NamespaceMap().alias("mcp__p04", "p04_read_fixture")
                    tool = next(tool for tool in tools if tool.get("name") in {"Read", "p04_read_fixture", alias})
                    args = {"file_path": str(self.root / "fixture.txt")} if self.client == "claude" else {}
                    # Deliberately reuse this ID across different child requests.
                    return calls_wire(self.protocol, body["model"], [(tool, args, "same_child_read")])
                finally:
                    with self.lock:
                        self.active.discard(role)
            if stage == 2 and single.VALUE in json.dumps(records.get("same_child_read")):
                return single.encode(self.protocol, body["model"], text=FINALS[index])
            raise ValueError("Child failed its independently correlated read round trip")
        spawn = single.find_tool(tools, "agent" if self.client == "claude" else "spawn_agent")
        if self.client == "claude" and stage == 1:
            return calls_wire(
                self.protocol,
                body["model"],
                [
                    (
                        spawn,
                        {
                            "prompt": task + " Read " + str(self.root / "fixture.txt"),
                            "description": "Read local fixture",
                            "subagent_type": "p04-worker",
                            "run_in_background": False,
                        },
                        f"spawn_{index}",
                    )
                    for index, task in enumerate(TASKS)
                ],
            )
        if self.client == "codex" and stage <= 2:
            return calls_wire(
                self.protocol,
                body["model"],
                [(spawn, {"message": TASKS[stage - 1] + " Read the local fixture"}, f"spawn_{stage - 1}")],
            )
        returned = json.dumps(records)
        complete = (
            (self.expected_failure(returned) and FINALS[1] in returned)
            if self.failure
            else all(marker in returned for marker in FINALS)
        )
        if complete:
            return single.encode(self.protocol, body["model"], text=DONE)
        if self.client == "codex":
            identifiers = [single.agent_id([records.get(f"spawn_{index}")]) for index in range(2)]
            pending = [
                identifier
                for index, identifier in enumerate(identifiers)
                if FINALS[index] not in returned
                and not (self.failure and index == 0 and self.expected_failure(returned))
            ]
            return calls_wire(
                self.protocol,
                body["model"],
                [(single.find_tool(tools, "wait_agent"), {"targets": pending, "timeout_ms": 10000}, f"wait_{stage}")],
            )
        raise ValueError("Parent did not replay both verified child results")


def drive(
    scenario,
    adapter_source,
    *,
    claude_tools=None,
    claude_agent_tools=None,
    claude_depth=None,
    gateway_limits=None,
    mcp_lanes=False,
    claude_isolation=None,
    child_model=None,
):
    """Run an isolated scenario; acceptance belongs to the calling gate."""
    client, upstream = scenario.client, scenario.protocol
    original_client = smoke_clients.run_client

    def fixture(_single_child_callback):
        return fixture_server(scenario)

    def invoke(*args, **kwargs):
        scenario.root = Path(args[6])
        if hasattr(scenario, "prepare"):
            scenario.prepare(scenario.root)
        if getattr(scenario, "disable_claude_retries", False):
            args[5]["CLAUDE_CODE_MAX_RETRIES"] = "0"
        options = dict(kwargs.get("codex_config") or {})
        options["agents.max_concurrent_threads_per_session"] = 2
        kwargs.update(
            codex_config=options,
            prompt="Explicitly delegate two independent read-only tasks to two subagents, wait for both, then summarize.",
        )
        if getattr(scenario, "capture_trace", False):
            kwargs["trace_file"] = scenario.root / "fault-trace.json"
        result = original_client(*args, **kwargs)
        scenario.result = result
        if getattr(scenario, "capture_trace", False):
            path = scenario.root / "fault-trace.json"
            scenario.trace_data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        audit = scenario.root / "mcp-audit.txt"
        scenario.audit_count = len(audit.read_text(encoding="utf-8").splitlines()) if audit.exists() else 0
        return result

    # Reuse P04's isolated catalog/config/process ownership; replace only its
    # single-child scenario and acceptance. No production monkeypatch or flag.
    with patch.object(smoke_clients, "fixture_server", fixture), patch.object(smoke_clients, "run_client", invoke):
        driver = single.run(
            client,
            upstream,
            adapter_source=adapter_source,
            claude_tools=claude_tools,
            claude_agent_tools=claude_agent_tools,
            claude_depth=claude_depth,
            gateway_limits=gateway_limits,
            mcp_lanes=mcp_lanes,
            claude_isolation=claude_isolation,
            child_model=child_model,
        )
    return driver


def run(client, upstream, adapter_source, *, failure=False):
    scenario = Scenario(client, upstream, failure)
    driver = drive(scenario, adapter_source)
    result = scenario.result
    lifecycle = result is not None and completed_lifecycle(client, result.stdout, failure=failure)
    passed = (
        result is not None
        and result.returncode == 0
        and DONE in result.stdout
        and lifecycle
        and scenario.peak == 2
        and scenario.stages["a"] == (1 if failure else 2)
        and scenario.stages["b"] == 2
        and not scenario.errors
        and scenario.rejections == (1 if failure else 0)
        and (client != "codex" or scenario.audit_count == (1 if failure else 2))
    )
    return {
        "passed": passed,
        "client": client,
        "upstream": upstream.value,
        "failure": failure,
        "fixture_errors": scenario.errors,
        "expected_upstream_rejections": scenario.rejections,
        "cli_version": driver["cli_version"],
        "peak_overlapping_child_inference": scenario.peak,
        "lifecycle_verified": lifecycle,
        "mcp_audit_count": scenario.audit_count,
        "observed": scenario.observed,
        "returncode": result.returncode if result else None,
        "stdout": result.stdout if result else "",
        "stderr": result.stderr if result else "",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex", "claude"], required=True)
    parser.add_argument("--upstream", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--adapter-source", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--failure-429", action="store_true")
    args = parser.parse_args()
    result = run(args.client, args.upstream, args.adapter_source, failure=args.failure_429)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in result.items() if not result["passed"] or key not in {"stdout", "stderr"}},
            indent=2,
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
