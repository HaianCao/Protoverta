"""Keyless, isolated parent/child CLI fixture; never call a real provider."""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import smoke_clients
import smoke_tool_search
from smoke_tool_search import functions

from protoverta.core import Protocol
from protoverta.namespaces import NamespaceMap
from protoverta.server import GatewayConfig, gateway_session
from protoverta.sse import encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta

TASK = "P04_CHILD_READ_TASK"
VALUE = "P04_LOCAL_FILE_VALUE"
CHILD_DONE = "P04_CHILD_VERIFIED"
DONE = "P04_PARENT_VERIFIED"


def user_text(body, protocol):
    messages = body.get("input", []) if protocol == Protocol.RESPONSES else body.get("messages", [])
    result = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content", [])
        if isinstance(content, str):
            result.append(content)
        elif isinstance(content, list):
            result.extend(block["text"] for block in content if isinstance(block, dict) and block.get("type") in {"text", "input_text"})
    return "\n".join(result)


def tool_results(body, protocol):
    return [value for _, value in tool_records(body, protocol)]


def tool_records(body, protocol):
    if protocol == Protocol.RESPONSES:
        return [(item.get("call_id"), item.get("output")) for item in body.get("input", []) if isinstance(item, dict) and item.get("type") == "function_call_output"]
    if protocol == Protocol.CHAT:
        return [(item.get("tool_call_id"), item.get("content")) for item in body.get("messages", []) if item.get("role") == "tool"]
    return [(block.get("tool_use_id"), block.get("content")) for item in body.get("messages", []) if isinstance(item.get("content"), list)
            for block in item["content"] if isinstance(block, dict) and block.get("type") == "tool_result" and not block.get("is_error")]


def agent_id(results):
    for value in results:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if isinstance(value, dict) and isinstance(value.get("agent_id"), str):
            return value["agent_id"]
    raise ValueError("No real spawned agent ID in tool results")


def find_tool(tools, name):
    alias = NamespaceMap().alias("multi_agent_v1", name)
    return next(tool for tool in tools if tool.get("name") == alias or tool.get("name", "").lower() == name)


def lifecycle_verified(client, stdout):
    if client == "claude":
        try:
            result = json.loads(stdout)
        except ValueError:
            return False
        if not isinstance(result, dict):
            return False
        stats = result.get("subagent_stats", {})
        return (isinstance(stats, dict) and result.get("is_error") is False and not result.get("permission_denials")
                and stats.get("spawned") == stats.get("completed") == 1 and stats.get("failed") == 0)
    children = set()
    waited = set()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item", {})
        if not isinstance(item, dict):
            continue
        if event.get("type") != "item.completed" or item.get("type") != "collab_tool_call" or item.get("status") != "completed":
            continue
        if item.get("tool") == "spawn_agent":
            children.update(item.get("receiver_thread_ids", []))
        if item.get("tool") == "wait":
            states = item.get("agents_states", {})
            if isinstance(states, dict):
                waited.update(key for key, value in states.items()
                              if isinstance(value, dict) and value.get("status") == "completed" and value.get("message") == CHILD_DONE)
    return len(children) == 1 and children == waited


def encode(protocol, model, *, text=None, tool=None, arguments=None, call_id="p04_call"):
    encoder = StreamEncoder(protocol)
    deltas = [Delta("start", model=model, input_tokens=1, output_tokens=0)]
    if tool:
        deltas += [Delta("tool_start", index=0, name=tool["name"], call_id=call_id),
                   Delta("tool_delta", index=0, text=json.dumps(arguments)), Delta("tool_end", index=0)]
    else:
        deltas.append(Delta("text", text=text))
    deltas.append(Delta("finish", reason="tool_calls" if tool else "stop", input_tokens=1, output_tokens=1))
    wire = []
    for delta in deltas:
        for event in encoder.feed(delta):
            data = event.data
            if tool and protocol == Protocol.RESPONSES and tool.get("namespace"):
                data = json.loads(data)
                for item in [data.get("item", {}), *data.get("response", {}).get("output", [])]:
                    if item.get("type") == "function_call":
                        item["namespace"] = tool["namespace"]
                data = json.dumps(data)
            wire.append(encode_sse(event.event, data))
    return io.BytesIO(b"".join(wire))


def run(client, upstream, *, inspect_only=False, adapter_source=None):
    executable = shutil.which(client)
    if executable is None:
        raise RuntimeError(f"{client} not on PATH")
    observed, schemas = [], {}
    state = {"parent": 0, "child": 0}
    lock = threading.Lock()
    original_process = smoke_clients.run_process
    model = "p04-fixture-model"

    with tempfile.TemporaryDirectory(prefix="protoverta-p04-") as directory:
        root = Path(directory)
        fixture = root / "fixture.txt"
        fixture.write_text(VALUE, encoding="utf-8")
        audit = root / "mcp-audit.txt"
        env = {key: value for key, value in os.environ.items() if key.upper() in
               {"PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP"}}
        for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
            path = root / key
            path.mkdir()
            env[key] = str(path)
        env.update(CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
                   GIT_ALLOW_PROTOCOL="file", GIT_TERMINAL_PROMPT="0", NO_PROXY="127.0.0.1,localhost",
                   PROTOVERTA_SMOKE_TOKEN="p04-local-token")
        version = original_process([executable, "--version"], cwd=directory, env=env,
                                   text=True, encoding="utf-8", errors="replace", timeout=3)
        cli_version = version.stdout.strip()
        expected = "codex-cli 0.166.0" if client == "codex" else "2.1.308 (Claude Code)"
        if version.returncode or (not inspect_only and cli_version != expected):
            raise ValueError("Subagent fixture requires the documented CLI version; inspect a new version separately")
        codex_options = {"agents.enabled": True, "features.multi_agent": True,
                         "agents.default_subagent_model": model, "agents.max_concurrent_threads_per_session": 1}
        if client == "codex":
            bundled = original_process([executable, "debug", "models", "--bundled"], cwd=directory, env=env,
                                       text=True, encoding="utf-8", errors="replace", timeout=15)
            if bundled.returncode:
                raise ValueError("Could not read bundled CLI model metadata")
            catalog = json.loads(bundled.stdout)
            entries = catalog["models"] if isinstance(catalog, dict) else catalog
            metadata = dict(next(item for item in entries if item["slug"] == "gpt-5.5"))
            metadata.update(slug=model, display_name="Synthetic subagent fixture", support_verbosity=False,
                            apply_patch_tool_type=None, tool_mode=None, experimental_supported_tools=[], supports_search_tool=False)
            catalog_path = root / "models.json"
            catalog_path.write_text(json.dumps({"models": [metadata]}), encoding="utf-8")
            codex_options["model_catalog_json"] = str(catalog_path)
            codex_options.update({"mcp_servers.p04.command": sys.executable,
                                  "mcp_servers.p04.args": [str(Path(__file__).resolve()), "--mcp", "--fixture-file", str(fixture), "--audit-file", str(audit)],
                                  "mcp_servers.p04.required": True})
            if adapter_source is not None:
                env.update(CODEX_CA_ENABLE_SUBAGENTS="1", CODEX_CA_MODEL_CATALOG=str(catalog_path))
                # Exercise the launcher's gated opt-in, not a manual -c bypass.
                for key in ("agents.enabled", "features.multi_agent", "agents.default_subagent_model", "agents.max_concurrent_threads_per_session"):
                    codex_options.pop(key)

        def process(args, **kwargs):
            if client == "claude":
                args[args.index("--tools") + 1] = "Agent,Read"
                args[args.index("--allowedTools") + 1] = "Agent,Read"
                args += ["--agents", json.dumps({"p04-worker": {"description": "Read the synthetic local fixture", "prompt": "Read only the requested fixture file and return its value.", "tools": ["Read"], "model": "inherit"}})]
                kwargs["env"].update(CLAUDE_CODE_SUBAGENT_MODEL=model, CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1")
            return original_process(args, **kwargs)

        def open_upstream(request, *, timeout):
            path = urlsplit(request.full_url).path
            expected_path = {Protocol.CHAT: "/v1/chat/completions", Protocol.MESSAGES: "/v1/messages", Protocol.RESPONSES: "/v1/responses"}[upstream]
            if path != expected_path:
                raise ValueError("Unexpected upstream endpoint in subagent fixture")
            body = json.loads(request.data)
            if body.get("model") != model:
                raise ValueError("Parent or child changed the selected model")
            role = "child" if TASK in user_text(body, upstream) else "parent"
            tools = list(functions(body, upstream))
            with lock:
                state[role] += 1
                stage = state[role]
                observation = {"role": role, "stage": stage, "model": body["model"], "path": path, "tool_names": [tool.get("name") for tool in tools]}
                observed.append(observation)
                if len(observed) > 10:
                    raise ValueError("Fixture exceeded request budget")
            for tool in tools:
                if any(word in tool.get("name", "").lower() for word in ("spawn", "agent", "wait", "exec_command", "shell_command", "read")):
                    schemas[tool["name"]] = {key: tool[key] for key in ("name", "namespace", "parameters") if key in tool}
            if inspect_only:
                return encode(upstream, body["model"], text=DONE)
            results = tool_results(body, upstream)
            records = dict(tool_records(body, upstream))
            observation["result_call_ids"] = sorted(records)
            if role == "child":
                if stage == 1:
                    read_alias = NamespaceMap().alias("mcp__p04", "p04_read_fixture")
                    tool = next(tool for tool in tools if tool.get("name") in {"Read", "p04_read_fixture", read_alias})
                    args = {"file_path": str(fixture)} if tool["name"] == "Read" else {}
                    return encode(upstream, body["model"], tool=tool, arguments=args, call_id="p04_read")
                if stage == 2 and VALUE in json.dumps(records.get("p04_read")):
                    return encode(upstream, body["model"], text=CHILD_DONE)
                raise ValueError("Child did not replay the successful read result")
            if stage == 1:
                tool = find_tool(tools, "agent" if client == "claude" else "spawn_agent")
                args = {"prompt": TASK + " Read " + str(fixture), "description": "Read local fixture", "subagent_type": "p04-worker", "run_in_background": False} if client == "claude" else {"message": TASK + " Read fixture.txt"}
                return encode(upstream, body["model"], tool=tool, arguments=args, call_id="p04_spawn")
            if CHILD_DONE in json.dumps(records.get("p04_wait" if client == "codex" else "p04_spawn")):
                return encode(upstream, body["model"], text=DONE)
            if client == "codex" and stage == 2:
                return encode(upstream, body["model"], tool=find_tool(tools, "wait_agent"),
                              arguments={"targets": [agent_id(results)], "timeout_ms": 10000}, call_id="p04_wait")
            raise ValueError("Parent did not replay completed child result")

        with smoke_clients.fixture_server(open_upstream) as upstream_origin:
            config = GatewayConfig(upstream, upstream_origin + "/v1", "fake-upstream-key", "p04-local-token",
                                   client_profile="codex" if client == "codex" else "claude",
                                   namespace_tools="flatten", messages_usage_policy="deferred" if client == "claude" and upstream != Protocol.MESSAGES else "strict",
                                   default_max_output_tokens=4096)
            with gateway_session(config) as origin, patch.object(smoke_clients, "run_process", process):
                result = smoke_clients.run_client(client, executable, origin, upstream_origin, config, env, directory,
                                                 True, adapter_source, False, upstream, 45, model,
                                                 prompt="Delegate one local read-only task to a subagent, wait for its result, then summarize.",
                                                 codex_config=codex_options,
                                                 agent_model_policy="selected" if adapter_source is not None else None)
        audited = audit.exists() and audit.read_text(encoding="utf-8").splitlines() == ["read"]
        lifecycle = lifecycle_verified(client, result.stdout)
        passed = result.returncode == 0 and DONE in result.stdout and (inspect_only or state["child"] == 2 and state["parent"] == (3 if client == "codex" else 2) and (client != "codex" or audited) and lifecycle)
        return {"passed": passed, "inspect_only": inspect_only, "client": client, "upstream": upstream.value,
                "observed": observed, "schemas": schemas, "mcp_read_audited": audited,
                "lifecycle_verified": lifecycle, "cli_version": cli_version,
                "adapter_source": str(adapter_source) if adapter_source else None, "returncode": result.returncode,
                "stdout": result.stdout[-4000:], "stderr": result.stderr[-3000:]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex", "claude"])
    parser.add_argument("--upstream", type=Protocol, choices=list(Protocol))
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--adapter-source", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--fixture-file", type=Path)
    parser.add_argument("--audit-file", type=Path)
    args = parser.parse_args()
    if args.mcp:
        if not args.fixture_file or not args.audit_file:
            parser.error("MCP fixture requires its fixed file and audit path")
        smoke_tool_search.LOOKUP = "p04_read_fixture"
        smoke_tool_search.VALUE = args.fixture_file.read_text(encoding="utf-8")
        original = smoke_tool_search.json.loads

        def record(raw, *extra, **kwargs):
            request = original(raw, *extra, **kwargs)
            if request.get("method") == "tools/call" and request.get("params", {}).get("name") == "p04_read_fixture":
                with args.audit_file.open("a", encoding="utf-8") as audit:
                    audit.write("read\n")
            return request

        with patch.object(smoke_tool_search.json, "loads", record):
            smoke_tool_search.serve_mcp()
        return 0
    if not args.client or args.upstream is None:
        parser.error("Client and upstream are required")
    result = run(args.client, args.upstream, inspect_only=args.inspect_only, adapter_source=args.adapter_source)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "schemas" and (not result["passed"] or key not in {"stdout", "stderr"})}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
