"""Normal parent exit or real Windows Ctrl+C with an active real child."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import smoke_agent_cancel as cancel
import smoke_clients
import smoke_parallel_agents as parallel
import smoke_subagents as single
from smoke_tool_search import functions

from protoverta.core import Protocol

TASK = "P05_EXIT_CHILD_TASK"
DONE = "P05_EXIT_PARENT_VERIFIED"
CHILD_DONE = "P05_EXIT_CHILD_VERIFIED"


class WireReply(io.BytesIO):
    def __init__(self, wire, state, *, child=False):
        super().__init__(wire)
        self.state, self.child = state, child

    def write_to(self, handler):
        wire = self.getvalue()
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Content-Length", str(len(wire)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(wire)
        handler.wfile.flush()
        handler.close_connection = True
        if not self.child:
            self.state.parent_final_sent.set()
            return
        handler.connection.settimeout(6)
        try:
            if handler.connection.recv(1):
                raise ValueError("Unexpected data after completed child stream")
        except ConnectionResetError:
            pass
        self.state.mark_disconnected()


class Scenario(parallel.Scenario):
    def __init__(self, client, protocol, mode):
        super().__init__(client, protocol)
        self.mode = mode
        self.child_started = threading.Event()
        self.exit_allowed = threading.Event()
        self.disconnected = threading.Event()
        self.child_id = None
        self.console = {}
        self.parent_final_sent = threading.Event()
        self.child_completed = False
        self.background_artifact_owned = False
        self.completion_notification = False
        self.stages = {"parent": 0, "child": 0}

    def mark_disconnected(self):
        self.disconnected.set()
        (self.root / "child-disconnected").write_text("observed", encoding="utf-8")

    def open(self, request, *, timeout):
        body = json.loads(request.data)
        expected = {Protocol.CHAT: "/v1/chat/completions", Protocol.MESSAGES: "/v1/messages", Protocol.RESPONSES: "/v1/responses"}[self.protocol]
        if body.get("model") != "p04-fixture-model" or urlsplit(request.full_url).path != expected:
            raise ValueError("Unexpected lifecycle endpoint/model")
        role = "child" if TASK in single.user_text(body, self.protocol) else "parent"
        with self.lock:
            self.stages[role] += 1
            stage = self.stages[role]
            self.observed.append({"role": role, "stage": stage})
        draining = self.client == "claude" and self.mode == "normal"
        max_stage = (3 if draining else 2) if role == "parent" else (2 if draining else 1)
        if stage > max_stage:
            raise ValueError("Lifecycle fixture exceeded inference budget")
        if role == "child":
            self.child_started.set()
            if self.client == "claude" and self.mode == "normal":
                if stage == 1:
                    if not self.parent_final_sent.wait(6):
                        raise ValueError("Claude child did not wait for parent final response")
                    tool = single.find_tool(list(functions(body, self.protocol)), "read")
                    return parallel.calls_wire(self.protocol, body["model"], [(tool, {"file_path": str(self.root / "fixture.txt")}, "exit_child_read")])
                records = dict(single.tool_records(body, self.protocol))
                if single.VALUE not in json.dumps(records.get("exit_child_read")):
                    raise ValueError("Background child read was not replayed")
                self.child_completed = True
                return WireReply(single.encode(self.protocol, body["model"], text=CHILD_DONE).read(), self, child=True)
            request.fixture_connection.settimeout(20)
            try:
                if request.fixture_connection.recv(1):
                    raise ValueError("Unexpected data instead of upstream disconnect")
            except ConnectionResetError:
                pass
            if not self.exit_allowed.is_set():
                raise ValueError("Child disconnected before parent exit boundary")
            self.mark_disconnected()
            return io.BytesIO(b"")
        tools = list(functions(body, self.protocol))
        if stage == 1:
            tool = single.find_tool(tools, "agent" if self.client == "claude" else "spawn_agent")
            args = (
                {"prompt": TASK, "description": "Wait in local fixture", "subagent_type": "p04-worker", "run_in_background": True}
                if self.client == "claude"
                else {"message": TASK}
            )
            return parallel.calls_wire(self.protocol, body["model"], [(tool, args, "spawn_exit_child")])
        if stage == 3:
            self.completion_notification, _ = cancel.notification_evidence(body, self.child_id, CHILD_DONE)
            if not self.child_completed or not self.completion_notification:
                raise ValueError("Parent continuation did not receive its background result")
            return single.encode(self.protocol, body["model"], text=DONE)
        records = dict(single.tool_records(body, self.protocol))
        self.child_id = cancel.task_identifier(records.get("spawn_exit_child"))
        if self.client == "claude":
            match = re.search(r"output_file:\s*([^\r\n]+)", cancel.result_text(records.get("spawn_exit_child")))
            self.background_artifact_owned = match is not None and Path(match[1]).resolve().is_relative_to(self.root.resolve())
            if not self.background_artifact_owned:
                raise ValueError("Background output escaped the owned root")
        if not self.child_started.wait(6):
            raise ValueError("Parent exit happened before actual child inference")
        self.exit_allowed.set()
        (self.root / "parent-exit-ready").write_text("ready", encoding="utf-8")
        if self.mode == "interrupt":
            request.fixture_connection.settimeout(15)
            try:
                if request.fixture_connection.recv(1):
                    raise ValueError("Unexpected data on interrupted parent request")
            except ConnectionResetError:
                pass
            return io.BytesIO(b"")
        wire = single.encode(self.protocol, body["model"], text=DONE)
        return WireReply(wire.read(), self) if self.client == "claude" else wire


def verified_cleanup(console, mode):
    return (
        isinstance(console, dict)
        and console.get("timed_out") is False
        and type(console.get("remaining_before_cleanup")) is int
        and console["remaining_before_cleanup"] == 0
        and console.get("upstream_closed_before_cleanup") is True
        and type(console.get("returncode")) is int
        and console["returncode"] == (130 if mode == "interrupt" else 0)
        and (mode != "interrupt" or console.get("event_sent") is True and console.get("console_owned") is True)
    )


def verified_lifecycle(client, stdout, child_id, mode):
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
            and all(type(stats.get(key)) is int for key in ("spawned", "completed", "failed", "started_in_background"))
            and stats.get("spawned") == stats.get("started_in_background") == 1
            and stats.get("failed") == 0
            and stats.get("completed") == (0 if mode == "interrupt" else 1)
            and result.get("permission_denials") == []
            and result.get("is_error") is (mode == "interrupt")
            and (mode != "interrupt" or result.get("terminal_reason") == "aborted_streaming")
        )
    spawned = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if isinstance(item, dict) and event.get("type") == "item.completed" and item.get("type") == "collab_tool_call" and item.get("tool") == "spawn_agent":
            if item.get("status") != "completed" or TASK not in str(item.get("prompt")):
                return False
            spawned.append(item.get("receiver_thread_ids"))
    return spawned == [[child_id]]


def run(client, upstream, adapter_source, mode):
    if os.name != "nt":
        raise RuntimeError("This gate requires Windows; no simulated signal accepted")
    state = Scenario(client, upstream, mode)
    original = smoke_clients.run_process

    def process(args, **kwargs):
        if not any(item in args for item in ("codex_adapter.cli", "claudapter.cli")):
            return original(args, **kwargs)
        root = Path(kwargs["cwd"])
        path = root / "console-input.json"
        path.write_text(json.dumps({"args": args, "env": kwargs["env"], "cwd": str(root), "mode": mode}), encoding="utf-8")
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0  # Hidden console, independent of any user terminal.
        options = {**kwargs, "timeout": 40, "creationflags": subprocess.CREATE_NEW_CONSOLE, "startupinfo": startup}
        interpreter = getattr(sys, "_base_executable", sys.executable)
        result = original([interpreter, str(Path(__file__).with_name("probe_console.py")), str(path)], **options)
        report = root / "console-result.json"
        state.console = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
        return result

    with patch.object(smoke_clients, "run_process", process):
        driver = parallel.drive(state, adapter_source)
    result = state.result
    cleanup = verified_cleanup(state.console, mode)
    lifecycle = result is not None and verified_lifecycle(client, result.stdout, state.child_id, mode)
    expected_child = 2 if client == "claude" and mode == "normal" else 1
    expected_parent = 3 if client == "claude" and mode == "normal" else 2
    passed = (
        result is not None
        and cleanup
        and lifecycle
        and result.returncode == state.console.get("returncode")
        and state.child_id
        and state.stages == {"parent": expected_parent, "child": expected_child}
        and state.disconnected.is_set()
        and not state.errors
        and state.audit_count == 0
        and (client != "claude" or state.background_artifact_owned)
        and (client != "claude" or mode != "normal" or state.child_completed and state.completion_notification)
        and (mode == "interrupt" or result.returncode == 0 and DONE in result.stdout)
    )
    return {
        "passed": bool(passed),
        "client": client,
        "upstream": upstream.value,
        "mode": mode,
        "cli_version": driver["cli_version"],
        "cleanup_verified": cleanup,
        "console": state.console,
        "lifecycle_verified": lifecycle,
        "child_started": state.child_started.is_set(),
        "child_disconnected": state.disconnected.is_set(),
        "child_completed": state.child_completed,
        "background_artifact_owned": state.background_artifact_owned,
        "completion_notification": state.completion_notification,
        "fixture_errors": state.errors,
        "observed": state.observed,
        "stdout": result.stdout if result else "",
        "stderr": result.stderr if result else "",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex", "claude"], required=True)
    parser.add_argument("--upstream", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--mode", choices=["normal", "interrupt"], required=True)
    parser.add_argument("--adapter-source", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = run(args.client, args.upstream, args.adapter_source, args.mode)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"stdout", "stderr"}}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
