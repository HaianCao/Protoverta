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
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch
from urllib.request import build_opener

from probe_process import run_process

_REAL_SUBPROCESS_RUN = subprocess.run  # bound before any launcher patch
from smoke_clients import MARKER, run_client
from smoke_resume import session_id
from stream_diagnostics import StreamDiagnostics

from protoverta.core import Protocol
from protoverta.server import GatewayConfig


def _probe_codex_goal(
    upstream,
    base_url,
    key,
    model,
    adapter_source,
    env,
    directory,
    timeout,
    *,
    nonce=None,
    reasoning="openrouter_disabled",
    diagnostics=None,
):
    """Observe the real transport; use a read-only tool, never bypass shell policy."""
    import sys

    sys.path.insert(0, str(adapter_source.resolve() / "src"))
    from codexapter.config import Config, Provider, SecretReference
    from codexapter.launcher import launch

    from protoverta import server

    provider = Provider(
        "smoke",
        "Smoke",
        base_url,
        protocol={
            Protocol.CHAT: "chat_completions",
            Protocol.MESSAGES: "anthropic_messages",
            Protocol.RESPONSES: "responses",
        }[upstream],
        execution_mode="gateway",
        auth_secret_ref="smoke-main",
        upstream_reasoning=reasoning,
    )
    config = Config(
        providers={"smoke": provider},
        secrets={"smoke-main": SecretReference("smoke-main", env_var="SMOKE_TOKEN")},
        path=Path(directory) / "adapter.toml",
    )
    env = dict(env, SMOKE_TOKEN=key)
    completed = None
    tool_results = set()
    requests = 0
    real_handler = server.make_handler
    opener = build_opener(server._RedirectBlocker())
    open_request = diagnostics.wrap_open(opener.open) if diagnostics is not None else opener.open

    def observed_open(request, *, timeout):
        nonlocal requests
        requests += 1
        body = json.loads(request.data)
        if upstream == Protocol.CHAT:
            calls = {
                call["id"]: call["function"]["name"] for item in body["messages"] for call in item.get("tool_calls", [])
            }
            results = [item["tool_call_id"] for item in body["messages"] if item.get("role") == "tool"]
        elif upstream == Protocol.RESPONSES:
            calls = {item["call_id"]: item["name"] for item in body["input"] if item.get("type") == "function_call"}
            results = [item["call_id"] for item in body["input"] if item.get("type") == "function_call_output"]
        else:
            blocks = [block for item in body["messages"] for block in item["content"] if isinstance(block, dict)]
            calls = {block["id"]: block["name"] for block in blocks if block.get("type") == "tool_use"}
            results = [block["tool_use_id"] for block in blocks if block.get("type") == "tool_result"]
        tool_results.update(call_id for call_id in results if calls.get(call_id) == "get_goal")
        return open_request(request, timeout=timeout)

    def run(args, **kwargs):
        nonlocal completed
        if "exec" not in args:
            # Launcher helpers (for example the isolated `codex --version`
            # probe) share this patched module; run them unchanged.
            return _REAL_SUBPROCESS_RUN(args, **kwargs)
        # All retry overrides belong to Codex's root scope, not exec's scope.
        at = args.index("exec")
        args[at:at] = [
            "-c",
            "model_providers.smoke.request_max_retries=0",
            "-c",
            "model_providers.smoke.stream_max_retries=0",
        ]
        kwargs.update(
            cwd=directory, capture_output=True, text=True, encoding="utf-8", errors="replace", input="", timeout=timeout
        )
        completed = run_process(args, **kwargs)
        return completed

    with (
        diagnostics.capture(observe_transport=False) if diagnostics is not None else nullcontext(),
        patch.dict(os.environ, env, clear=True),
        patch(
            "protoverta.server.make_handler",
            side_effect=lambda config, **options: real_handler(config, upstream_open=observed_open, **options),
        ),
        patch("codexapter.launcher.subprocess.run", side_effect=run),
    ):
        marker = nonce or "PROTOVERTA_SMOKE_OK"
        launch(
            config,
            provider,
            model,
            "low",
            [
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--json",
                "--sandbox",
                "read-only",
                f"Call get_goal exactly once. Do not create or update any goal. Do not use shell, files, network, or other tools. After receiving the tool result, reply with {marker}.",
            ],
        )
    return completed, bool(tool_results), requests


def _completed_answer(client: str, stdout: str) -> tuple[str, bool]:
    """Require final assistant output, not just a nonce echoed by a tool event."""
    if client == "claude":
        result = json.loads(stdout)
        return str(result.get("result", "")), not result.get("is_error", True) and not result.get(
            "permission_denials"
        ) and result.get("num_turns", 0) >= 2
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


def probe(
    client: str,
    upstream: Protocol,
    base_url: str,
    key: str,
    model: str,
    adapter_source: Path,
    *,
    timeout: int = 120,
    tool_loop: bool = False,
    reasoning: str = "openrouter_disabled",
    diagnostics: bool = False,
    max_upstream_requests: int | None = None,
) -> dict:
    if max_upstream_requests is not None and not diagnostics:
        raise ValueError("A request budget requires diagnostics mode")
    executable = shutil.which(client)
    if not executable:
        raise ValueError(f"{client} is not on PATH")
    # "auto" is a launcher policy; validate the gateway with its explicit base.
    config = GatewayConfig(
        upstream,
        base_url,
        key,
        "isolated-probe",
        upstream_reasoning="provider_default" if reasoning == "auto" else reasoning,
    )
    # run_client receives the prefix before /v1; validate rather than guess it.
    if not base_url.endswith("/v1"):
        raise ValueError("This probe requires an explicit /v1 API prefix")
    with tempfile.TemporaryDirectory(prefix="protoverta-client-probe-") as directory:
        root = Path(directory)
        env = {
            name: value
            for name, value in os.environ.items()
            if name.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"}
        }
        env.update(
            {
                "HOME": directory,
                "USERPROFILE": directory,
                "APPDATA": str(root / "roaming"),
                "LOCALAPPDATA": str(root / "local"),
                "CODEX_HOME": str(root / "codex"),
                "CLAUDE_CONFIG_DIR": str(root / "claude"),
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                "CLAUDE_CODE_SIMPLE": "1",
            }
        )
        if max_upstream_requests is not None:
            env["PROTOVERTA_PROBE_REQUEST_LIMIT"] = str(max_upstream_requests)
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
        diagnostic = StreamDiagnostics(max_upstream_requests=max_upstream_requests) if diagnostics else None
        trace_file = root / "trace.json" if diagnostics else None
        if tool_loop and client == "codex":
            result, observed_tool, requests = _probe_codex_goal(
                upstream,
                base_url,
                key,
                model,
                adapter_source,
                env,
                directory,
                timeout,
                reasoning=reasoning,
                diagnostics=diagnostic,
            )
        else:
            result = run_client(
                client,
                executable,
                "http://127.0.0.1:0",
                base_url.removesuffix("/v1"),
                config,
                env,
                directory,
                tool_loop,
                adapter_source,
                False,
                upstream,
                timeout,
                model,
                prompt=prompt,
                trace_file=trace_file,
                adapter_reasoning_policy="auto" if reasoning == "auto" else None,
            )
        report = diagnostic.snapshot() if diagnostic is not None else None
        if trace_file is not None and trace_file.is_file():
            observations = json.loads(trace_file.read_text(encoding="utf-8"))
            report = next((item["stream_diagnostics"] for item in observations if "stream_diagnostics" in item), report)
        # Never print raw request/response bodies or credentials. Retain only
        # status and a bounded, key-redacted error tail on failure.
        try:
            answer, executed_tool = _completed_answer(client, result.stdout) if result.returncode == 0 else ("", False)
        except (ValueError, TypeError):
            answer, executed_tool = "", False
        executed_tool = executed_tool or observed_tool
        passed = (
            result.returncode == 0
            and expected in answer
            and (not tool_loop or executed_tool)
            and (not diagnostics or not report.get("failures"))
        )
        error = ""
        if not passed:
            if diagnostics:
                failures = report.get("failures", [])
                error = (
                    "client_timeout"
                    if result.returncode == 124
                    else failures[0]["category"]
                    if failures
                    else "client_or_verification_failed"
                )
            else:
                error = (result.stderr + result.stdout).replace(key, "[REDACTED]")[-2500:]
        output = {
            "client": client,
            "upstream": upstream.value,
            "model": model,
            "tool_loop": tool_loop,
            "passed": passed,
            "tool_verified": executed_tool if tool_loop else None,
            "requests": requests,
            "returncode": result.returncode,
            "error": error,
        }
        if diagnostics:
            output["diagnostics"] = report
        return output


def probe_resume(
    client: str,
    source: Protocol,
    target: Protocol,
    base_url: str,
    key: str,
    model: str,
    adapter_source: Path,
    *,
    timeout: int = 120,
    tool_loop: bool = False,
    target_base_url: str | None = None,
    target_key: str | None = None,
    target_model: str | None = None,
    source_reasoning: str = "openrouter_disabled",
    target_reasoning: str | None = None,
    target_public_reasoning: bool = False,
    verify_replay: bool = False,
) -> dict:
    """Two synthetic turns; optionally resume after a real read-only tool call."""
    executable = shutil.which(client)
    if not executable:
        raise ValueError(f"{client} is not on PATH")
    target_base_url = target_base_url or base_url
    target_key = target_key or key
    target_model = target_model or model
    target_reasoning = target_reasoning or source_reasoning
    if not base_url.endswith("/v1") or not target_base_url.endswith("/v1"):
        raise ValueError("This probe requires explicit /v1 API prefixes")

    def redact(value):
        return value.replace(key, "[REDACTED]").replace(target_key, "[REDACTED]")

    nonce = "RESUME_NONCE_" + secrets.token_hex(12)
    with tempfile.TemporaryDirectory(prefix="protoverta-live-resume-") as directory:
        root = Path(directory)
        env = {
            name: value
            for name, value in os.environ.items()
            if name.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"}
        }
        env.update(
            {
                "HOME": directory,
                "USERPROFILE": directory,
                "APPDATA": str(root / "roaming"),
                "LOCALAPPDATA": str(root / "local"),
                "CODEX_HOME": str(root / "codex"),
                "CLAUDE_CONFIG_DIR": str(root / "claude"),
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                "CLAUDE_CODE_SIMPLE": "1",
            }
        )
        for name in ("roaming", "local", "codex", "claude"):
            (root / name).mkdir()
        config = GatewayConfig(
            source,
            base_url,
            key,
            "isolated-probe",
            upstream_reasoning="provider_default" if source_reasoning == "auto" else source_reasoning,
        )
        fixture = root / "fixture.txt"
        if tool_loop and client == "claude":
            fixture.write_text(nonce + "\n", encoding="utf-8")
            prompt = f"Read only {fixture.as_posix()} with the Read tool. Remember the exact token it contains for my next message. Then reply ACK only. Do not write files, access the network, or use other tools."
            first = run_client(
                client,
                executable,
                "http://127.0.0.1:0",
                base_url.removesuffix("/v1"),
                config,
                env,
                directory,
                True,
                adapter_source,
                False,
                source,
                timeout,
                model,
                prompt=prompt,
                persist_session=True,
            )
            try:
                _, tool_verified = _completed_answer(client, first.stdout)
            except (ValueError, TypeError):
                tool_verified = False
        elif tool_loop:
            first, tool_verified, _ = _probe_codex_goal(
                source,
                base_url,
                key,
                model,
                adapter_source,
                env,
                directory,
                timeout,
                nonce=nonce,
                reasoning=source_reasoning,
            )
        else:
            first = run_client(
                client,
                executable,
                "http://127.0.0.1:0",
                base_url.removesuffix("/v1"),
                config,
                env,
                directory,
                False,
                adapter_source,
                False,
                source,
                timeout,
                model,
                prompt=f"Remember this synthetic token for my next message: {nonce}. Reply ACK only. Do not use tools.",
                persist_session=True,
            )
            tool_verified = False
        if first.returncode != 0 or (tool_loop and not tool_verified):
            return {
                "passed": False,
                "client": client,
                "stage": "initial",
                "error": redact(first.stderr + first.stdout)[-2000:],
            }
        identifier = session_id(client, first.stdout)
        config = GatewayConfig(
            target,
            target_base_url,
            target_key,
            "isolated-probe",
            upstream_reasoning="provider_default" if target_reasoning == "auto" else target_reasoning,
            public_reasoning=target_public_reasoning,
        )
        trace = root / "trace.json"
        prompt = "Return only the exact synthetic token from our previous message. Do not use tools."
        if verify_replay and target_public_reasoning:
            prompt += " Before answering, calculate 37 times 41 and check the token carefully, but still output only the original token."
        second = run_client(
            client,
            executable,
            "http://127.0.0.1:0",
            target_base_url.removesuffix("/v1"),
            config,
            env,
            directory,
            False,
            adapter_source,
            False,
            target,
            timeout,
            target_model,
            prompt=prompt,
            persist_session=True,
            resume_id=identifier,
            provider_id="alternate",
            trace_file=trace,
        )
        observations = json.loads(trace.read_text(encoding="utf-8")) if trace.is_file() else []
        public_reasoning_received = any(item.get("public_reasoning_received") for item in observations)
        stream_errors = sorted({item["stream_error"] for item in observations if "stream_error" in item})
        try:
            answer, _ = _completed_answer(client, second.stdout)
            same_session = session_id(client, second.stdout) == identifier
        except (ValueError, KeyError, StopIteration):
            answer, same_session = "", False
        passed = second.returncode == 0 and same_session and nonce in answer
        replayed_public_reasoning = None
        if passed and verify_replay:
            third = run_client(
                client,
                executable,
                "http://127.0.0.1:0",
                target_base_url.removesuffix("/v1"),
                config,
                env,
                directory,
                False,
                adapter_source,
                False,
                target,
                timeout,
                target_model,
                prompt="Repeat only the exact synthetic token from our earlier conversation. Do not use tools.",
                persist_session=True,
                resume_id=identifier,
                provider_id="alternate",
                trace_file=trace,
            )
            try:
                third_answer, _ = _completed_answer(client, third.stdout)
                passed = (
                    third.returncode == 0 and session_id(client, third.stdout) == identifier and nonce in third_answer
                )
            except (ValueError, KeyError, StopIteration):
                passed = False
            observations = json.loads(trace.read_text(encoding="utf-8")) if trace.is_file() else []
            replayed_public_reasoning = any(item.get("public_reasoning_items", 0) > 0 for item in observations)
            stream_errors += [item["stream_error"] for item in observations if "stream_error" in item]
            if not passed:
                second = third
        return {
            "passed": passed,
            "client": client,
            "source": source.value,
            "target": target.value,
            "model": model,
            "target_model": target_model,
            "provider_changed": base_url != target_base_url,
            "credentials_changed": key != target_key,
            "tool_loop": tool_loop,
            "tool_verified": tool_verified if tool_loop else None,
            "same_session": same_session,
            "nonce_recalled": nonce in answer,
            "public_reasoning_received": public_reasoning_received,
            "replayed_public_reasoning": replayed_public_reasoning,
            "stream_errors": stream_errors,
            "error": "" if passed else redact(second.stderr + second.stdout)[-2500:],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex", "claude"], required=True)
    parser.add_argument("--upstream", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--key-env", required=True)
    parser.add_argument("--base-url", default="https://openrouter.ai/api/v1")
    parser.add_argument("--adapter-source", type=Path, required=True)
    parser.add_argument(
        "--tool-loop",
        action="store_true",
        help="Verify a real tool round trip: Claude reads a nonce file; Codex queries get_goal",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Fresh probes: bounded stage/transport shapes and fixed failure categories, without client output",
    )
    parser.add_argument(
        "--max-upstream-requests",
        type=int,
        help="Diagnostics only: stop locally before another upstream request exceeds this budget",
    )
    parser.add_argument("--timeout", type=int, default=120, help="Maximum client runtime in seconds")
    parser.add_argument(
        "--resume-target",
        type=Protocol,
        choices=list(Protocol),
        help="Run two persistent turns, resuming against this protocol",
    )
    parser.add_argument("--target-base-url", help="Resume on a different real API prefix")
    parser.add_argument("--target-model", help="Resume using a different real model")
    parser.add_argument("--target-key-env", help="Environment variable for the resume provider's key")
    parser.add_argument(
        "--source-reasoning",
        choices=["auto", "provider_default", "disabled", "openrouter_disabled"],
        default="openrouter_disabled",
    )
    parser.add_argument("--target-reasoning", choices=["auto", "provider_default", "disabled", "openrouter_disabled"])
    parser.add_argument(
        "--target-public-reasoning",
        action="store_true",
        help="Opt in to unsigned Chat reasoning_content for the Codex resume target",
    )
    parser.add_argument(
        "--verify-replay",
        action="store_true",
        help="Resume a third turn on the target and observe whether public reasoning history is replayed",
    )
    parser.add_argument("--allow-live", action="store_true", help="Acknowledge real API calls and possible charges")
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("Live API calls require --allow-live")
    if args.timeout <= 0:
        parser.error("Timeout must be positive")
    if args.diagnostics and args.resume_target:
        parser.error("Diagnostics mode currently requires a fresh probe")
    if args.max_upstream_requests is not None and (not args.diagnostics or args.max_upstream_requests <= 0):
        parser.error("A positive request budget requires diagnostics mode")
    key = os.environ.get(args.key_env)
    if not key:
        parser.error("The configured key environment variable is empty")
    target_key = os.environ.get(args.target_key_env) if args.target_key_env else key
    if not target_key:
        parser.error("The target key environment variable is empty")
    if not args.resume_target and any(
        (
            args.target_base_url,
            args.target_model,
            args.target_key_env,
            args.target_reasoning,
            args.target_public_reasoning,
            args.verify_replay,
        )
    ):
        parser.error("Target options require --resume-target")
    if args.target_public_reasoning and (args.client != "codex" or args.resume_target != Protocol.CHAT):
        parser.error("Public reasoning is only portable for a Codex Chat resume target")
    try:
        result = (
            probe_resume(
                args.client,
                args.upstream,
                args.resume_target,
                args.base_url,
                key,
                args.model,
                args.adapter_source,
                timeout=args.timeout,
                tool_loop=args.tool_loop,
                target_base_url=args.target_base_url,
                target_key=target_key,
                target_model=args.target_model,
                source_reasoning=args.source_reasoning,
                target_reasoning=args.target_reasoning,
                target_public_reasoning=args.target_public_reasoning,
                verify_replay=args.verify_replay,
            )
            if args.resume_target
            else probe(
                args.client,
                args.upstream,
                args.base_url,
                key,
                args.model,
                args.adapter_source,
                timeout=args.timeout,
                tool_loop=args.tool_loop,
                reasoning=args.source_reasoning,
                diagnostics=args.diagnostics,
                max_upstream_requests=args.max_upstream_requests,
            )
        )
    except subprocess.TimeoutExpired:
        result = {"passed": False, "client": args.client, "error": "client_timeout"}
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
