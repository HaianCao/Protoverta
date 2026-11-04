"""Real parent/two-child SSE frames interleaved with colliding tool-call IDs."""

from __future__ import annotations

import argparse
import io
import json
import threading
from pathlib import Path
from urllib.parse import urlsplit

import smoke_agent_cancel as cancel
import smoke_parallel_agents as parallel
import smoke_subagents as single
from smoke_tool_search import functions

from protoverta.core import Protocol
from protoverta.namespaces import NamespaceMap
from protoverta.sse import SSEParser, encode_sse

DONE = "P05_THREE_STREAMS_VERIFIED"
SHARED = "same_three_lane_read"


class Interleaved(io.BytesIO):
    def __init__(self, wire, scenario, role):
        super().__init__(wire)
        self.scenario, self.role = scenario, role

    def write_to(self, handler):
        state = self.scenario
        frames = [encode_sse(event.event, event.data) for event in SSEParser().feed(self.getvalue())]
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.close_connection = True
        for index, frame in enumerate(frames):
            state.stream_barrier.wait(6)
            handler.wfile.write(frame)
            handler.wfile.flush()
            with state.lock:
                state.frames.append({"lane": self.role, "frame": index})
            state.stream_barrier.wait(6)


class Scenario(parallel.Scenario):
    def __init__(self, client, protocol):
        super().__init__(client, protocol)
        self.stream_barrier = threading.Barrier(3)
        self.frames = []
        self.reads = set()
        self.children_done = threading.Event()
        self.notifications = False
        self.ids = None

    def prepare(self, root):
        for role in ("parent", "a", "b"):
            (root / (role + ".txt")).write_text(single.VALUE + "_" + role, encoding="utf-8")

    def open(self, request, *, timeout):
        body = json.loads(request.data)
        expected = {Protocol.CHAT: "/v1/chat/completions", Protocol.MESSAGES: "/v1/messages", Protocol.RESPONSES: "/v1/responses"}[self.protocol]
        if body.get("model") != "p04-fixture-model" or urlsplit(request.full_url).path != expected:
            raise ValueError("Three-stream endpoint/model mismatch")
        text = single.user_text(body, self.protocol)
        role = "a" if parallel.TASKS[0] in text else "b" if parallel.TASKS[1] in text else "parent"
        records = dict(single.tool_records(body, self.protocol))
        tools = list(functions(body, self.protocol))
        with self.lock:
            self.stages[role] += 1
            stage = self.stages[role]
            if sum(self.stages.values()) > 16:
                raise ValueError("Three-stream request limit")
        spawn = single.find_tool(tools, "agent" if self.client == "claude" else "spawn_agent") if role == "parent" else None
        if role == "parent" and self.client == "claude" and stage == 1:
            return parallel.calls_wire(self.protocol, body["model"], [(spawn, {"prompt": task, "description": "Read own lane fixture", "subagent_type": "p04-worker", "run_in_background": True}, f"spawn_{i}") for i, task in enumerate(parallel.TASKS)])
        if role == "parent" and self.client == "codex" and stage <= 2:
            return parallel.calls_wire(self.protocol, body["model"], [(spawn, {"message": parallel.TASKS[stage - 1]}, f"spawn_{stage - 1}")])
        parent_read = 2 if self.client == "claude" else 3
        if role != "parent" and stage == 1 or role == "parent" and stage == parent_read:
            if role == "parent":
                self.ids = [cancel.task_identifier(records.get(f"spawn_{i}")) for i in range(2)]
            alias = NamespaceMap().alias("mcp__p04", "p04_read_fixture")
            tool = next(tool for tool in tools if tool.get("name") in {"Read", "p04_read_fixture", alias})
            arguments = {"file_path": str(self.root / (role + ".txt"))} if self.client == "claude" else {"lane": role}
            return Interleaved(parallel.calls_wire(self.protocol, body["model"], [(tool, arguments, SHARED)]).read(), self, role)
        if role not in self.reads:
            value = json.dumps(records.get(SHARED))
            if single.VALUE + "_" + role not in value or any(single.VALUE + "_" + other in value for other in {"parent", "a", "b"} - {role}):
                raise ValueError("Lane read result missing or mixed")
            with self.lock:
                self.reads.add(role)
        if role != "parent":
            if {"a", "b"} <= self.reads:
                self.children_done.set()
            return single.encode(self.protocol, body["model"], text=parallel.FINALS[0 if role == "a" else 1])
        if self.client == "claude":
            self.notifications = all(cancel.notification_evidence(body, identifier, marker)[0] for identifier, marker in zip(self.ids, parallel.FINALS, strict=True))
            if self.notifications:
                return single.encode(self.protocol, body["model"], text=DONE)
            if not self.children_done.wait(6):
                raise ValueError("Background children did not finish")
            return parallel.calls_wire(self.protocol, body["model"], [(single.find_tool(tools, "read"), {"file_path": str(self.root / "parent.txt")}, f"notification_{stage}")])
        returned = json.dumps(records)
        if all(marker in returned for marker in parallel.FINALS):
            return single.encode(self.protocol, body["model"], text=DONE)
        pending = [identifier for identifier, marker in zip(self.ids, parallel.FINALS, strict=True) if marker not in returned]
        return parallel.calls_wire(self.protocol, body["model"], [(single.find_tool(tools, "wait_agent"), {"targets": pending, "timeout_ms": 10000}, f"wait_{stage}")])


def verified_frames(frames):
    rounds = {}
    for item in frames:
        rounds.setdefault(item["frame"], []).append(item["lane"])
    indices = [item["frame"] for item in frames]
    return (len(rounds) >= 3 and sorted(rounds) == list(range(len(rounds))) and indices == sorted(indices)
            and all(sorted(lanes) == ["a", "b", "parent"] for lanes in rounds.values()))


def run(client, upstream, source):
    state = Scenario(client, upstream)
    driver = parallel.drive(state, source, mcp_lanes=True)
    result = state.result
    lifecycle = result is not None and parallel.completed_lifecycle(client, result.stdout)
    passed = (result is not None and result.returncode == 0 and DONE in result.stdout and lifecycle and verified_frames(state.frames)
              and state.reads == {"a", "b", "parent"} and state.stages["a"] == state.stages["b"] == 2
              and not state.errors and (state.audit_count == 3 if client == "codex" else state.notifications))
    return {"passed": passed, "client": client, "upstream": upstream.value, "cli_version": driver["cli_version"],
            "lifecycle_verified": lifecycle, "frames": state.frames, "verified_reads": sorted(state.reads),
            "fixture_errors": state.errors, "mcp_audit_count": state.audit_count,
            "stdout": result.stdout if result else "", "stderr": result.stderr if result else ""}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex", "claude"], required=True)
    parser.add_argument("--upstream", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--adapter-source", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = run(args.client, args.upstream, args.adapter_source)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"stdout", "stderr", "frames"}}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
