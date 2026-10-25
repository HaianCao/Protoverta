"""Run an unmodified pinned official compliance runner against loopback fixtures.

Schema findings are failures, never rewritten into passes or provider evidence.
Download/build the official runner separately; no installer/network bootstrap here.
"""

import argparse
import hashlib
import io
import json
import subprocess
import sys
import threading
from pathlib import Path

from test_environment import sanitized_environment

PIN = "8907bc3b7c4300ff5c21fd647cfde9929243afa1"
CASES = ("basic-response", "streaming-response", "system-prompt", "tool-calling", "image-input", "multi-turn")


def fixture(protocol):
    from protoverta.core import convert_request, convert_response
    from protoverta.sse import encode_sse
    from protoverta.stream_encoder import StreamEncoder
    from protoverta.streaming import Delta

    def upstream(request, timeout):
        body = convert_request(json.loads(request.data), protocol, "chat")
        tool = body.get("tools", [{}])[0].get("function")
        usage = {"prompt_tokens": 19, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}, "completion_tokens_details": {"reasoning_tokens": 0}}
        message = {"role": "assistant", "content": "Hello synthetic world"}
        reason = "stop"
        if tool:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_fixture", "type": "function", "function": {"name": tool["name"], "arguments": '{"location":"San Francisco"}'}}]}
            reason = "tool_calls"
        if body.get("stream"):
            encoder = StreamEncoder(protocol)
            deltas = [Delta("start", model="fixture-model", input_tokens=19, output_tokens=0), Delta("text", index=0, text="Hello synthetic world"), Delta("finish", reason="stop", input_tokens=19, output_tokens=7, cached_input_tokens=0, cache_creation_input_tokens=0)]
            return io.BytesIO(b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta)))
        result = {"model": "fixture-model", "choices": [{"message": message, "finish_reason": reason}], "usage": usage}
        return io.BytesIO(json.dumps(convert_response(result, "chat", protocol)).encode())
    return upstream


def run(suite, runner, *, installed=False):
    env = sanitized_environment()
    revision = subprocess.check_output(["git", "-C", str(suite), "rev-parse", "HEAD"], env=env, text=True).strip()
    if revision != PIN:
        raise ValueError("official compliance checkout does not match the reviewed commit")
    # Verify the test/validator checkout is not locally patched before execution.
    if subprocess.check_output(["git", "-C", str(suite), "status", "--porcelain", "--untracked-files=no"], env=env, text=True).strip():
        raise ValueError("official suite has tracked modifications")
    if not installed:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from protoverta.server import GatewayConfig, GatewayHTTPServer, make_handler

    reports = []
    for protocol in ("chat", "messages"):
        config = GatewayConfig(protocol, "https://example.invalid/v1", "synthetic-upstream", "synthetic-local", client_profile="codex", default_max_output_tokens=128)
        server = GatewayHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=fixture(protocol)))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            result = subprocess.run(["node", str(runner), "--base-url", f"http://127.0.0.1:{server.server_port}/v1", "--api-key", "synthetic-local", "--model", "fixture-model", "--filter", ",".join(CASES), "--json"], env=env, capture_output=True, text=True, timeout=90, check=False)
            output = json.loads(result.stdout)
            if {item["id"] for item in output["results"]} != set(CASES) or output["summary"].get("skipped"):
                raise ValueError("official suite returned missing or skipped checks")
            reports.append({"upstream": protocol, "exit_code": result.returncode, "summary": output["summary"], "results": [{"id": item["id"], "status": item["status"], "errors": item.get("errors", [])} for item in output["results"]]})
        finally:
            server.shutdown()
            server.server_close()
            worker.join(5)
    return {"official_revision": PIN, "runner_sha256": hashlib.sha256(runner.read_bytes()).hexdigest(), "cases": CASES, "local_fixtures_only": True, "provider_certification": False, "reports": reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True, help="Bundled unmodified official CLI, generated from the reviewed checkout")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--installed", action="store_true")
    args = parser.parse_args()
    report = run(args.suite.resolve(), args.runner.resolve(), installed=args.installed)
    raw = json.dumps(report, indent=2)
    if args.report:
        args.report.write_text(raw + "\n", encoding="utf-8")
    print(raw)
    return 0 if all(item["exit_code"] == 0 for item in report["reports"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
