"""Native Claude isolation=worktree: relative read, parent isolation and cleanup."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

import smoke_parallel_agents as parallel
import smoke_subagents as single
from probe_process import run_process
from smoke_tool_search import functions
from smoke_two_launchers import environment

from protoverta.core import Protocol

TASK = "P05_NATIVE_WORKTREE_TASK"
CHILD = "P05_NATIVE_WORKTREE_CHILD_VERIFIED"
DONE = "P05_NATIVE_WORKTREE_PARENT_VERIFIED"
VALUE = "P05_NATIVE_CHILD_ONLY_VALUE"


def verified_lifecycle(stdout):
    if not single.lifecycle_verified("claude", stdout):
        return False
    result = json.loads(stdout)
    stats = result["subagent_stats"]
    return result.get("permission_denials") == [] and all(type(stats.get(key)) is int for key in ("spawned", "completed", "failed"))


class Scenario(parallel.Scenario):
    def __init__(self, protocol):
        super().__init__("claude", protocol)
        self.stages = {"parent": 0, "child": 0}
        self.git_env = None
        self.child_path = None
        self.cleaned = False
        self.read_verified = False

    def git(self, *args):
        result = run_process(["git", *args], cwd=str(self.root), env=self.git_env,
                             text=True, encoding="utf-8", errors="replace", timeout=5)
        if result.returncode:
            raise RuntimeError("Owned native-worktree Git command failed")
        return result.stdout

    def prepare(self, root):
        import os

        self.git_env = environment(root / "git-state")
        self.git_env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "--initial-branch=main")
        self.git("add", "fixture.txt")
        self.git("-c", "user.name=Synthetic Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "-m", "Synthetic fixture")

    def paths(self):
        return [Path(line[len("worktree "):]).resolve() for line in self.git("worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]

    def open(self, request, *, timeout):
        body = json.loads(request.data)
        expected = {Protocol.CHAT: "/v1/chat/completions", Protocol.MESSAGES: "/v1/messages", Protocol.RESPONSES: "/v1/responses"}[self.protocol]
        if body.get("model") != "p04-fixture-model" or urlsplit(request.full_url).path != expected:
            raise ValueError("Native-worktree endpoint/model mismatch")
        role = "child" if TASK in single.user_text(body, self.protocol) else "parent"
        self.stages[role] += 1
        stage = self.stages[role]
        if stage > 2:
            raise ValueError("Native worktree request budget")
        tools, records = list(functions(body, self.protocol)), dict(single.tool_records(body, self.protocol))
        if role == "parent" and stage == 1:
            return single.encode(self.protocol, body["model"], tool=single.find_tool(tools, "agent"),
                                 arguments={"prompt": TASK + " Read fixture.txt relative to your worktree", "description": "Read own worktree", "subagent_type": "p04-worker", "run_in_background": False}, call_id="worktree_spawn")
        if role == "child" and stage == 1:
            children = [path for path in self.paths() if path != self.root.resolve()]
            if len(children) != 1 or not children[0].is_relative_to(self.root.resolve()):
                raise ValueError("Native Agent did not create an owned linked worktree")
            self.child_path = children[0]
            (self.child_path / "fixture.txt").write_text(VALUE, encoding="utf-8")
            return single.encode(self.protocol, body["model"], tool=single.find_tool(tools, "read"), arguments={"file_path": "fixture.txt"}, call_id="worktree_read")
        if role == "child":
            if VALUE not in json.dumps(records.get("worktree_read")):
                raise ValueError("Child relative read escaped its native worktree")
            self.read_verified = True
            # Undo only our synthetic probe modification so native clean-worktree
            # cleanup can be verified without deleting or masking dirty work.
            (self.child_path / "fixture.txt").write_text(single.VALUE, encoding="utf-8")
            return single.encode(self.protocol, body["model"], text=CHILD)
        if CHILD not in json.dumps(records.get("worktree_spawn")):
            raise ValueError("Native worktree child result missing")
        self.cleaned = self.paths() == [self.root.resolve()] and not self.child_path.exists()
        if (self.root / "fixture.txt").read_text(encoding="utf-8") != single.VALUE:
            raise ValueError("Main checkout was modified")
        return single.encode(self.protocol, body["model"], text=DONE)


def run(upstream, source):
    state = Scenario(upstream)
    driver = parallel.drive(state, source, claude_isolation="worktree")
    result = state.result
    lifecycle = result is not None and verified_lifecycle(result.stdout)
    passed = (result is not None and result.returncode == 0 and DONE in result.stdout and lifecycle and state.cleaned
              and state.read_verified and state.stages == {"parent": 2, "child": 2} and not state.errors)
    return {"passed": passed, "upstream": upstream.value, "cli_version": driver["cli_version"],
            "native_worktree_cleaned": state.cleaned, "relative_read_verified": state.read_verified,
            "lifecycle_verified": lifecycle, "fixture_errors": state.errors,
            "stdout": result.stdout if result else "", "stderr": result.stderr if result else ""}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--adapter-source", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = run(args.upstream, args.adapter_source)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"stdout", "stderr"}}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
