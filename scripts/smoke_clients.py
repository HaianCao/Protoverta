"""Exercise installed CLI clients against scripted SSE; no provider keys needed.

Run with Protoverta installed, or PYTHONPATH=src. Client state is temporary.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

from probe_process import run_process

from protoverta.core import Protocol, convert_request
from protoverta.server import GatewayConfig, gateway_session
from protoverta.sse import encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta

MARKER = "PROTOVERTA_SMOKE_OK"


def tool_execution_failed(client, stdout, stderr):
    """Do not count tool error results as a successful agent tool round trip."""
    if client == "codex":
        return "ERROR codex_core::tools::router" in stderr or "Goal tools require a persistent thread" in stderr
    try:
        result = json.loads(stdout)
    except (ValueError, TypeError):
        return True
    return not isinstance(result, dict) or result.get("is_error") is True or bool(result.get("permission_denials"))


@contextmanager
def fixture_server(open_upstream):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            auth = (
                self.headers.get("Authorization") == "Bearer fake-upstream-key"
                or self.headers.get("x-api-key") == "fake-upstream-key"
            )
            if not auth:
                self.send_error(401)
                return
            body = self.rfile.read(int(self.headers["Content-Length"]))
            request = Request(
                "http://127.0.0.1" + self.path,
                data=body,
                headers=dict(self.headers),
                method="POST",
            )
            with open_upstream(request, timeout=10) as upstream:
                wire = upstream.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def summarize_request(body: dict) -> dict:
    return {
        "keys": sorted(body),
        "options": {
            key: body[key]
            for key in ("reasoning", "text", "parallel_tool_calls", "include")
            if key in body
        },
        "tool_types": sorted(
            {str(tool.get("type", "function")) for tool in body.get("tools", [])}
        ),
        "tool_names": [
            tool.get("name") or tool.get("function", {}).get("name")
            for tool in body.get("tools", [])
        ],
    }


def run_smoke(
    client: str,
    upstream: Protocol,
    *,
    tool_loop: bool = False,
    adapter_source: Path | None = None,
    installed_adapter: bool = False,
    timeout: int = 45,
    model: str | None = None,
    upstream_reasoning: str = "provider_default",
    mcp_fixture: bool = False,
) -> dict:
    if mcp_fixture and (client != "claude" or not tool_loop or not (adapter_source or installed_adapter)):
        raise ValueError("MCP fixture requires Claude, --tool-loop and an adapter")
    executable = shutil.which(client)
    if not executable:
        raise RuntimeError(f"{client} is not on PATH")
    requests = []
    rounds = 0
    tool_result_count = 0

    def inspect_conversion(body, source, target, **options):
        requests.append({"inbound": summarize_request(body)})
        return convert_request(body, source, target, **options)

    def open_upstream(request, *, timeout):
        nonlocal rounds, tool_result_count
        rounds += 1
        body = json.loads(request.data)
        requests.append({"upstream": summarize_request(body)})
        encoder = StreamEncoder(upstream)
        deltas = [Delta("start", model=body["model"], input_tokens=1, output_tokens=0)]
        if tool_loop and rounds == 1:
            name = "get_goal" if client == "codex" else "Read"
            available = {
                tool.get("name") or tool.get("function", {}).get("name")
                for tool in body.get("tools", [])
            }
            if mcp_fixture:
                name = next((value for value in available if value and "fixture_lookup" in value), "missing-mcp-fixture")
            if name not in available:
                raise RuntimeError(
                    f"Smoke tool {name} is absent from the client request"
                )
            arguments = (
                "{}"
                if client == "codex" or mcp_fixture
                else json.dumps({"file_path": str(task_root / "fixture.txt")})
            )
            for index in range(2):
                deltas.extend(
                    [
                        Delta(
                            "tool_start",
                            index=index,
                            call_id=f"call_smoke_{index}",
                            name=name,
                        ),
                        Delta("tool_delta", index=index, text=arguments),
                        *(
                            [Delta("tool_end", index=index)]
                            if upstream == Protocol.MESSAGES
                            else []
                        ),
                    ]
                )
            if upstream != Protocol.MESSAGES:
                deltas.extend(Delta("tool_end", index=index) for index in range(2))
            deltas.append(
                Delta("finish", reason="tool_calls", input_tokens=1, output_tokens=1)
            )
        else:
            if tool_loop:
                if mcp_fixture and "LOCAL_SEARCH_FIXTURE_VALUE" not in json.dumps(body):
                    raise ValueError("Client did not replay a successful fixture MCP result")
                if upstream == Protocol.RESPONSES:
                    tool_result_count = sum(
                        item.get("type") == "function_call_output"
                        for item in body["input"]
                    )
                elif upstream == Protocol.CHAT:
                    tool_result_count = sum(
                        item.get("role") == "tool" for item in body["messages"]
                    )
                else:
                    tool_result_count = sum(
                        block.get("type") == "tool_result"
                        for item in body["messages"]
                        for block in item.get("content", [])
                        if isinstance(block, dict)
                    )
            deltas.extend(
                [
                    Delta("text", text=MARKER),
                    Delta("finish", reason="stop", input_tokens=1, output_tokens=1),
                ]
            )
        wire = b"".join(
            encode_sse(item.event, item.data)
            for delta in deltas
            for item in encoder.feed(delta)
        )
        return io.BytesIO(wire)

    with tempfile.TemporaryDirectory(prefix="protoverta-smoke-") as directory:
        task_root = Path(directory)
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "PATH",
                "PATHEXT",
                "SYSTEMROOT",
                "WINDIR",
                "COMSPEC",
                "TEMP",
                "TMP",
            }
        }
        env.update(
            {
                "HOME": directory,
                "USERPROFILE": directory,
                "CODEX_HOME": str(task_root / "codex"),
                "CLAUDE_CONFIG_DIR": str(task_root / "claude"),
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                "CLAUDE_CODE_SIMPLE": "1",
                "PROTOVERTA_SMOKE_TOKEN": "smoke-local-token",
                # Client startup must not fetch plugin marketplaces or prompt
                # through a user's Git credential helper during local fixtures.
                "GIT_ALLOW_PROTOCOL": "file",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        (task_root / "codex").mkdir()
        (task_root / "claude").mkdir()
        if mcp_fixture:
            mcp_path = task_root / "mcp.json"
            # Windows names are case-insensitive even after env was copied to
            # a plain dict by the wrapper; exercise the actual MCP expansion.
            token_name = "PROTOVERTA_MCP_FIXTURE_TOKEN"
            if os.name == "nt":
                token_name = token_name.lower()
            mcp_path.write_text(json.dumps({"mcpServers": {"fixture": {"command": sys.executable, "args": [str(Path(__file__).with_name("smoke_tool_search.py")), "--mcp", "--mcp-verify-env"], "env_vars": ["PYTHONPATH", token_name]}}}), encoding="utf-8")
            env["CLAUDE_CA_MCP_CONFIG"] = str(mcp_path)
            env["PROTOVERTA_MCP_FIXTURE_TOKEN"] = "local-mcp-env"
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        (task_root / "fixture.txt").write_text(
            "Protoverta local tool fixture.\n", encoding="utf-8"
        )
        with (
            fixture_server(open_upstream) as upstream_origin,
            patch(
                "protoverta.server.convert_request",
                side_effect=inspect_conversion,
            ),
        ):
            reasoning_policy, public_reasoning = upstream_reasoning, False
            if reasoning_policy == "auto":
                from protoverta.automatic_reasoning import automatic_reasoning

                reasoning_policy, public_reasoning = automatic_reasoning("responses" if client == "codex" else "messages", upstream, upstream_origin + "/v1")
            config = GatewayConfig(
                upstream,
                upstream_origin + "/v1",
                "fake-upstream-key",
                "smoke-local-token",
                client_profile="codex" if client == "codex" else "claude",
                default_max_output_tokens=4096,
                upstream_reasoning=reasoning_policy,
                public_reasoning=public_reasoning,
                messages_usage_policy="deferred"
                if client == "claude" and upstream != Protocol.MESSAGES
                else "strict",
            )
            with gateway_session(config) as origin:
                result = run_client(
                    client,
                    executable,
                    origin,
                    upstream_origin,
                    config,
                    env,
                    directory,
                    tool_loop,
                    adapter_source,
                    installed_adapter,
                    upstream,
                    timeout,
                    model,
                    mcp_tool_name="mcp__fixture__fixture_lookup" if mcp_fixture else None,
                    adapter_reasoning_policy="auto" if upstream_reasoning == "auto" else None,
                )
        return {
            "client": client,
            "upstream": upstream.value,
            "adapter": "installed"
            if installed_adapter
            else (str(adapter_source) if adapter_source else None),
            "passed": result.returncode == 0
            and MARKER in result.stdout
            and (not tool_loop or (rounds == 2 and tool_result_count == 2 and not tool_execution_failed(client, result.stdout, result.stderr))),
            "returncode": result.returncode,
            "requests": requests,
            "rounds": rounds,
            "tool_result_count": tool_result_count,
            "stdout": result.stdout[-3000:],
            "stderr": result.stderr[-3000:],
        }


def run_client(
    client,
    executable,
    origin,
    upstream_origin,
    config,
    env,
    directory,
    tool_loop,
    adapter_source,
    installed_adapter,
    upstream,
    timeout,
    model,
    prompt: str | None = None,
    persist_session: bool = False,
    resume_id: str | None = None,
    provider_id: str = "smoke",
    trace_file: Path | None = None,
    codex_config: dict | None = None,
    mcp_tool_name: str | None = None,
    adapter_reasoning_policy: str | None = None,
):
    if client == "codex":
        settings = {
            "model_provider": "protoverta_smoke",
            "model_providers.protoverta_smoke.name": "Protoverta smoke",
            "model_providers.protoverta_smoke.base_url": origin + "/v1",
            "model_providers.protoverta_smoke.env_key": "PROTOVERTA_SMOKE_TOKEN",
            "model_providers.protoverta_smoke.wire_api": "responses",
            "model_providers.protoverta_smoke.requires_openai_auth": False,
            "model_providers.protoverta_smoke.supports_websockets": False,
            "model_providers.protoverta_smoke.request_max_retries": 0,
            "model_providers.protoverta_smoke.stream_max_retries": 0,
            "web_search": "disabled",
            "model_reasoning_summary": "none",
            "features.multi_agent": False,
            # Keep persistent fixture threads from starting a background daemon
            # that can inherit captured pipes beyond the isolated CLI lifetime.
            "features.daemon_auto_start": False,
            "features.plugins": False,
            "features.apps": False,
            "features.remote_plugin": False,
            "check_for_update_on_startup": False,
        }
        settings.update(codex_config or {})
        args = [
            executable,
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--json",
            "--sandbox",
            "read-only",
            "-m",
            model or "protoverta-smoke",
        ]
        if tool_loop:
            args.remove("--ephemeral")
        for key, value in settings.items():
            args += ["-c", f"{key}={json.dumps(value)}"]
        args.append(prompt or "Reply with PROTOVERTA_SMOKE_OK. Do not use tools.")
    else:
        env["ANTHROPIC_BASE_URL"] = origin
        env["ANTHROPIC_AUTH_TOKEN"] = config.inbound_token
        if upstream != Protocol.MESSAGES:
            env.update(
                {
                    "MAX_THINKING_TOKENS": "0",
                    "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": "1",
                    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
                    "CLAUDE_CODE_DISABLE_STRUCTURED_OUTPUTS": "1",
                    "DISABLE_PROMPT_CACHING": "1",
                    "ENABLE_TOOL_SEARCH": "false",
                }
            )
        args = [
            executable,
            "-p",
            "--model",
            model or "claude-sonnet-4-6",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--tools",
            "Read" if tool_loop else "",
            "--allowedTools",
            mcp_tool_name or "Read",
            "--no-session-persistence",
            "--system-prompt",
            "This is a local protocol smoke test.",
            "--output-format",
            "json",
            prompt or "Reply with PROTOVERTA_SMOKE_OK.",
        ]
    if adapter_source is not None or installed_adapter:
        adapter_policy = adapter_reasoning_policy or config.upstream_reasoning
        config_path = Path(directory) / "adapter.toml"
        if adapter_source is not None:
            env["PYTHONPATH"] = os.pathsep.join(
                [
                    str(adapter_source.resolve() / "src"),
                    str(Path(__file__).resolve().parents[1] / "src"),
                ]
            )
        env["SMOKE_TOKEN"] = config.upstream_api_key
        if client == "codex":
            protocol = {
                Protocol.CHAT: "chat_completions",
                Protocol.MESSAGES: "anthropic_messages",
                Protocol.RESPONSES: "responses",
            }[upstream]
            config_path.write_text(
                f'schema_version = 1\n[providers.smoke]\ndisplay_name = "Smoke"\nbase_url = "{upstream_origin}/v1"\nprotocol = "{protocol}"\nexecution_mode = "gateway"\nupstream_reasoning = "{adapter_policy}"\npublic_reasoning = {str(config.public_reasoning and adapter_policy != "auto").lower()}\nclient_tool_search = "{config.client_tool_search}"\nauth_secret_ref = "smoke-main"\n[secrets.smoke-main]\nbackend = "environment"\nenv_var = "SMOKE_TOKEN"\n',
                encoding="utf-8",
            )
            options = [
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--json",
                "--sandbox",
                "read-only",
            ]
            if not tool_loop:
                options.append("--ephemeral")
            args = [
                sys.executable,
                "-m",
                "codex_adapter.cli",
                "--config",
                str(config_path),
                "exec",
                "-p",
                "smoke",
                "-m",
                model or "protoverta-smoke",
                "--",
                *options,
                prompt or "Reply with PROTOVERTA_SMOKE_OK.",
            ]
        else:
            base = (
                upstream_origin
                if upstream == Protocol.MESSAGES
                else upstream_origin + "/v1"
            )
            config_path.write_text(
                f'schema_version = 1\n[[providers]]\nid = "smoke"\ndisplay_name = "Smoke"\nbase_url = "{base}"\nprotocol = "{upstream.value}"\nexecution_mode = "gateway"\nupstream_reasoning = "{adapter_policy}"\n',
                encoding="utf-8",
            )
            env["CLAUDE_CA_CONFIG"] = str(config_path)
            args = [
                sys.executable,
                "-m",
                "claudapter.cli",
                "print",
                "-p",
                "smoke",
                "-m",
                model or "claude-sonnet-4-6",
                "--",
                *args[4:],
            ]
        if provider_id != "smoke":
            content = config_path.read_text(encoding="utf-8")
            content = content.replace("[providers.smoke]", f"[providers.{provider_id}]").replace('id = "smoke"', f'id = "{provider_id}"')
            config_path.write_text(content, encoding="utf-8")
            args[args.index("-p") + 1] = provider_id
            if client == "claude":
                # ClaudApter derives its credential variable from the provider
                # ID. Model/provider replay must not accidentally reuse smoke's
                # credential mapping or leak that extra variable to the CLI.
                env.pop("SMOKE_TOKEN", None)
                env[provider_id.upper().replace("-", "_") + "_TOKEN"] = config.upstream_api_key
    if persist_session:
        args = [value for value in args if value not in {"--ephemeral", "--no-session-persistence"}]
    if resume_id:
        if client == "codex":
            at = args.index("--") + 1 if adapter_source is not None or installed_adapter else args.index("exec") + 1
            args[at:at] = ["resume", resume_id]
            if "--sandbox" in args:
                at = args.index("--sandbox")
                del args[at:at + 2]
            # exec resume has no --sandbox flag of its own. Preserve the first
            # invocation's read-only policy explicitly at Codex's root scope.
            if adapter_source is not None or installed_adapter:
                args[args.index("exec")] = "run"
                at = args.index("--") + 1
                args[at:at] = ["--sandbox", "read-only", "exec"]
            else:
                at = args.index("exec")
                args[at:at] = ["--sandbox", "read-only"]
        else:
            args[-1:-1] = ["--resume", resume_id]
    if client == "codex" and (adapter_source is not None or installed_adapter):
        # Real-provider probes must not multiply failed requests through the
        # CLI's default reconnect policy. These overrides belong at root scope.
        at = args.index("--") + 1
        retry_options = ["-c", f"model_providers.{provider_id}.request_max_retries=0", "-c", f"model_providers.{provider_id}.stream_max_retries=0", "-c", "features.daemon_auto_start=false"]
        for key in ("features.plugins", "features.apps", "features.remote_plugin", "check_for_update_on_startup"):
            retry_options += ["-c", key + "=false"]
        for key, value in (codex_config or {}).items():
            retry_options += ["-c", f"{key}={json.dumps(value)}"]
        if not resume_id:
            args[args.index("exec")] = "run"
            retry_options.append("exec")
        args[at:at] = retry_options
    if client == "codex":
        # Explicitly bypass an existing/shared daemon too. The feature flag
        # alone controls auto-start, not attachment to a background server.
        at = args.index("--") + 1 if adapter_source is not None or installed_adapter else 1
        args[at:at] = ["--no-daemon"]
    if trace_file is not None:
        if adapter_source is None:
            raise ValueError("Tracing requires a source adapter and synthetic state")
        module_index = args.index("-m")
        module_name = args[module_index + 1]
        args[module_index:module_index + 2] = [str(Path(__file__).with_name("trace_adapter.py")), module_name, str(trace_file)]
    return run_process(
        args,
        cwd=directory,
        env=env,
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex", "claude"], required=True)
    parser.add_argument("--upstream-reasoning", choices=["auto", "provider_default", "disabled", "openrouter_disabled"], default="provider_default")
    parser.add_argument("--mcp-fixture", action="store_true", help="Claude adapter: execute the fixed read-only MCP tool")
    parser.add_argument(
        "--upstream", type=Protocol, choices=list(Protocol), required=True
    )
    parser.add_argument(
        "--model",
        help="Exercise client behavior for an arbitrary model ID against the local fixture",
    )
    parser.add_argument(
        "--tool-loop",
        action="store_true",
        help="Execute two read-only client tools before the final answer",
    )
    adapter = parser.add_mutually_exclusive_group()
    adapter.add_argument(
        "--adapter-source",
        type=Path,
        help="Also exercise the consumer launcher from this source checkout",
    )
    adapter.add_argument(
        "--installed-adapter",
        action="store_true",
        help="Exercise the consumer installed in this Python environment",
    )
    args = parser.parse_args()
    result = run_smoke(
        args.client,
        args.upstream,
        tool_loop=args.tool_loop,
        adapter_source=args.adapter_source,
        installed_adapter=args.installed_adapter,
        model=args.model,
        upstream_reasoning=args.upstream_reasoning,
        mcp_fixture=args.mcp_fixture,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
