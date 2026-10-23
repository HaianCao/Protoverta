"""Opt-in Chat model baseline: report shapes only, never response content/keys."""

from __future__ import annotations

import argparse
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from protoverta.server import GatewayConfig, _RedirectBlocker


def probe(base_url: str, model: str, key: str, *, timeout: int = 90) -> dict:
    config = GatewayConfig("chat", base_url, key, "isolated-probe")
    body = {"model": model, "messages": [{"role": "user", "content": "Reply with exactly PROTOVERTA_OK. Do not use tools."}], "max_tokens": 256, "stream": False}
    request = Request(config.upstream_url, json.dumps(body).encode(), {"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    try:
        with build_opener(_RedirectBlocker()).open(request, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                return {"model": model, "passed": False, "error": "response_too_large"}
            result = json.loads(raw)
    except HTTPError as exc:
        exc.close()
        return {"model": model, "passed": False, "http_status": exc.code}
    except (URLError, TimeoutError, ValueError, OSError):
        return {"model": model, "passed": False, "error": "transport_or_json_error"}
    choices = result.get("choices", []) if isinstance(result, dict) else []
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message", {})
    if not isinstance(message, dict):
        return {"model": model, "passed": False, "error": "invalid_message"}
    return {"model": model, "http_status": 200, "passed": "PROTOVERTA_OK" in str(message.get("content", "")), "finish_reason": choice.get("finish_reason"), "reasoning_fields": [name for name in ("reasoning", "reasoning_content", "reasoning_details") if message.get(name)], "has_text": bool(message.get("content"))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", action="append", required=True)
    parser.add_argument("--key-env", required=True)
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("Live API calls require --allow-live")
    key = os.environ.get(args.key_env)
    if not key:
        parser.error("The configured key environment variable is empty")
    results = [probe(args.base_url, model, key) for model in args.model]
    print(json.dumps(results, indent=2))
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
