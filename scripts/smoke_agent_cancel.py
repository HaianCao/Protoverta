"""Cancel one real harness child while its sibling continues, loopback only."""

from __future__ import annotations

import argparse
import io
import json
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

import smoke_parallel_agents as parallel
import smoke_subagents as single
from smoke_tool_search import functions

from protoverta.core import Protocol
from protoverta.namespaces import NamespaceMap

DONE = "P05_CANCEL_PARENT_VERIFIED"


def result_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            item["text"] for item in value if isinstance(item, dict) and item.get("type") in {"text", "input_text"} and isinstance(item.get("text"), str)
        )
    return json.dumps(value)


def task_identifier(value):
    text = result_text(value)
    patterns = [
        r'"(?:task_id|agent_id|agentId)"\s*:\s*"([A-Za-z0-9_-]+)"',
        r"(?:task_id|agentId|agent_id):\s*([A-Za-z0-9_-]+)",
        r"<task_id>([A-Za-z0-9_-]+)</task_id>",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match[1]
    raise ValueError("No actual background task ID in Agent result")


def notification_evidence(body, task_id, marker):
    candidates = [
        (message.get("role"), result_text(message.get("content")))
        for message in body.get("messages", body.get("input", []))
        if isinstance(message, dict) and message.get("role") in {"user", "system", "developer"}
    ]
    for key in ("system", "instructions"):
        if key in body:
            candidates.append((key, result_text(body[key])))
    shapes = [
        {"role": role, "has_task_id": task_id in text, "has_result_marker": marker in text, "is_task_notification": "<task-notification>" in text}
        for role, text in candidates
        if marker in text
    ]
    return any(item["has_task_id"] and item["is_task_notification"] for item in shapes), shapes


class Scenario(parallel.Scenario):
    def __init__(self, client, protocol):
        super().__init__(client, protocol)
        self.started = set()
        self.both_started = threading.Event()
        self.cancelled = threading.Event()
        self.sibling_final = threading.Event()
        self.parent_results = []
        self.cancel_target = None
        self.sibling_target = None
        self.cancel_requested = threading.Event()
        self.sibling_waiting = threading.Event()
        self.sibling_alive_when_cancelled = False
        self.notification_seen = False
        self.notification_shape = []
        self.background_artifacts_owned = False

    def open(self, request, *, timeout):
        expected_path = {Protocol.CHAT: "/v1/chat/completions", Protocol.MESSAGES: "/v1/messages", Protocol.RESPONSES: "/v1/responses"}[self.protocol]
        if urlsplit(request.full_url).path != expected_path:
            raise ValueError("Unexpected upstream endpoint")
        body = json.loads(request.data)
        if body.get("model") != "p04-fixture-model":
            raise ValueError("Selected model changed")
        text = single.user_text(body, self.protocol)
        role = "a" if parallel.TASKS[0] in text else "b" if parallel.TASKS[1] in text else "parent"
        tools = list(functions(body, self.protocol))
        records = dict(single.tool_records(body, self.protocol))
        with self.lock:
            self.stages[role] += 1
            stage = self.stages[role]
            self.observed.append({"role": role, "stage": stage, "result_call_ids": sorted(records)})
            if len(self.observed) > 16:
                raise ValueError("Cancel scenario request limit exceeded")
        if role != "parent":
            if stage == 1:
                with self.lock:
                    self.started.add(role)
                    if role == "b":
                        self.sibling_waiting.set()
                    self.peak = max(self.peak, len(self.started))
                    if len(self.started) == 2:
                        self.both_started.set()
                if role == "a":
                    request.fixture_connection.settimeout(8)
                    try:
                        if request.fixture_connection.recv(1):
                            raise ValueError("Unexpected data instead of child cancellation")
                    except ConnectionResetError:
                        pass
                    if not self.cancel_requested.is_set():
                        raise ValueError("Child disconnected before the requested stop")
                    self.sibling_alive_when_cancelled = self.sibling_waiting.is_set() and not self.sibling_final.is_set()
                    self.cancelled.set()
                    return io.BytesIO(b"")  # No invented successful terminal for a cancelled generation.
                if not self.cancelled.wait(8):
                    raise ValueError("Sibling was not released by observed upstream disconnect")
                alias = NamespaceMap().alias("mcp__p04", "p04_read_fixture")
                tool = next(tool for tool in tools if tool.get("name") in {"Read", "p04_read_fixture", alias})
                args = {"file_path": str(self.root / "fixture.txt")} if self.client == "claude" else {}
                return parallel.calls_wire(self.protocol, body["model"], [(tool, args, "same_child_read")])
            if role == "b" and stage == 2 and single.VALUE in json.dumps(records.get("same_child_read")):
                self.sibling_final.set()
                return single.encode(self.protocol, body["model"], text=parallel.FINALS[1])
            raise ValueError("Cancelled child resumed or sibling read failed")
        self.parent_results.append(records)
        spawn = single.find_tool(tools, "agent" if self.client == "claude" else "spawn_agent")
        if stage == 1 and self.client == "claude":
            return parallel.calls_wire(
                self.protocol,
                body["model"],
                [
                    (
                        spawn,
                        {"prompt": task + " Read fixture.txt", "description": "Read local fixture", "subagent_type": "p04-worker", "run_in_background": True},
                        f"spawn_{index}",
                    )
                    for index, task in enumerate(parallel.TASKS)
                ],
            )
        if self.client == "codex" and stage <= 2:
            return parallel.calls_wire(
                self.protocol, body["model"], [(spawn, {"message": parallel.TASKS[stage - 1] + " Read fixture.txt"}, f"spawn_{stage - 1}")]
            )
        stop_stage = 2 if self.client == "claude" else 3
        if stage == stop_stage:
            if not self.both_started.wait(6):
                raise ValueError("Both children did not start before cancellation")
            self.cancel_target = task_identifier(records.get("spawn_0"))
            self.sibling_target = task_identifier(records.get("spawn_1"))
            if self.client == "claude":
                paths = [re.search(r"output_file:\s*([^\r\n]+)", result_text(records.get(f"spawn_{index}"))) for index in range(2)]
                self.background_artifacts_owned = all(match is not None and Path(match[1]).resolve().is_relative_to(self.root.resolve()) for match in paths)
                if not self.background_artifacts_owned:
                    raise ValueError("Background artifact path escaped the disposable probe root")
            stop = single.find_tool(tools, "taskstop" if self.client == "claude" else "close_agent")
            args = {"task_id": self.cancel_target} if self.client == "claude" else {"target": self.cancel_target}
            self.cancel_requested.set()
            return parallel.calls_wire(self.protocol, body["model"], [(stop, args, "cancel_a")])
        if not self.cancelled.wait(2):
            raise ValueError("Stop tool did not disconnect the requested upstream")
        if parallel.FINALS[1] in json.dumps(body):
            if self.client == "claude":
                self.notification_seen, self.notification_shape = notification_evidence(body, self.sibling_target, parallel.FINALS[1])
            return single.encode(self.protocol, body["model"], text=DONE)
        if self.client == "codex":
            target = single.agent_id([records.get("spawn_1")])
            return parallel.calls_wire(
                self.protocol, body["model"], [(single.find_tool(tools, "wait_agent"), {"targets": [target], "timeout_ms": 10000}, f"wait_b_{stage}")]
            )
        if not self.sibling_final.wait(6):
            raise ValueError("Sibling did not finish its inference after cancellation")
        # A local read gives the harness a tool boundary at which to deliver its
        # background completion notification. TaskOutput is not advertised here.
        return parallel.calls_wire(
            self.protocol, body["model"], [(single.find_tool(tools, "read"), {"file_path": str(self.root / "fixture.txt")}, f"parent_read_{stage}")]
        )


def verified_lifecycle(client, stdout, scenario):
    if client == "claude":
        try:
            result = json.loads(stdout)
            stop = json.loads(result_text(scenario.parent_results[-1].get("cancel_a")))
        except (ValueError, IndexError, TypeError):
            return False
        if not isinstance(result, dict) or not isinstance(stop, dict):
            return False
        stats = result.get("subagent_stats", {})
        killed = stats.get("killed") if isinstance(stats, dict) else None
        return (
            isinstance(stats, dict)
            and result.get("is_error") is False
            and result.get("permission_denials") == []
            and all(type(stats.get(key)) is int for key in ("spawned", "completed", "failed"))
            and stats.get("spawned") == 2
            and stats.get("completed") == 1
            and stats.get("failed") == 0
            and isinstance(killed, dict)
            and all(type(value) is int for value in killed.values())
            and killed == {"parent": 1, "user": 0, "system": 0}
            and stats.get("started_in_background") == 2
            and scenario.notification_seen
            and stop.get("task_id") == scenario.cancel_target
            and stop.get("task_type") == "local_agent"
            and str(stop.get("message", "")).startswith("Successfully stopped task:")
        )
    assigned, spawned, closed, waited = {}, set(), set(), {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if not isinstance(item, dict) or event.get("type") != "item.completed" or item.get("type") != "collab_tool_call" or item.get("status") != "completed":
            continue
        receivers = item.get("receiver_thread_ids", [])
        if not isinstance(receivers, list) or not all(isinstance(value, str) for value in receivers):
            return False
        if item.get("tool") == "spawn_agent" and len(receivers) == 1:
            spawned.update(receivers)
            for task in parallel.TASKS:
                if task in str(item.get("prompt")):
                    assigned[task] = receivers[0]
        elif item.get("tool") == "close_agent":
            closed.update(receivers)
        elif item.get("tool") == "wait" and isinstance(item.get("agents_states"), dict):
            for key, state in item["agents_states"].items():
                if isinstance(state, dict) and state.get("status") == "completed":
                    waited[key] = state.get("message")
    return (
        len(assigned) == 2
        and spawned == {scenario.cancel_target, scenario.sibling_target}
        and scenario.cancel_target != scenario.sibling_target
        and assigned.get(parallel.TASKS[0]) == scenario.cancel_target
        and assigned.get(parallel.TASKS[1]) == scenario.sibling_target
        and closed == {scenario.cancel_target}
        and waited == {scenario.sibling_target: parallel.FINALS[1]}
    )


def run(client, upstream, adapter_source):
    scenario = Scenario(client, upstream)
    driver = parallel.drive(scenario, adapter_source, claude_tools="Agent,Read,TaskStop")
    result = scenario.result
    lifecycle = result is not None and verified_lifecycle(client, result.stdout, scenario)
    passed = (
        result is not None
        and result.returncode == 0
        and DONE in result.stdout
        and lifecycle
        and scenario.cancelled.is_set()
        and scenario.sibling_alive_when_cancelled
        and scenario.sibling_final.is_set()
        and scenario.stages["a"] == 1
        and scenario.stages["b"] == 2
        and not scenario.errors
        and (scenario.audit_count == 1 if client == "codex" else scenario.background_artifacts_owned)
    )
    return {
        "passed": passed,
        "client": client,
        "upstream": upstream.value,
        "cli_version": driver["cli_version"],
        "cancelled_upstream": scenario.cancelled.is_set(),
        "sibling_final_inference": scenario.sibling_final.is_set(),
        "lifecycle_verified": lifecycle,
        "sibling_alive_when_cancelled": scenario.sibling_alive_when_cancelled,
        "background_notification_seen": scenario.notification_seen,
        "mcp_audit_count": scenario.audit_count,
        "background_artifacts_owned": scenario.background_artifacts_owned,
        "notification_shape": scenario.notification_shape,
        "fixture_errors": scenario.errors,
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
    args = parser.parse_args()
    result = run(args.client, args.upstream, args.adapter_source)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if not result["passed"] or key not in {"stdout", "stderr"}}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
