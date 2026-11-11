"""Exact parent/child model selection against synthetic loopback providers."""

from __future__ import annotations

import argparse
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

MAIN = "p04-fixture-model"
CHILD = "p06-fixture-child"
TASK = "P06_CHILD_TASK"
FIRST = "P06_CHILD_FIRST_VERIFIED"
RESUME_TASK = "P06_RESUME_SAME_CHILD"
RESUMED = "P06_CHILD_RESUMED_VERIFIED"
DONE = "P06_PARENT_VERIFIED"


class Scenario(parallel.Scenario):
    def __init__(self, client, protocol, *, closed_resume=False):
        super().__init__(client, protocol)
        self.stages = {"parent": 0, "child": 0}
        self.child_id = None
        self.schemas = {}
        self.phase = "first"
        self.resumed = threading.Event()
        self.first_done = threading.Event()
        self.history_verified = False
        self.notification_verified = False
        self.rejected_models = []
        self.closed_resume = closed_resume
        self.resume_error = None

    def open(self, request, *, timeout):
        body = json.loads(request.data)
        expected = {
            Protocol.CHAT: "/v1/chat/completions",
            Protocol.MESSAGES: "/v1/messages",
            Protocol.RESPONSES: "/v1/responses",
        }[self.protocol]
        text = single.user_text(body, self.protocol)
        role = "child" if TASK in text or RESUME_TASK in text else "parent"
        if urlsplit(request.full_url).path != expected or body.get("model") != (MAIN if role == "parent" else CHILD):
            self.rejected_models.append({"role": role, "model": body.get("model")})
            raise ValueError("Auxiliary model or endpoint changed/fell back")
        tools, records = list(functions(body, self.protocol)), dict(single.tool_records(body, self.protocol))
        with self.lock:
            self.stages[role] += 1
            stage = self.stages[role]
            self.observed.append({"role": role, "stage": stage, "model": body["model"], "call_ids": sorted(records)})
            if sum(self.stages.values()) > 18:
                raise ValueError("Auxiliary fixture request budget")
        if role == "child":
            if stage == 1:
                alias = NamespaceMap().alias("mcp__p04", "p04_read_fixture")
                tool = next(tool for tool in tools if tool.get("name") in {"Read", "p04_read_fixture", alias})
                args = {"file_path": str(self.root / "fixture.txt")} if self.client == "claude" else {}
                return parallel.calls_wire(self.protocol, CHILD, [(tool, args, "p06_child_read")])
            if stage == 2 and single.VALUE in json.dumps(records.get("p06_child_read")):
                self.first_done.set()
                return single.encode(self.protocol, CHILD, text=FIRST)
            if (
                stage == 3
                and RESUME_TASK in json.dumps(body)
                and FIRST in json.dumps(body)
                and single.VALUE in json.dumps(body)
            ):
                self.history_verified = True
                self.resumed.set()
                return single.encode(self.protocol, CHILD, text=RESUMED)
            raise ValueError("Child model read failed")
        if stage == 1:
            names = {
                NamespaceMap().alias("multi_agent_v1", name)
                for name in ("spawn_agent", "resume_agent", "close_agent", "send_input", "wait_agent")
            }
            self.schemas = {
                tool["name"]: tool.get("parameters")
                for tool in tools
                if isinstance(tool.get("name"), str)
                and (
                    tool["name"] in names
                    or any(word in tool["name"].lower() for word in ("agent", "send_input", "message"))
                )
            }
            tool = single.find_tool(tools, "agent" if self.client == "claude" else "spawn_agent")
            args = (
                {
                    "prompt": TASK,
                    "description": "Read local fixture",
                    "subagent_type": "p04-worker",
                    "run_in_background": True,
                }
                if self.client == "claude"
                else {"message": TASK}
            )
            return parallel.calls_wire(self.protocol, MAIN, [(tool, args, "p06_spawn")])
        self.child_id = cancel.task_identifier(records.get("p06_spawn"))
        first_received = (
            cancel.notification_evidence(body, self.child_id, FIRST)[0]
            if self.client == "claude"
            else FIRST in json.dumps(records)
        )
        if self.phase == "first" and first_received:
            self.phase = "closing" if self.closed_resume else "wait-resume"
            tool = single.find_tool(
                tools,
                "close_agent" if self.closed_resume else "sendmessage" if self.client == "claude" else "send_input",
            )
            args = (
                {"target": self.child_id}
                if self.closed_resume
                else {"to": self.child_id, "message": RESUME_TASK, "summary": "Continue the same synthetic child"}
                if self.client == "claude"
                else {"target": self.child_id, "message": RESUME_TASK}
            )
            return parallel.calls_wire(self.protocol, MAIN, [(tool, args, "p06_continue")])
        if self.phase == "first" and self.client == "claude":
            if not self.first_done.wait(6):
                raise ValueError("Claude background model B did not finish")
            return parallel.calls_wire(
                self.protocol,
                MAIN,
                [
                    (
                        single.find_tool(tools, "read"),
                        {"file_path": str(self.root / "fixture.txt")},
                        f"p06_first_notification_{stage}",
                    )
                ],
            )
        if self.phase == "closing":
            self.phase = "reopening"
            return parallel.calls_wire(
                self.protocol, MAIN, [(single.find_tool(tools, "resume_agent"), {"id": self.child_id}, "p06_reopen")]
            )
        if self.phase == "reopening":
            self.phase = "wait-resume"
            return parallel.calls_wire(
                self.protocol,
                MAIN,
                [
                    (
                        single.find_tool(tools, "send_input"),
                        {"target": self.child_id, "message": RESUME_TASK},
                        "p06_send",
                    )
                ],
            )
        if self.phase == "wait-resume":
            if self.client == "claude":
                self.notification_verified = cancel.notification_evidence(body, self.child_id, RESUMED)[0]
                if self.notification_verified:
                    return single.encode(self.protocol, MAIN, text=DONE)
                if not self.resumed.wait(6):
                    raise ValueError("Claude child did not resume via native SendMessage")
                return parallel.calls_wire(
                    self.protocol,
                    MAIN,
                    [
                        (
                            single.find_tool(tools, "read"),
                            {"file_path": str(self.root / "fixture.txt")},
                            f"p06_notification_{stage}",
                        )
                    ],
                )
            if RESUMED in json.dumps(records):
                return single.encode(self.protocol, MAIN, text=DONE)
            if self.closed_resume and "errored" in json.dumps(records):
                self.resume_error = {key: value for key, value in records.items() if "errored" in json.dumps(value)}
                return single.encode(self.protocol, MAIN, text="P06_CLOSED_RESUME_NOT_CERTIFIED")
        return parallel.calls_wire(
            self.protocol,
            MAIN,
            [
                (
                    single.find_tool(tools, "wait_agent"),
                    {"targets": [self.child_id], "timeout_ms": 10000},
                    f"p06_wait_{stage}",
                )
            ],
        )


def verified_lifecycle(client, stdout, child_id):
    if client == "claude":
        try:
            result = json.loads(stdout)
        except ValueError:
            return False
        stats = result.get("subagent_stats", {}) if isinstance(result, dict) else {}
        return (
            isinstance(result, dict)
            and result.get("is_error") is False
            and result.get("permission_denials") == []
            and isinstance(stats, dict)
            and all(type(stats.get(key)) is int for key in ("spawned", "completed", "failed", "started_in_background"))
            and stats.get("spawned") == stats.get("completed") == stats.get("started_in_background") == 1
            and stats.get("failed") == 0
        )
    spawned, sent, waited = [], [], set()
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
            or item.get("status") != "completed"
        ):
            continue
        if item.get("tool") == "spawn_agent":
            spawned.append((item.get("receiver_thread_ids"), item.get("prompt")))
        elif item.get("tool") == "send_input":
            sent.append((item.get("receiver_thread_ids"), item.get("prompt")))
        elif item.get("tool") == "wait":
            states = item.get("agents_states")
            if not isinstance(states, dict):
                return False
            for identifier, state in states.items():
                if identifier != child_id:
                    return False
                if isinstance(state, dict) and state.get("status") == "completed":
                    if not isinstance(state.get("message"), str):
                        return False
                    waited.add(state["message"])
    return spawned == [([child_id], TASK)] and sent == [([child_id], RESUME_TASK)] and waited == {FIRST, RESUMED}


def run(client, upstream, source, *, closed_resume=False):
    if closed_resume and client != "codex":
        raise ValueError("Closed-thread probe is Codex only")
    scenario = Scenario(client, upstream, closed_resume=closed_resume)
    driver = parallel.drive(scenario, source, child_model=CHILD, claude_tools="Agent,Read,SendMessage")
    result = scenario.result
    lifecycle = result is not None and verified_lifecycle(client, result.stdout, scenario.child_id)
    closed_guard = (
        closed_resume
        and result is not None
        and result.returncode != 0
        and scenario.stages["child"] == 2
        and not scenario.errors
        and not scenario.rejected_models
        and "Closed child resume may switch" in result.stdout + result.stderr
        and "send_input" in result.stdout + result.stderr
    )
    passed = (
        result is not None
        and result.returncode == 0
        and DONE in result.stdout
        and lifecycle
        and scenario.child_id is not None
        and scenario.stages["child"] == 3
        and scenario.history_verified
        and not scenario.errors
        and (scenario.audit_count == 1 if client == "codex" else scenario.notification_verified)
    )
    if closed_resume:
        passed = closed_guard
    return {
        "passed": passed,
        "client": client,
        "upstream": upstream.value,
        "cli_version": driver["cli_version"],
        "observed": scenario.observed,
        "fixture_errors": scenario.errors,
        "schemas": scenario.schemas,
        "history_verified": scenario.history_verified,
        "notification_verified": scenario.notification_verified,
        "rejected_models": scenario.rejected_models,
        "lifecycle_verified": lifecycle,
        "closed_resume": closed_resume,
        "closed_resume_error": scenario.resume_error,
        "closed_resume_guard_verified": closed_guard,
        "stdout": result.stdout if result else "",
        "stderr": result.stderr if result else "",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex", "claude"], required=True)
    parser.add_argument("--upstream", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--adapter-source", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--closed-resume", action="store_true")
    args = parser.parse_args()
    result = run(args.client, args.upstream, args.adapter_source, closed_resume=args.closed_resume)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key not in {"stdout", "stderr", "schemas"}}, indent=2
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
