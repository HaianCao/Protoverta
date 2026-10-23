"""Opt-in synthetic JSON Schema probe through one translated gateway route.

A passing sample verifies request transport and this sample's output, not the
provider's universal schema enforcement. No credentials or raw bodies are printed.
"""

from __future__ import annotations

import argparse
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from protoverta import Protocol, StreamDecoder, convert_request, convert_response
from protoverta.server import GatewayConfig, gateway_session
from protoverta.sse import SSEParser


def probe(source: Protocol, upstream: Protocol, model: str, base_url: str, key: str, *, stream: bool = False) -> dict:
    if source == upstream:
        raise ValueError("Choose different source and upstream protocols")
    schema = {"type": "object", "properties": {"value": {"type": "integer", "enum": [5]}}, "required": ["value"], "additionalProperties": False}
    template = {"model": model, "messages": [{"role": "user", "content": "Return only a JSON object with value equal to the integer 5."}], "max_completion_tokens": 256, "stream": stream, "response_format": {"type": "json_schema", "json_schema": {"name": "probe_result", "strict": True, "schema": schema}}}
    body = convert_request(template, "chat", source)
    config = GatewayConfig(upstream, base_url, key, "isolated-probe", upstream_reasoning="openrouter_disabled", messages_usage_policy="deferred" if source == Protocol.MESSAGES else "strict")
    endpoint = {Protocol.CHAT: "/v1/chat/completions", Protocol.MESSAGES: "/v1/messages", Protocol.RESPONSES: "/v1/responses"}[source]
    with gateway_session(config) as origin:
        request = Request(origin + endpoint, json.dumps(body).encode(), {"Authorization": "Bearer isolated-probe", "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=120) as response:
                if stream:
                    decoder = StreamDecoder(source)
                    parser = SSEParser()
                    parts = []
                    while chunk := response.read(4096):
                        for event in parser.feed(chunk):
                            parts.extend(delta.text for delta in decoder.feed(event) if delta.kind == "text")
                    parser.finish(strict=True)
                    decoder.finish()
                    text = "".join(parts)
                else:
                    result = convert_response(json.load(response), source, "chat")
                    text = result["choices"][0]["message"]["content"]
        except HTTPError as exc:
            try:
                message = str(json.load(exc).get("error", {}).get("message", ""))
            except (ValueError, TypeError, AttributeError):
                message = ""
            raise ValueError(f"HTTP {exc.code}: {message[:300]}") from exc
    matched = json.loads(text) == {"value": 5}
    return {"passed": matched, "source": source.value, "upstream": upstream.value, "stream": stream, "sample_matches_schema": matched}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--upstream", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--key-env", required=True)
    parser.add_argument("--base-url", default="https://openrouter.ai/api/v1")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("Live API calls require --allow-live")
    key = os.environ.get(args.key_env)
    if not key:
        parser.error("The configured key environment variable is empty")
    try:
        result = probe(args.source, args.upstream, args.model, args.base_url, key, stream=args.stream)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        result = {"passed": False, "source": args.source.value, "upstream": args.upstream.value, "error": str(exc).replace(key, "[REDACTED]")}
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
