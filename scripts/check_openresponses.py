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
from collections import Counter
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
            # Match the explicit synthetic measurement in the JSON fixture.
            # Messages cannot carry a reasoning subtotal; its receiver still
            # reports that field as unknown rather than manufacturing zero.
            deltas = [Delta("start", model="fixture-model", input_tokens=19, output_tokens=0), Delta("text", index=0, text="Hello synthetic world"), Delta("finish", reason="stop", input_tokens=19, output_tokens=7, cached_input_tokens=0, cache_creation_input_tokens=0, reasoning_output_tokens=usage["completion_tokens_details"]["reasoning_tokens"])]
            return io.BytesIO(b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta)))
        result = {"model": "fixture-model", "choices": [{"message": message, "finish_reason": reason}], "usage": usage}
        return io.BytesIO(json.dumps(convert_response(result, "chat", protocol)).encode())
    return upstream


def classify_missing_field(field):
    root = field.split(".", 1)[0]
    if root in {"id", "object", "created_at", "completed_at", "status", "incomplete_details", "store", "background", "previous_response_id", "max_tool_calls", "safety_identifier", "prompt_cache_key", "truncation"}:
        return "gateway-owned"
    if root in {"instructions", "max_output_tokens", "tools", "tool_choice", "parallel_tool_calls", "text", "temperature", "top_p"}:
        return "request-declared"
    if root in {"usage", "service_tier"}:
        return "upstream-measured"
    if root in {"presence_penalty", "frequency_penalty", "top_logprobs"}:
        return "unavailable"
    return "unavailable"


def summarize(reports):
    counts = Counter(item["status"] for report in reports for item in report["results"])
    total = sum(counts.values())
    passed = len(reports) == 2 and {report["upstream"] for report in reports} == {"chat", "messages"} and total == 2 * len(CASES) and counts["passed"] == total and all(report["exit_code"] == 0 for report in reports)
    fields = Counter(error.split(":", 1)[0] for report in reports for item in report["results"] for error in item["errors"])
    groups = {}
    provenance_classes = {}
    for field, count in sorted(fields.items()):
        root = field.split(".", 1)[0]
        if root in {"tools", "tool_choice", "parallel_tool_calls", "text", "temperature", "top_p"}:
            group = "request_metadata_or_nullable_tool_fields"
        elif root in {"usage", "service_tier"}:
            group = "upstream_measurements_or_provider_facts"
        elif root in {"presence_penalty", "frequency_penalty", "top_logprobs"}:
            group = "controls_outside_portable_subset"
        else:
            group = "envelope_or_unclassified"
        groups.setdefault(group, {})[field] = count
        provenance_classes.setdefault(classify_missing_field(field), {})[field] = count
    return {"outcome": "PASSED_FULL_SCHEMA" if passed else "FAILED_FULL_SCHEMA", "passed": counts["passed"], "failed": counts["failed"],
            "skipped": counts["skipped"], "total": total, "field_groups": groups, "provenance_classes": provenance_classes}


def validate_cases(output):
    results = output["results"]
    if len(results) != len(CASES) or {item["id"] for item in results} != set(CASES) or output["summary"].get("skipped"):
        raise ValueError("official suite returned missing, duplicate or skipped checks")
    if any(item["status"] not in {"passed", "failed"} for item in results):
        raise ValueError("official suite returned an unknown or skipped status")


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
            validate_cases(output)
            reports.append({"upstream": protocol, "exit_code": result.returncode, "summary": output["summary"], "results": [{"id": item["id"], "status": item["status"], "errors": item.get("errors", [])} for item in output["results"]]})
        finally:
            server.shutdown()
            server.server_close()
            worker.join(5)
    return {"official_revision": PIN, "runner_sha256": hashlib.sha256(runner.read_bytes()).hexdigest(), "cases": CASES, "local_fixtures_only": True, "provider_certification": False, "summary": summarize(reports), "reports": reports}


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
    return 0 if report["summary"]["outcome"] == "PASSED_FULL_SCHEMA" else 1


if __name__ == "__main__":
    raise SystemExit(main())
