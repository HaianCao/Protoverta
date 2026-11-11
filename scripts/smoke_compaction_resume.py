"""Exercise real CLI auto-compaction against loopback SSE fixtures; no API keys.

All client state is temporary. No existing session files are read or rewritten.
This verifies that when a multi-turn tool loop crosses the harness's compaction
threshold, the harness itself generates its text summarization request and
replays the compacted summary turn on the next request without the gateway
summarizing, rewriting, or truncating history on its own.
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
from protoverta.server import GatewayConfig, gateway_session
from protoverta.sse import encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta

CODEX_COMPACT_PROMPT_MARKER = "CONTEXT CHECKPOINT COMPACTION"
CODEX_COMPACT_HANDOFF_MARKER = "Another language model started to solve this problem and produced a summary"
CLAUDE_COMPACT_PROMPT_MARKER = "CRITICAL: Respond with TEXT ONLY. Do NOT call any tools."
CLAUDE_COMPACT_HANDOFF_MARKER = (
    "Continue the conversation from where it left off without asking the user any further questions."
)


def classify_compaction_request(client: str, serialized_body: str) -> dict[str, bool]:
    if client == "codex":
        return {
            "is_compact_prompt": CODEX_COMPACT_PROMPT_MARKER in serialized_body,
            "has_compacted_summary": CODEX_COMPACT_HANDOFF_MARKER in serialized_body,
        }
    return {
        "is_compact_prompt": CLAUDE_COMPACT_PROMPT_MARKER in serialized_body,
        "has_compacted_summary": CLAUDE_COMPACT_HANDOFF_MARKER in serialized_body,
    }


def run_compaction_smoke(
    client: str,
    upstream: Protocol,
    adapter_source: Path,
    *,
    timeout: int = 60,
) -> dict:
    executable = shutil.which(client)
    if not executable:
        raise ValueError(f"{client} is not on PATH")
    summary_nonce = "COMPACT_SUMMARY_NONCE_" + secrets.token_hex(8)
    final_marker = "COMPACT_FINAL_OK_" + secrets.token_hex(8)
    requests: list[dict] = []
    file_a_path = ""
    file_b_path = ""

    def open_upstream(request, *, timeout):
        body = json.loads(request.data)
        endpoint = urlsplit(request.full_url).path
        protocol = Protocol.CHAT if endpoint.endswith("/chat/completions") else Protocol(endpoint.rsplit("/", 1)[-1])
        serialized = json.dumps(body)
        flags = classify_compaction_request(client, serialized)
        summary_replayed = summary_nonce in serialized
        turn_index = len(requests) + 1
        requests.append(
            {
                "turn": turn_index,
                "protocol": protocol.value,
                "model": body.get("model"),
                "is_compact_prompt": flags["is_compact_prompt"],
                "has_compacted_summary": flags["has_compacted_summary"],
                "summary_nonce_replayed": summary_replayed,
            }
        )
        encoder = StreamEncoder(protocol)
        if client == "codex":
            if turn_index == 1:
                deltas = [
                    Delta("start", model=body["model"], input_tokens=5000, output_tokens=0),
                    Delta("tool_start", index=0, call_id="compact_call_1", name="get_goal"),
                    Delta("tool_delta", index=0, text="{}"),
                    Delta("tool_end", index=0),
                    Delta("finish", reason="tool_calls", input_tokens=5000, output_tokens=50),
                ]
            elif flags["is_compact_prompt"]:
                deltas = [
                    Delta("start", model=body["model"], input_tokens=500, output_tokens=0),
                    Delta(
                        "text",
                        text=f"Summary: checked goal tool ({summary_nonce}). Proceed to final answer.",
                    ),
                    Delta("finish", reason="stop", input_tokens=500, output_tokens=20),
                ]
            else:
                answer = (
                    final_marker if flags["has_compacted_summary"] and summary_replayed else "COMPACT_SUMMARY_MISSING"
                )
                deltas = [
                    Delta("start", model=body["model"], input_tokens=200, output_tokens=0),
                    Delta("text", text=answer),
                    Delta("finish", reason="stop", input_tokens=200, output_tokens=10),
                ]
        else:
            if turn_index == 1:
                deltas = [
                    Delta("start", model=body["model"], input_tokens=600, output_tokens=1),
                    Delta("tool_start", index=0, call_id="call_read_a", name="Read"),
                    Delta(
                        "tool_delta",
                        index=0,
                        text=json.dumps({"file_path": file_a_path}),
                    ),
                    Delta("tool_end", index=0),
                    Delta("finish", reason="tool_calls", input_tokens=600, output_tokens=20),
                ]
            elif turn_index == 2:
                deltas = [
                    Delta("start", model=body["model"], input_tokens=20000, output_tokens=1),
                    Delta("tool_start", index=0, call_id="call_read_b", name="Read"),
                    Delta(
                        "tool_delta",
                        index=0,
                        text=json.dumps({"file_path": file_b_path}),
                    ),
                    Delta("tool_end", index=0),
                    Delta("finish", reason="tool_calls", input_tokens=20000, output_tokens=20),
                ]
            elif flags["is_compact_prompt"]:
                deltas = [
                    Delta("start", model=body["model"], input_tokens=500, output_tokens=1),
                    Delta(
                        "text",
                        text=(
                            f"<analysis>Read two fixture files.</analysis>\n<summary>Primary Request: Read fixtures ({summary_nonce}) and finish.</summary>"
                        ),
                    ),
                    Delta("finish", reason="stop", input_tokens=500, output_tokens=30),
                ]
            else:
                answer = (
                    final_marker if flags["has_compacted_summary"] and summary_replayed else "COMPACT_SUMMARY_MISSING"
                )
                deltas = [
                    Delta("start", model=body["model"], input_tokens=200, output_tokens=1),
                    Delta("text", text=answer),
                    Delta("finish", reason="stop", input_tokens=200, output_tokens=10),
                ]
        return io.BytesIO(
            b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta))
        )

    with tempfile.TemporaryDirectory(prefix="protoverta-compact-") as directory:
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
                "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "1",
                "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "100000",
                "PROTOVERTA_SMOKE_TOKEN": "smoke-local-token",
            }
        )
        for name in ("roaming", "local", "codex", "claude"):
            (root / name).mkdir()
        file_a_path = str(root / "a.txt")
        file_b_path = str(root / "b.txt")
        # Write ~3.6 KB per file (~900 tokens each by bytesPerToken=4) so that
        # on translated routes (where message_start reports deferred 0
        # input_tokens and Claude falls back to byte estimation) the total
        # exceeds the 800-token threshold (1% of 100k - 20k) after 2 tool groups.
        Path(file_a_path).write_text("FILE_A_CONTENT_" * 240 + "\n", encoding="utf-8")
        Path(file_b_path).write_text("FILE_B_CONTENT_" * 240 + "\n", encoding="utf-8")
        codex_config = (
            {
                "model_auto_compact_token_limit": 1000,
                "model_context_window": 8000,
                "model_reasoning_summary": "none",
            }
            if client == "codex"
            else None
        )
        with fixture_server(open_upstream) as upstream_origin:
            config = GatewayConfig(
                upstream,
                upstream_origin + "/v1",
                "fake-upstream-key",
                "smoke-local-token",
                client_profile="codex" if client == "codex" else "claude",
                default_max_output_tokens=4096,
                messages_usage_policy="deferred" if client == "claude" and upstream != Protocol.MESSAGES else "strict",
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
                    True,
                    adapter_source,
                    False,
                    upstream,
                    timeout,
                    "claude-sonnet-4-6" if client == "claude" else "protoverta-compact",
                    prompt="Check the goal or read the fixture files, then reply briefly.",
                    persist_session=True,
                    codex_config=codex_config,
                )
        expected_turns = 3 if client == "codex" else 4
        compact_turn = next((item for item in requests if item["is_compact_prompt"]), None)
        final_turn = requests[-1] if requests else None
        passed = (
            result.returncode == 0
            and final_marker in result.stdout
            and len(requests) == expected_turns
            and compact_turn is not None
            and final_turn is not None
            and final_turn["has_compacted_summary"]
            and final_turn["summary_nonce_replayed"]
            and all(item["protocol"] == upstream.value for item in requests)
        )
        return {
            "passed": passed,
            "client": client,
            "upstream": upstream.value,
            "returncode": result.returncode,
            "expected_turns": expected_turns,
            "observed_turns": len(requests),
            "requests": requests,
            "error": "" if passed else (result.stderr + result.stdout)[-2500:],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex", "claude"], required=True)
    parser.add_argument("--upstream", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--adapter-source", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = run_compaction_smoke(
        args.client,
        args.upstream,
        args.adapter_source.resolve(),
        timeout=args.timeout,
    )
    output = json.dumps(report, indent=2)
    if args.report:
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
