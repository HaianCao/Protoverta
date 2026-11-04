"""Two actual source launchers with separate gateway/provider/model state."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

import smoke_clients
import smoke_subagents as single
import smoke_tool_search
from probe_process import _start, run_process
from smoke_tool_search import functions

from protoverta.core import Protocol
from protoverta.namespaces import NamespaceMap
from protoverta.server import GatewayConfig
from protoverta.session_diagnostics import latest_diagnostics

PAIRS = [("codex", "responses", "codex", "chat"), ("codex", "messages", "codex", "responses"),
         ("claude", "messages", "claude", "chat"), ("claude", "responses", "claude", "messages"),
         ("codex", "chat", "claude", "responses"), ("claude", "chat", "codex", "messages")]


def environment(root):
    env = {key: value for key, value in os.environ.items() if key.upper() in
           {"PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC"}}
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "TEMP", "TMP"):
        path = root / key
        path.mkdir(parents=True)
        env[key] = str(path)
    env.update(CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
               CLAUDE_CODE_MAX_RETRIES="0", GIT_ALLOW_PROTOCOL="file", GIT_TERMINAL_PROMPT="0",
               NO_PROXY="127.0.0.1,localhost")
    return env


def worker(bundle_path):
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    root = bundle_path.parent
    env = environment(root / "state")
    client, protocol = bundle["client"], Protocol(bundle["protocol"])
    executable = shutil.which(client)
    if executable is None:
        raise RuntimeError("Required CLI missing")
    version = run_process([executable, "--version"], cwd=str(root), env=env, text=True, encoding="utf-8", errors="replace", timeout=5)
    expected = "codex-cli 0.166.0" if client == "codex" else "2.1.308 (Claude Code)"
    if version.returncode or version.stdout.strip() != expected:
        raise ValueError("Fixture requires the documented CLI version")
    original = smoke_clients.run_process

    def observe(args, **kwargs):
        path = root / "adapter.toml"
        content = path.read_text(encoding="utf-8")
        marker = 'execution_mode = "gateway"\n'
        if content.count(marker) != 1:
            raise ValueError("Expected exactly one owned fixture provider")
        runtime = 'gateway_runtime = { diagnostics_dir = ' + json.dumps(bundle["diagnostics"]) + ' }\n'
        path.write_text(content.replace(marker, marker + runtime), encoding="utf-8")
        at = args.index("-m")
        module = args[at + 1]
        args[at:at + 2] = [str(Path(__file__).with_name("probe_gateway_context.py")), module, str(root / "gateway.json")]
        return original(args, **kwargs)

    config = GatewayConfig(protocol, bundle["origin"] + "/v1", bundle["key"], "p05-worker-token")
    options = {}
    if bundle.get("worktrees") and client == "codex":
        options = {"mcp_servers.worktree.command": sys.executable,
                   "mcp_servers.worktree.args": [str(Path(__file__).resolve()), "--read-mcp"],
                   "mcp_servers.worktree.required": True}
    with patch.object(smoke_clients, "run_process", observe):
        result = smoke_clients.run_client(client, executable, "http://127.0.0.1:0", bundle["origin"], config,
                                         env, str(root), bundle.get("worktrees", False), Path(bundle["source"]), False, protocol, 30,
                                         bundle["model"], provider_id=bundle["provider"],
                                         codex_config=options,
                                         prompt="Return the synthetic fixture answer. Do not use tools.")
    passed = result.returncode == 0 and bundle["answer"] in result.stdout and bundle["other_answer"] not in result.stdout
    report = {"passed": passed, "returncode": result.returncode, "cli_version": version.stdout.strip(),
              "stdout": result.stdout, "stderr": result.stderr}
    (root / "worker-result.json").write_text(json.dumps(report), encoding="utf-8")
    return 0 if passed else 1


class Upstream:
    def __init__(self, protocol, model, key, answer, *, client=None, read_value=None):
        self.protocol, self.model, self.key, self.answer = protocol, model, key, answer
        self.started, self.release = threading.Event(), threading.Event()
        self.requests, self.errors = 0, []
        self.client, self.read_value, self.read_verified = client, read_value, False

    @contextmanager
    def serve(self):
        state = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                try:
                    length = int(self.headers["Content-Length"])
                    if not 0 <= length <= 4 * 1024 * 1024:
                        raise ValueError("Request size")
                    body = json.loads(self.rfile.read(length))
                    expected = {Protocol.CHAT: "/v1/chat/completions", Protocol.MESSAGES: "/v1/messages", Protocol.RESPONSES: "/v1/responses"}[state.protocol]
                    credentials = [value for value in (self.headers.get("Authorization"), self.headers.get("x-api-key")) if value]
                    if self.path.split("?", 1)[0] != expected or body.get("model") != state.model or credentials not in (["Bearer " + state.key], [state.key]):
                        raise ValueError("Endpoint/model/auth isolation mismatch")
                    state.requests += 1
                    if state.requests > (2 if state.read_value else 1):
                        raise ValueError("Unexpected extra upstream request")
                    state.started.set()
                    if not state.release.wait(25):
                        raise TimeoutError("Owned fixture release deadline")
                    if state.read_value and state.requests == 1:
                        tools = list(functions(body, state.protocol))
                        if state.client == "claude":
                            tool = single.find_tool(tools, "read")
                            args = {"file_path": "fixture.txt"}
                        else:
                            alias = NamespaceMap().alias("mcp__worktree", "read_worktree_fixture")
                            tool = next(tool for tool in tools if tool.get("name") in {"read_worktree_fixture", "mcp__worktree__read_worktree_fixture", alias})
                            args = {}
                        wire = single.encode(state.protocol, state.model, tool=tool, arguments=args, call_id="same-worktree-read").read()
                    else:
                        if state.read_value:
                            records = dict(single.tool_records(body, state.protocol))
                            if state.read_value not in json.dumps(records.get("same-worktree-read")):
                                raise ValueError("Worktree read returned wrong cwd/file or tool failure")
                            state.read_verified = True
                        wire = single.encode(state.protocol, state.model, text=state.answer).read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                except Exception as exc:  # noqa: BLE001 -- fixture errors are shape-only
                    state.errors.append(type(exc).__name__)
                    wire = b'{"error":{"message":"Synthetic isolation fixture rejected request"}}'
                    self.send_response(422)
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(wire)))
                self.send_header("x-should-retry", "false")
                self.end_headers()
                try:
                    self.wfile.write(wire)
                except OSError:
                    state.errors.append("PeerClosed")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            state.release.set()
            server.shutdown()
            server.server_close()
            thread.join(2)


def auth_status(context, client, token):
    path = "/v1/responses" if client == "codex" else "/v1/messages"
    request = Request(context["origin"] + path, data=b"{", headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=2) as response:
            return response.status
    except HTTPError as error:
        error.close()
        return error.code


def port_closed(origin):
    url = urlsplit(origin)
    try:
        with socket.create_connection((url.hostname, url.port), timeout=0.5):
            return False
    except OSError:
        return True


def diagnostics(root, specs):
    identifiers, valid = [], True
    for spec in specs:
        paths = list((root / spec["provider"]).glob("session-*.jsonl"))
        report = latest_diagnostics(root / spec["provider"])
        records = report["records"]
        raw = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        valid &= (len(paths) == 1 and any(record["outcome"] == "success" for record in records)
                  and all(record["upstream_protocol"] == spec["protocol"] for record in records)
                  and all(other[field] not in raw for other in specs for field in ("key", "model", "answer")))
        identifiers.append({record["request_id"] for record in records})
    return bool(valid and all(identifiers) and not identifiers[0] & identifiers[1])


def accepted(results):
    flags = ["distinct_gateways", "cross_tokens_rejected", "first_completed", "first_gateway_closed", "second_survived", "second_completed", "second_gateway_closed", "diagnostics_isolated"]
    counts = results.get("upstream_counts")
    if results.get("worktrees"):
        flags += ["worktree_reads_verified", "worktree_cwd_verified", "main_checkout_unchanged"]
    return (all(results.get(key) is True for key in flags) and isinstance(counts, list) and len(counts) == 2
            and all(type(count) is int and count == (2 if results.get("worktrees") else 1) for count in counts)
            and results.get("fixture_errors") == [[], []] and results.get("errors") == [])


def create_worktrees(root):
    env = environment(root / "git-state")
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    main = root / "main"
    main.mkdir()
    def git(*args):
        result = run_process(["git", *args], cwd=str(main), env=env, text=True, encoding="utf-8", errors="replace", timeout=5)
        if result.returncode:
            raise RuntimeError("Temporary worktree Git command failed")
    git("init", "--initial-branch=main")
    (main / "fixture.txt").write_text("P05_MAIN_CHECKOUT_DO_NOT_USE", encoding="utf-8")
    git("add", "fixture.txt")
    git("-c", "user.name=Synthetic Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "-m", "Synthetic fixture")
    for provider in ("alpha", "beta"):
        target = (root / provider).resolve()
        if not target.is_relative_to(root.resolve()):
            raise ValueError("Worktree target escaped owned root")
        git("worktree", "add", "--detach", str(target))
        (target / "fixture.txt").write_text("P05_WORKTREE_" + provider.upper(), encoding="utf-8")


def run(pair, codex_source, claude_source, *, worktrees=False):
    results = {"passed": False, "pair": pair, "errors": [], "worktrees": worktrees}
    with tempfile.TemporaryDirectory(prefix="protoverta-two-launchers-") as directory, ExitStack() as stack:
        root = Path(directory)
        specs, upstreams, processes = [], [], []
        try:
            if worktrees:
                create_worktrees(root)
            for index, (client, protocol) in enumerate(((pair[0], pair[1]), (pair[2], pair[3]))):
                provider = ("alpha", "beta")[index]
                task = root / provider
                task.mkdir(exist_ok=worktrees)
                spec = {"client": client, "protocol": protocol, "provider": provider, "model": "p05-model-" + provider,
                        "key": "p05-synthetic-key-" + provider, "answer": "P05_SESSION_" + provider.upper(),
                        "other_answer": "P05_SESSION_" + ("BETA" if index == 0 else "ALPHA"),
                        "diagnostics": str(root / "diagnostics"),
                        "source": str((codex_source if client == "codex" else claude_source).resolve()), "worktrees": worktrees}
                upstream = Upstream(Protocol(protocol), spec["model"], spec["key"], spec["answer"], client=client,
                                    read_value="P05_WORKTREE_" + provider.upper() if worktrees else None)
                spec["origin"] = stack.enter_context(upstream.serve())
                path = task / "worker.json"
                path.write_text(json.dumps(spec), encoding="utf-8")
                specs.append(spec)
                upstreams.append(upstream)
                process, _job = stack.enter_context(_start([sys.executable, str(Path(__file__).resolve()), "--worker", str(path)],
                                                          cwd=str(task), env=environment(task / "controller-state"),
                                                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                          text=True, encoding="utf-8", errors="replace"))
                processes.append(process)
            deadline = time.monotonic() + 18
            for upstream in upstreams:
                if not upstream.started.wait(max(0, deadline - time.monotonic())):
                    raise RuntimeError("Two launcher inference requests did not overlap")
            contexts = [json.loads((root / spec["provider"] / "gateway.json").read_text(encoding="utf-8")) for spec in specs]
            results["distinct_gateways"] = contexts[0]["origin"] != contexts[1]["origin"] and contexts[0]["token"] != contexts[1]["token"]
            results["cross_tokens_rejected"] = all(auth_status(contexts[index], spec["client"], contexts[1 - index]["token"]) == 401 for index, spec in enumerate(specs))
            upstreams[0].release.set()
            _stdout, _stderr = processes[0].communicate(timeout=12)
            first = json.loads((root / "alpha" / "worker-result.json").read_text(encoding="utf-8"))
            results["first_completed"] = processes[0].returncode == 0 and first["passed"]
            results["first_gateway_closed"] = (root / "alpha" / "gateway.closed").exists() and port_closed(contexts[0]["origin"])
            results["second_auth_status"] = auth_status(contexts[1], specs[1]["client"], contexts[1]["token"])
            results["second_survived"] = processes[1].poll() is None and not upstreams[1].release.is_set() and results["second_auth_status"] == 422
            upstreams[1].release.set()
            _stdout, _stderr = processes[1].communicate(timeout=12)
            second = json.loads((root / "beta" / "worker-result.json").read_text(encoding="utf-8"))
            results["second_completed"] = processes[1].returncode == 0 and second["passed"]
            results["second_gateway_closed"] = (root / "beta" / "gateway.closed").exists() and port_closed(contexts[1]["origin"])
            results["diagnostics_isolated"] = diagnostics(root / "diagnostics", specs)
            results["upstream_counts"] = [upstream.requests for upstream in upstreams]
            results["fixture_errors"] = [upstream.errors for upstream in upstreams]
            results["cli_versions"] = [first["cli_version"], second["cli_version"]]
            if worktrees:
                results["worktree_reads_verified"] = all(upstream.read_verified for upstream in upstreams)
                results["worktree_cwd_verified"] = all(Path(context["cwd"]) == (root / spec["provider"]).resolve() and (root / spec["provider"] / ".git").is_file() for context, spec in zip(contexts, specs, strict=True))
                results["main_checkout_unchanged"] = (root / "main" / "fixture.txt").read_text(encoding="utf-8") == "P05_MAIN_CHECKOUT_DO_NOT_USE"
            results["passed"] = accepted(results)
        except Exception as exc:  # noqa: BLE001 -- synthetic fixture diagnostics, no credentials/payloads
            results["errors"].append(type(exc).__name__)
            results["workers"] = [{"finished": process.poll() is not None} for process in processes]
        finally:
            for upstream in upstreams:
                upstream.release.set()
    results["temporary_state_removed"] = not root.exists()
    results["passed"] = results["passed"] and results["temporary_state_removed"]
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--pair", type=int, choices=range(len(PAIRS)), default=0)
    parser.add_argument("--codex-source", type=Path, default=Path("../free-codex-cli"))
    parser.add_argument("--claude-source", type=Path, default=Path("../free-claude-cli"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--worktrees", action="store_true")
    parser.add_argument("--read-mcp", action="store_true")
    args = parser.parse_args()
    if args.read_mcp:
        cwd = Path.cwd().resolve()
        if not (cwd / ".git").is_file() or not cwd.parent.name.startswith("protoverta-two-launchers-"):
            raise ValueError("MCP read requires its owned temporary worktree cwd")
        smoke_tool_search.LOOKUP = "read_worktree_fixture"
        smoke_tool_search.VALUE = (cwd / "fixture.txt").read_text(encoding="utf-8")
        smoke_tool_search.serve_mcp()
        return 0
    if args.worker:
        return worker(args.worker)
    result = run(PAIRS[args.pair], args.codex_source, args.claude_source, worktrees=args.worktrees)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
