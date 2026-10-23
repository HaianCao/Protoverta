"""Actual CLI resume/history checks against loopback fixtures; no API keys.

All client state is temporary. No existing session files are read or rewritten.
This verifies stateless text replay, not encrypted reasoning or real models.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from smoke_clients import fixture_server, run_client

from protoverta.core import Protocol
from protoverta.server import GatewayConfig
from protoverta.sse import encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta


def session_id(client: str, stdout: str) -> str:
    if client == "claude":
        return json.loads(stdout)["session_id"]
    return next(event["thread_id"] for line in stdout.splitlines() if (event := json.loads(line)).get("type") == "thread.started")


def tool_deltas(client: str, fixture_path: str) -> list[Delta]:
    arguments = "{}" if client == "codex" else json.dumps({"file_path": fixture_path})
    return [Delta("tool_start", index=0, call_id="resume_call", name="get_goal" if client == "codex" else "Read"), Delta("tool_delta", index=0, text=arguments), Delta("tool_end", index=0), Delta("finish", reason="tool_calls", input_tokens=1, output_tokens=1)]


def run_resume(client: str, source: Protocol, target: Protocol, adapter_source: Path, *, change_provider: bool = False, change_model: bool = False, timeout: int = 60, trace: bool = False, tool_loop: bool = False) -> dict:
    executable = shutil.which(client)
    if not executable:
        raise ValueError(f"{client} is not on PATH")
    previous = "FIRST_ANSWER_" + secrets.token_hex(12)
    final = "RESUME_OK_" + secrets.token_hex(12)
    requests = []
    initial_finished = False
    fixture_path = ""

    def tool_history(body, protocol):
        if protocol == Protocol.RESPONSES:
            calls = {item["call_id"]: item["name"] for item in body["input"] if item.get("type") == "function_call"}
            results = {item["call_id"] for item in body["input"] if item.get("type") == "function_call_output"}
        elif protocol == Protocol.CHAT:
            calls = {call["id"]: call["function"]["name"] for item in body["messages"] for call in item.get("tool_calls", [])}
            results = {item["tool_call_id"] for item in body["messages"] if item.get("role") == "tool"}
        else:
            blocks = [block for item in body["messages"] for block in item["content"] if isinstance(block, dict)]
            calls = {block["id"]: block["name"] for block in blocks if block.get("type") == "tool_use"}
            results = {block["tool_use_id"] for block in blocks if block.get("type") == "tool_result"}
        return "resume_call" in results and calls.get("resume_call") == ("get_goal" if client == "codex" else "Read")

    def open_upstream(request, *, timeout):
        nonlocal initial_finished
        body = json.loads(request.data)
        endpoint = urlsplit(request.full_url).path
        protocol = Protocol.CHAT if endpoint.endswith("/chat/completions") else Protocol(endpoint.rsplit("/", 1)[-1])
        serialized = json.dumps(body)
        history_present = previous in serialized
        tool_present = tool_history(body, protocol) if tool_loop else False
        requests.append({"protocol": protocol.value, "model": body["model"], "previous_answer_present": history_present, "tool_history_present": tool_present})
        encoder = StreamEncoder(protocol)
        deltas = [Delta("start", model=body["model"], input_tokens=1, output_tokens=0)]
        if tool_loop and len(requests) == 1:
            deltas.extend(tool_deltas(client, fixture_path))
        else:
            answer = previous if not initial_finished else final if history_present and (not tool_loop or tool_present) else "HISTORY_MISSING"
            initial_finished = True
            deltas.extend([Delta("text", text=answer), Delta("finish", reason="stop", input_tokens=1, output_tokens=1)])
        return io.BytesIO(b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta)))

    with tempfile.TemporaryDirectory(prefix="protoverta-resume-") as directory:
        root = Path(directory)
        env = {name: value for name, value in os.environ.items() if name.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"}}
        env.update({"HOME": directory, "USERPROFILE": directory, "APPDATA": str(root / "roaming"), "LOCALAPPDATA": str(root / "local"), "CODEX_HOME": str(root / "codex"), "CLAUDE_CONFIG_DIR": str(root / "claude"), "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_SIMPLE": "1", "PROTOVERTA_SMOKE_TOKEN": "smoke-local-token"})
        for name in ("roaming", "local", "codex", "claude"):
            (root / name).mkdir()
        fixture_path = str(root / "fixture.txt")
        Path(fixture_path).write_text("Synthetic resume tool fixture\n", encoding="utf-8")
        with fixture_server(open_upstream) as origin:
            config = GatewayConfig(source, origin + "/v1", "fake-upstream-key", "smoke-local-token")
            first = run_client(client, executable, "http://127.0.0.1:0", origin, config, env, directory, tool_loop, adapter_source, False, source, timeout, "protoverta-first", prompt="Reply briefly. Do not use tools.", persist_session=True)
            if first.returncode != 0 or previous not in first.stdout or (tool_loop and not any(request["tool_history_present"] for request in requests)):
                return {"passed": False, "stage": "initial", "client": client, "error": (first.stderr + first.stdout)[-2000:]}
            identifier = session_id(client, first.stdout)
            config = GatewayConfig(target, origin + "/v1", "fake-upstream-key", "smoke-local-token")
            trace_file = root / "request-shapes.json" if trace else None
            second = run_client(client, executable, "http://127.0.0.1:0", origin, config, env, directory, tool_loop, adapter_source, False, target, timeout, "protoverta-second" if change_model else "protoverta-first", prompt="Continue our previous conversation. Do not use tools.", persist_session=True, resume_id=identifier, provider_id="alternate" if change_provider else "smoke", trace_file=trace_file)
            try:
                same_session = session_id(client, second.stdout) == identifier
            except (ValueError, KeyError, StopIteration):
                same_session = False
            last = requests[-1]
            passed = second.returncode == 0 and same_session and final in second.stdout and len(requests) == (3 if tool_loop else 2) and last["previous_answer_present"] and (not tool_loop or last["tool_history_present"]) and last["protocol"] == target.value and last["model"] == ("protoverta-second" if change_model else "protoverta-first")
            error = "" if passed else (second.stderr + second.stdout)[-2500:]
            unsupported = "Codex gateway cannot relocate mid-conversation instructions" in error or "Codex gateway cannot translate namespaced tools" in error
            observations = json.loads(trace_file.read_text(encoding="utf-8")) if trace_file and trace_file.exists() else []
            return {"passed": passed, "classification": "supported" if passed else "unsupported_history" if unsupported else "failed", "client": client, "source": source.value, "target": target.value, "change_provider": change_provider, "change_model": change_model, "tool_loop": tool_loop, "same_session": same_session, "requests": requests, "request_shapes": observations[:1], "error": error}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex", "claude"], required=True)
    parser.add_argument("--source", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--target", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--adapter-source", type=Path, required=True)
    parser.add_argument("--change-provider", action="store_true")
    parser.add_argument("--change-model", action="store_true")
    parser.add_argument("--trace", action="store_true", help="Capture converted Codex request shapes only, without content or credentials")
    parser.add_argument("--tool-loop", action="store_true", help="Resume after a completed read-only tool call")
    args = parser.parse_args()
    result = run_resume(args.client, args.source, args.target, args.adapter_source, change_provider=args.change_provider, change_model=args.change_model, trace=args.trace, tool_loop=args.tool_loop)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
