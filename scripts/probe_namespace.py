"""Opt-in real-provider namespace round trip through the local Responses gateway.

Only synthetic arithmetic is sent. Never prints credentials or raw responses.
"""

from __future__ import annotations

import argparse
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from protoverta.core import Protocol
from protoverta.server import GatewayConfig, gateway_session


def probe(protocol: Protocol, base_url: str, key: str, model: str) -> dict:
    config = GatewayConfig(protocol, base_url, key, "isolated-probe", namespace_tools="flatten", upstream_reasoning="openrouter_disabled", default_max_output_tokens=512)
    tool = {"type": "namespace", "name": "calculator", "tools": [{"type": "function", "name": "add", "description": "Add two integer arguments", "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}}]}
    first_request = {"model": model, "input": [{"role": "user", "content": "Use calculator.add exactly once to add 2 and 3, then report the result."}], "tools": [tool], "tool_choice": "required", "max_output_tokens": 512}

    def post(origin: str, payload: dict) -> dict:
        request = Request(origin + "/v1/responses", json.dumps(payload).encode(), {"Authorization": "Bearer isolated-probe", "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=120) as response:
                return json.load(response)
        except HTTPError as exc:
            # Preserve a bounded diagnostic, never print a credential or body.
            try:
                message = str(json.load(exc).get("error", {}).get("message", ""))
            except (ValueError, TypeError, AttributeError):
                message = ""
            raise RuntimeError(f"HTTP {exc.code}: {message[:300]}") from exc

    with gateway_session(config) as origin:
        first = post(origin, first_request)
        calls = [item for item in first.get("output", []) if item.get("type") == "function_call"]
        matched = [item for item in calls if item.get("namespace") == "calculator" and item.get("name") == "add"]
        if len(matched) != 1:
            return {"passed": False, "upstream": protocol.value, "stage": "tool_call", "calls": len(calls), "namespace_restored": False}
        call = matched[0]
        arguments = json.loads(call["arguments"])
        if arguments != {"a": 2, "b": 3}:
            return {"passed": False, "upstream": protocol.value, "stage": "arguments", "namespace_restored": True, "arguments_match": False}
        followup = {"model": model, "input": [*first_request["input"], *first["output"], {"type": "function_call_output", "call_id": call["call_id"], "output": "5"}], "tools": [tool], "max_output_tokens": 512}
        second = post(origin, followup)
        output = " ".join(part.get("text", "") for item in second.get("output", []) if item.get("type") == "message" for part in item.get("content", []) if part.get("type") == "output_text")
        passed = "5" in output
        return {"passed": passed, "upstream": protocol.value, "namespace_restored": True, "arguments_match": True, "result_replayed": passed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Protocol, choices=[Protocol.CHAT, Protocol.MESSAGES], required=True)
    parser.add_argument("--base-url", default="https://openrouter.ai/api/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--key-env", required=True)
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("Live API calls require --allow-live")
    key = os.environ.get(args.key_env)
    if not key:
        parser.error("The configured key environment variable is empty")
    try:
        result = probe(args.upstream, args.base_url, key, args.model)
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:
        result = {"passed": False, "upstream": args.upstream.value, "error": str(exc).replace(key, "[REDACTED]")}
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
