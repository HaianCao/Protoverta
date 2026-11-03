"""Claude nested delegation/depth refusal, source wrapper and local fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

import smoke_parallel_agents as parallel
import smoke_subagents as single
from smoke_tool_search import functions

from protoverta.core import Protocol
from protoverta.namespaces import NamespaceMap

CHILD = "P05_NESTED_CHILD_TASK"
LEAF = "P05_NESTED_LEAF_TASK"
LEAF_DONE = "P05_NESTED_LEAF_VERIFIED"
CHILD_DONE = "P05_NESTED_CHILD_VERIFIED"
BLOCKED = "P05_NESTED_BLOCKED_BY_DEPTH"
DONE = "P05_NESTED_PARENT_VERIFIED"


class Scenario(parallel.Scenario):
    def __init__(self, protocol, depth):
        super().__init__("claude", protocol)
        self.depth = depth
        self.stages = {"parent": 0, "child": 0, "leaf": 0}
        self.child_agent_advertised = None
        self.read_verified = False

    def open(self, request, *, timeout):
        expected_path = {Protocol.CHAT: "/v1/chat/completions", Protocol.MESSAGES: "/v1/messages", Protocol.RESPONSES: "/v1/responses"}[self.protocol]
        if urlsplit(request.full_url).path != expected_path:
            raise ValueError("Unexpected nested upstream endpoint")
        body = json.loads(request.data)
        if body.get("model") != "p04-fixture-model":
            raise ValueError("Nested model changed")
        text = single.user_text(body, self.protocol)
        role = "leaf" if LEAF in text else "child" if CHILD in text else "parent"
        records = dict(single.tool_records(body, self.protocol))
        tools = list(functions(body, self.protocol))
        self.stages[role] += 1
        stage = self.stages[role]
        self.observed.append({"role": role, "stage": stage, "result_call_ids": sorted(records), "tool_names": [tool.get("name") for tool in tools]})
        if len(self.observed) > 8:
            raise ValueError("Nested fixture request budget exhausted")
        if stage == 1 and role == "parent":
            tool = single.find_tool(tools, "agent")
            return parallel.calls_wire(self.protocol, body["model"], [(tool, {"prompt": CHILD + " Delegate a local read to a nested worker", "description": "Nested fixture read", "subagent_type": "p04-worker", "run_in_background": False}, "same_spawn")])
        if role == "child" and stage == 1:
            self.child_agent_advertised = any(tool.get("name") == "Agent" for tool in tools)
            if self.depth > 1:
                tool = single.find_tool(tools, "agent")
                return parallel.calls_wire(self.protocol, body["model"], [(tool, {"prompt": LEAF + " Read " + str(self.root / "fixture.txt"), "description": "Read nested fixture", "subagent_type": "p04-worker", "run_in_background": False}, "same_spawn")])
            if self.child_agent_advertised:
                raise ValueError("Agent still advertised at explicit depth limit")
        if (role == "leaf" or role == "child" and self.depth == 1) and stage == 1:
            return parallel.calls_wire(self.protocol, body["model"], [(single.find_tool(tools, "read"), {"file_path": str(self.root / "fixture.txt")}, "nested_read")])
        if role == "leaf" or role == "child" and self.depth == 1:
            if stage != 2 or single.VALUE not in json.dumps(records.get("nested_read")):
                raise ValueError("Nested/local read result missing")
            self.read_verified = True
            return single.encode(self.protocol, body["model"], text=LEAF_DONE if role == "leaf" else BLOCKED)
        if role == "child" and stage == 2 and LEAF_DONE in json.dumps(records.get("same_spawn")):
            return single.encode(self.protocol, body["model"], text=CHILD_DONE)
        if role == "parent" and stage == 2 and (CHILD_DONE if self.depth > 1 else BLOCKED) in json.dumps(records.get("same_spawn")):
            return single.encode(self.protocol, body["model"], text=DONE)
        raise ValueError("Nested lifecycle did not return correlated results")


def verified_lifecycle(output, depth):
    if not isinstance(output, dict):
        return False
    stats = output.get("subagent_stats", {})
    expected = 2 if depth > 1 else 1
    return (isinstance(stats, dict) and all(type(stats.get(key)) is int for key in ("spawned", "completed", "failed", "max_depth", "spawned_by_subagents"))
            and output.get("is_error") is False and output.get("permission_denials") == []
            and stats.get("spawned") == stats.get("completed") == expected and stats.get("failed") == 0
            and stats.get("max_depth") == expected and stats.get("spawned_by_subagents") == expected - 1)


def run(upstream, adapter_source, *, depth=2):
    scenario = Scenario(upstream, depth)
    driver = parallel.drive(scenario, adapter_source, claude_tools="Agent,Read", claude_agent_tools=["Agent", "Read"], claude_depth=depth)
    result = scenario.result
    try:
        output = json.loads(result.stdout) if result else {}
    except ValueError:
        output = {}
    stats = output.get("subagent_stats", {}) if isinstance(output, dict) else {}
    lifecycle = verified_lifecycle(output, depth)
    passed = (result is not None and result.returncode == 0 and DONE in result.stdout and lifecycle
              and scenario.read_verified and not scenario.errors and scenario.child_agent_advertised is (depth > 1)
              and scenario.stages == {"parent": 2, "child": 2, "leaf": 2 if depth > 1 else 0})
    return {"passed": passed, "upstream": upstream.value, "depth_limit": depth, "cli_version": driver["cli_version"],
            "lifecycle_verified": lifecycle, "child_agent_advertised": scenario.child_agent_advertised,
            "stats": stats, "observed": scenario.observed, "fixture_errors": scenario.errors,
            "stdout": result.stdout if result else "", "stderr": result.stderr if result else ""}


def codex_profile_control(upstream, adapter_source):
    result = single.run("codex", upstream, adapter_source=adapter_source)
    child = [item for item in result["observed"] if item["role"] == "child" and item["stage"] == 1]
    names = set(child[0]["tool_names"]) if len(child) == 1 else set()
    nested = bool(names & {"spawn_agent", NamespaceMap().alias("multi_agent_v1", "spawn_agent")})
    return {"passed": result["passed"] and bool(child) and not nested,
            "client": "codex", "upstream": upstream.value, "cli_version": result["cli_version"],
            "child_spawn_advertised": nested, "scope": "Existing single-child profile only; no claim that all Codex configurations reject nesting."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--client", choices=["codex", "claude"], default="claude")
    parser.add_argument("--adapter-source", type=Path, required=True)
    parser.add_argument("--depth-limit", type=int, choices=[1, 2], default=2)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = codex_profile_control(args.upstream, args.adapter_source) if args.client == "codex" else run(args.upstream, args.adapter_source, depth=args.depth_limit)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if not result["passed"] or key not in {"stdout", "stderr"}}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
