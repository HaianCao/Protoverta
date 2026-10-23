"""Opt-in real-provider text/tool probe using isolated consumer and CLI state.

Sends synthetic prompts only. Requires a key already in the named environment
variable. No credentials, client state, or provider responses are saved.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.request import build_opener

from smoke_clients import MARKER, run_client
from smoke_resume import session_id

from protoverta.core import Protocol
from protoverta.server import GatewayConfig


def _probe_codex_goal(upstream, base_url, key, model, adapter_source, env, directory, timeout):
    """Observe the real transport; use a read-only tool, never bypass shell policy."""
    import sys

    sys.path.insert(0, str(adapter_source.resolve() / "src"))
    from codex_adapter.config import Config, Provider, SecretReference
    from codex_adapter.launcher import launch

    from protoverta import server

    provider = Provider("smoke", "Smoke", base_url, protocol={Protocol.CHAT: "chat_completions", Protocol.MESSAGES: "anthropic_messages", Protocol.RESPONSES: "responses"}[upstream], execution_mode="gateway", auth_secret_ref="smoke-main", upstream_reasoning="openrouter_disabled")
    config = Config(providers={"smoke": provider}, secrets={"smoke-main": SecretReference("smoke-main", env_var="SMOKE_TOKEN")}, path=Path(directory) / "adapter.toml")
    env = dict(env, SMOKE_TOKEN=key)
    completed = None
    tool_results = set()
    requests = 0
    real_handler, real_run = server.make_handler, subprocess.run
    opener = build_opener(server._RedirectBlocker())

    def observed_open(request, *, timeout):
        nonlocal requests
        requests += 1
        body = json.loads(request.data)
        if upstream == Protocol.CHAT:
            calls = {call["id"]: call["function"]["name"] for item in body["messages"] for call in item.get("tool_calls", [])}
            results = [item["tool_call_id"] for item in body["messages"] if item.get("role") == "tool"]
        elif upstream == Protocol.RESPONSES:
            calls = {item["call_id"]: item["name"] for item in body["input"] if item.get("type") == "function_call"}
            results = [item["call_id"] for item in body["input"] if item.get("type") == "function_call_output"]
        else:
            blocks = [block for item in body["messages"] for block in item["content"] if isinstance(block, dict)]
            calls = {block["id"]: block["name"] for block in blocks if block.get("type") == "tool_use"}
            results = [block["tool_use_id"] for block in blocks if block.get("type") == "tool_result"]
        tool_results.update(call_id for call_id in results if calls.get(call_id) == "get_goal")
        return opener.open(request, timeout=timeout)

    def run(args, **kwargs):
        nonlocal completed
        # All retry overrides belong to Codex's root scope, not exec's scope.
        at = args.index("exec")
        args[at:at] = ["-c", "model_providers.smoke.request_max_retries=0", "-c", "model_providers.smoke.stream_max_retries=0"]
        kwargs.update(cwd=directory, capture_output=True, text=True, encoding="utf-8", errors="replace", input="", timeout=timeout)
        completed = real_run(args, **kwargs)
        return completed

    with patch.dict(os.environ, env, clear=True), patch("protoverta.server.make_handler", side_effect=lambda config: real_handler(config, upstream_open=observed_open)), patch("codex_adapter.launcher.subprocess.run", side_effect=run):
        launch(config, provider, model, "low", ["exec", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--json", "--sandbox", "read-only", "Call get_goal exactly once. Do not create or update any goal. Do not use shell, files, network, or other tools. After receiving the tool result, reply with PROTOVERTA_SMOKE_OK."])
    return completed, bool(tool_results), requests


def _completed_answer(client: str, stdout: str) -> tuple[str, bool]:
    """Require final assistant output, not just a nonce echoed by a tool event."""
    if client == "claude":
        result = json.loads(stdout)
        return str(result.get("result", "")), not result.get("is_error", True) and not result.get("permission_denials") and result.get("num_turns", 0) >= 2
    answer = []
    executed_tool = False
    for line in stdout.splitlines():
        event = json.loads(line)
        item = event.get("item", {})
        if event.get("type") == "item.completed":
            if item.get("type") == "agent_message":
                answer.append(item.get("text", ""))
            elif item.get("type") == "command_execution" and item.get("exit_code") == 0:
                executed_tool = True
    return "\n".join(answer), executed_tool


def probe(client: str, upstream: Protocol, base_url: str, key: str, model: str, adapter_source: Path, *, timeout: int = 120, tool_loop: bool = False) -> dict:
    executable = shutil.which(client)
    if not executable:
        raise ValueError(f"{client} is not on PATH")
    config = GatewayConfig(upstream, base_url, key, "isolated-probe", upstream_reasoning="openrouter_disabled")
    # run_client receives the prefix before /v1; validate rather than guess it.
    if not base_url.endswith("/v1"):
        raise ValueError("This OpenRouter probe requires an explicit /v1 API prefix")
    with tempfile.TemporaryDirectory(prefix="protoverta-client-probe-") as directory:
        root = Path(directory)
        env = {name: value for name, value in os.environ.items() if name.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"}}
        env.update({"HOME": directory, "USERPROFILE": directory, "APPDATA": str(root / "roaming"), "LOCALAPPDATA": str(root / "local"), "CODEX_HOME": str(root / "codex"), "CLAUDE_CONFIG_DIR": str(root / "claude"), "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_SIMPLE": "1"})
        for name in ("roaming", "local", "codex", "claude"):
            (root / name).mkdir()
        expected = MARKER
        prompt = None
        if tool_loop and client != "codex":
            expected = MARKER + "_" + secrets.token_hex(12)
            fixture = root / "fixture.txt"
            fixture.write_text(expected + "\n", encoding="utf-8")
            prompt = f"Read the file {fixture.as_posix()} using {'only the Read tool' if client == 'claude' else 'a read-only shell command'}. Then reply with exactly its full contents. Do not read any other files, write anything, use network access, or delegate."
        observed_tool, requests = False, None
        if tool_loop and client == "codex":
            result, observed_tool, requests = _probe_codex_goal(upstream, base_url, key, model, adapter_source, env, directory, timeout)
        else:
            result = run_client(client, executable, "http://127.0.0.1:0", base_url.removesuffix("/v1"), config, env, directory, tool_loop, adapter_source, False, upstream, timeout, model, prompt=prompt)
        # Never print raw request/response bodies or credentials. Retain only
        # status and a bounded, key-redacted error tail on failure.
        try:
            answer, executed_tool = _completed_answer(client, result.stdout) if result.returncode == 0 else ("", False)
        except (ValueError, TypeError):
            answer, executed_tool = "", False
        executed_tool = executed_tool or observed_tool
        passed = result.returncode == 0 and expected in answer and (not tool_loop or executed_tool)
        return {"client": client, "upstream": upstream.value, "model": model, "tool_loop": tool_loop, "passed": passed, "tool_verified": executed_tool if tool_loop else None, "requests": requests, "returncode": result.returncode, "error": "" if passed else (result.stderr + result.stdout)[-2500:].replace(key, "[REDACTED]")}


def probe_resume(client: str, source: Protocol, target: Protocol, base_url: str, key: str, model: str, adapter_source: Path, *, timeout: int = 120) -> dict:
    """Two short synthetic API turns; replay one nonce on the selected model."""
    executable = shutil.which(client)
    if not executable:
        raise ValueError(f"{client} is not on PATH")
    if not base_url.endswith("/v1"):
        raise ValueError("This probe requires an explicit /v1 API prefix")
    nonce = "RESUME_NONCE_" + secrets.token_hex(12)
    with tempfile.TemporaryDirectory(prefix="protoverta-live-resume-") as directory:
        root = Path(directory)
        env = {name: value for name, value in os.environ.items() if name.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"}}
        env.update({"HOME": directory, "USERPROFILE": directory, "APPDATA": str(root / "roaming"), "LOCALAPPDATA": str(root / "local"), "CODEX_HOME": str(root / "codex"), "CLAUDE_CONFIG_DIR": str(root / "claude"), "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_SIMPLE": "1"})
        for name in ("roaming", "local", "codex", "claude"):
            (root / name).mkdir()
        config = GatewayConfig(source, base_url, key, "isolated-probe", upstream_reasoning="openrouter_disabled")
        first = run_client(client, executable, "http://127.0.0.1:0", base_url.removesuffix("/v1"), config, env, directory, False, adapter_source, False, source, timeout, model, prompt=f"Remember this synthetic token for my next message: {nonce}. Reply ACK only. Do not use tools.", persist_session=True)
        if first.returncode != 0:
            return {"passed": False, "client": client, "stage": "initial", "error": (first.stderr + first.stdout)[-2000:].replace(key, "[REDACTED]")}
        identifier = session_id(client, first.stdout)
        config = GatewayConfig(target, base_url, key, "isolated-probe", upstream_reasoning="openrouter_disabled")
        second = run_client(client, executable, "http://127.0.0.1:0", base_url.removesuffix("/v1"), config, env, directory, False, adapter_source, False, target, timeout, model, prompt="Return only the exact synthetic token I asked you to remember in my previous message. Do not use tools.", persist_session=True, resume_id=identifier, provider_id="alternate")
        try:
            answer, _ = _completed_answer(client, second.stdout)
            same_session = session_id(client, second.stdout) == identifier
        except (ValueError, KeyError, StopIteration):
            answer, same_session = "", False
        passed = second.returncode == 0 and same_session and nonce in answer
        return {"passed": passed, "client": client, "source": source.value, "target": target.value, "model": model, "same_session": same_session, "nonce_recalled": nonce in answer, "error": "" if passed else (second.stderr + second.stdout)[-2500:].replace(key, "[REDACTED]")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex", "claude"], required=True)
    parser.add_argument("--upstream", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--key-env", required=True)
    parser.add_argument("--base-url", default="https://openrouter.ai/api/v1")
    parser.add_argument("--adapter-source", type=Path, required=True)
    parser.add_argument("--tool-loop", action="store_true", help="Verify a real tool round trip: Claude reads a nonce file; Codex queries get_goal")
    parser.add_argument("--resume-target", type=Protocol, choices=list(Protocol), help="Run two persistent text turns, resuming against this protocol")
    parser.add_argument("--allow-live", action="store_true", help="Acknowledge real API calls and possible charges")
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("Live API calls require --allow-live")
    if args.resume_target and args.tool_loop:
        parser.error("Real-provider resume currently supports text only; do not combine with --tool-loop")
    key = os.environ.get(args.key_env)
    if not key:
        parser.error("The configured key environment variable is empty")
    result = probe_resume(args.client, args.upstream, args.resume_target, args.base_url, key, args.model, args.adapter_source) if args.resume_target else probe(args.client, args.upstream, args.base_url, key, args.model, args.adapter_source, tool_loop=args.tool_loop)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
