"""Opt-in real Codex client-search smoke with a read-only local MCP fixture.

No provider calls, user config, real sessions, or global MCP registrations.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from smoke_clients import MARKER, fixture_server, run_client, tool_execution_failed

from protoverta.core import Protocol
from protoverta.server import GatewayConfig, gateway_session
from protoverta.sse import encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta

LOOKUP = "fixture_lookup"
VALUE = "LOCAL_SEARCH_FIXTURE_VALUE"


def serve_mcp(*, verify_env=False):
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        method = request.get("method")
        if method == "initialize":
            result = {"protocolVersion": request["params"]["protocolVersion"], "capabilities": {"tools": {}}, "serverInfo": {"name": "protoverta-read-only-fixture", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": LOOKUP, "description": "Return a fixed synthetic test value; no I/O or side effects.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}}]}
        elif method == "tools/call" and request.get("params", {}).get("name") == LOOKUP:
            env_ok = not verify_env or os.environ.get("PROTOVERTA_MCP_FIXTURE_TOKEN") == "local-mcp-env"
            result = {"content": [{"type": "text", "text": VALUE if env_ok else "Fixture environment reference did not resolve"}], "isError": not env_ok}
        elif method == "resources/list":
            result = {"resources": []}
        elif method == "prompts/list":
            result = {"prompts": []}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Unsupported fixture method"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)


def functions(body, protocol):
    tools = list(body.get("tools", []))
    if protocol == Protocol.RESPONSES:
        for item in body.get("input", []):
            if isinstance(item, dict) and item.get("type") == "tool_search_output":
                tools.extend(item.get("tools", []))
    for tool in tools:
        if protocol == Protocol.CHAT and tool.get("type") == "function":
            yield tool["function"]
        elif protocol == Protocol.MESSAGES:
            yield {**tool, "parameters": tool.get("input_schema", {})}
        elif protocol == Protocol.RESPONSES:
            if tool.get("type") == "namespace":
                for child in tool["tools"]:
                    yield {**child, "namespace": tool["name"]}
            else:
                yield tool


def search_arguments(schema):
    """Use only the query/limit fields actually advertised by this client."""
    result = {}
    for key, value in schema.get("properties", {}).items():
        if key not in schema.get("required", []) and key not in ("query", "limit"):
            continue
        kind = value.get("type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "string" in kinds and key in ("query", "goal"):
            result[key] = LOOKUP
        elif any(kind in kinds for kind in ("integer", "number")) and key in ("limit", "max_results"):
            result[key] = 1
        else:
            raise ValueError("Unsupported required client search argument: " + key)
    return result


def lookup_completed(stdout):
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or not isinstance(event.get("item", {}), dict):
            continue
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "mcp_tool_call" and item.get("server") == "fixture" and item.get("tool") == LOOKUP:
            result = item.get("result")
            return item.get("status") == "completed" and item.get("error") is None and isinstance(result, dict) and {"type": "text", "text": VALUE} in (result.get("content") or [])
    return False


def run(upstream, adapter_source=None, installed_adapter=False):
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("codex is not on PATH")
    rounds, observed = 0, []
    with tempfile.TemporaryDirectory(prefix="protoverta-search-") as directory:
        root = Path(directory)
        env = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"}}
        env.update(HOME=directory, USERPROFILE=directory, APPDATA=str(root / "roaming"), LOCALAPPDATA=str(root / "local"), CODEX_HOME=str(root / "codex"), PROTOVERTA_SMOKE_TOKEN="local-search-token")
        env.update(GIT_ALLOW_PROTOCOL="file", GIT_TERMINAL_PROMPT="0")
        (root / "codex").mkdir()
        # Read only the binary's bundled metadata, never query a model provider.
        catalog = json.loads(subprocess.run([executable, "debug", "models", "--bundled"], env=env, capture_output=True, text=True, encoding="utf-8", check=True, timeout=15).stdout)
        entries = catalog["models"] if isinstance(catalog, dict) else catalog
        model = dict(next(item for item in entries if item["slug"] == "gpt-5.5"))
        model.update(slug="protoverta-search-fixture", display_name="Local search fixture", supports_search_tool=True, support_verbosity=False, apply_patch_tool_type=None, tool_mode=None, experimental_supported_tools=[])
        catalog_path = root / "models.json"
        catalog_path.write_text(json.dumps({"models": [model]}), encoding="utf-8")
        from harness_policy import codex_settings

        options = {"model_catalog_json": str(catalog_path), "mcp_servers.fixture.command": sys.executable, "mcp_servers.fixture.args": [str(Path(__file__).resolve()), "--mcp"], "mcp_servers.fixture.required": True, "features.daemon_auto_start": False, "features.apps": False, "features.plugins": False}
        options.update(codex_settings(executable, adapter_source))
        if adapter_source is not None or installed_adapter:
            # Exercise the wrapper's session-scoped integration, not only raw
            # Codex -c options. The catalog belongs only to this fixture model.
            mcp_path = root / "mcp.json"
            mcp_path.write_text(json.dumps({"mcpServers": {"fixture": {"command": sys.executable, "args": [str(Path(__file__).resolve()), "--mcp", "--mcp-verify-env"], "env_vars": ["PYTHONPATH", "PROTOVERTA_MCP_FIXTURE_TOKEN"]}}}), encoding="utf-8")
            env.update(CODEX_CA_MCP_CONFIG=str(mcp_path), CODEX_CA_MODEL_CATALOG=str(catalog_path))
            env["PROTOVERTA_MCP_FIXTURE_TOKEN"] = "local-mcp-env"
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
            options = {key: value for key, value in options.items() if not key.startswith("mcp_servers.") and key != "model_catalog_json"}

        def open_upstream(request, *, timeout):
            nonlocal rounds
            rounds += 1
            body = json.loads(request.data)
            tools = list(functions(body, upstream))
            observed.append({"tool_types": [tool.get("type", "function") for tool in tools], "names": [tool.get("name") for tool in tools]})
            if upstream == Protocol.RESPONSES:
                observed[-1]["search_outputs"] = [item for item in body.get("input", []) if isinstance(item, dict) and item.get("type") == "tool_search_output"]
            encoder = StreamEncoder(upstream)
            deltas = [Delta("start", model=body["model"], input_tokens=1, output_tokens=0)]
            if rounds == 1:
                search = next((tool for tool in tools if tool.get("type") == "tool_search" or tool.get("name") == "pv_client_tool_search"), None)
                if search is None:
                    raise ValueError("This client did not advertise native client tool search")
                observed[-1]["search_parameters"] = search["parameters"]
                args = search_arguments(search["parameters"])
                deltas += [Delta("tool_start", index=0, call_id="search_fixture_1", name=search.get("name", "pv_client_tool_search"), tool_type="tool_search" if upstream == Protocol.RESPONSES else "function"), Delta("tool_delta", index=0, text=json.dumps(args)), Delta("tool_end", index=0), Delta("finish", reason="tool_calls", input_tokens=1, output_tokens=1)]
            elif rounds == 2:
                loaded = next((tool for tool in tools if LOOKUP in tool.get("name", "") or "Return a fixed synthetic test value" in tool.get("description", "")), None)
                if loaded is None:
                    raise ValueError("Client search did not load the fixture tool")
                deltas += [Delta("tool_start", index=0, call_id="lookup_fixture_1", name=loaded["name"]), Delta("tool_delta", index=0, text="{}"), Delta("tool_end", index=0), Delta("finish", reason="tool_calls", input_tokens=1, output_tokens=1)]
            elif rounds == 3:
                if VALUE not in json.dumps(body):
                    raise ValueError("The client did not return the successful MCP tool result")
                deltas += [Delta("text", text=MARKER), Delta("finish", reason="stop", input_tokens=1, output_tokens=1)]
            else:
                raise ValueError("Unexpected extra model round")
            wire = []
            for delta in deltas:
                for event in encoder.feed(delta):
                    data = event.data
                    # The generic encoder has no namespace metadata; this native
                    # control fixture preserves the client's loaded namespace.
                    if upstream == Protocol.RESPONSES and rounds == 2:
                        data = json.loads(data)
                        items = [data.get("item", {})] + data.get("response", {}).get("output", [])
                        for item in items:
                            if item.get("type") == "function_call":
                                item["namespace"] = loaded["namespace"]
                        data = json.dumps(data)
                    wire.append(encode_sse(event.event, data))
            return io.BytesIO(b"".join(wire))

        with fixture_server(open_upstream) as upstream_origin:
            config = GatewayConfig(upstream, upstream_origin + "/v1", "fake-upstream-key", "local-search-token", client_profile="codex", client_tool_search="reject" if upstream == Protocol.RESPONSES else "function", namespace_tools="flatten")
            with gateway_session(config) as origin:
                result = run_client("codex", executable, origin, upstream_origin, config, env, directory, False, adapter_source, installed_adapter, upstream, 25, model["slug"], codex_config=options)
        passed = result.returncode == 0 and rounds == 3 and MARKER in result.stdout and lookup_completed(result.stdout) and not tool_execution_failed("codex", result.stdout, result.stderr)
        return {"passed": passed, "upstream": upstream.value, "rounds": rounds, "observed": observed, "returncode": result.returncode, "stdout": result.stdout[-3000:], "stderr": result.stderr[-3000:]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--mcp-verify-env", action="store_true")
    parser.add_argument("--upstream", type=Protocol, choices=list(Protocol), default=Protocol.CHAT)
    adapter = parser.add_mutually_exclusive_group()
    adapter.add_argument("--adapter-source", type=Path)
    adapter.add_argument("--installed-adapter", action="store_true")
    args = parser.parse_args()
    if args.mcp:
        serve_mcp(verify_env=args.mcp_verify_env)
        return 0
    result = run(args.upstream, args.adapter_source, args.installed_adapter)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
