"""Opt-in bounded live Chat logprob probe; reports counts, never token data/keys.

One Chat request exercises Responses gateway output and reverse Chat conversion.
This is not a live probe of a provider's own Responses endpoint.
"""

import argparse
import io
import json
import os
import secrets
import threading
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from protoverta import ConversionError, StreamTranscoder, convert_response
from protoverta.server import (
    GatewayConfig,
    GatewayHTTPServer,
    _RedirectBlocker,
    gateway_session,
    make_handler,
)
from protoverta.sse import SSEParser
from protoverta.streaming import StreamError
from protoverta.transport import DeadlineHTTPHandler, DeadlineHTTPSHandler

LIMIT = 1024 * 1024
CODES = {"unsupported_reasoning", "unsupported_feature", "invalid_stream", "invalid_response", "upstream_timeout", "request_deadline", "progress_timeout"}


def shape(raw):
    """Fixed structural observations only; no generated text/token values."""
    try:
        body = json.loads(raw)
        choices = body.get("choices", []) if isinstance(body, dict) else []
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        message = choice.get("message")
        message = message if isinstance(message, dict) else {}
        probability = choice.get("logprobs")
        records = probability.get("content") if isinstance(probability, dict) else None
        result = {
            "has_text": bool(message.get("content")),
            "has_logprob_records": isinstance(records, list) and bool(records),
            "logprob_records": len(records) if isinstance(records, list) else 0,
            "has_reasoning": any(bool(message.get(k)) for k in ("reasoning", "reasoning_content", "reasoning_details")),
            "finish_reason": choice.get("finish_reason") if choice.get("finish_reason") in ("stop", "length", "tool_calls") else "other",
        }
        if isinstance(records, list):
            result["missing_byte_records"] = sum(not isinstance(r, dict) or r.get("bytes") is None for r in records)
        return result
    except (ValueError, TypeError, AttributeError, RecursionError):
        return {"invalid_json_shape": True}


@contextmanager
def session(config, upstream_open):
    if upstream_open is None:
        with gateway_session(config) as origin:
            yield origin
        return
    server = GatewayHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=upstream_open))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5)


def probe(base_url, model, key, *, streaming=False, upstream_open=None, upstream_reasoning="provider_default"):
    config = GatewayConfig(
        "chat",
        base_url,
        key,
        secrets.token_hex(32),
        timeout=15,
        total_timeout=30,
        progress_timeout=20,
        retry_attempts=1,
        max_body_bytes=LIMIT,
        max_stream_bytes=LIMIT,
        upstream_reasoning=upstream_reasoning,
    )
    body = {
        "model": model,
        "input": "Reply with exactly OK.",
        "max_output_tokens": 64,
        "include": ["message.output_text.logprobs"],
        "top_logprobs": 2,
        "stream": streaming,
    }
    result = {"model": model, "upstream": "chat", "streaming": streaming, "passed": False, "requests": 1, "outcome": "not_tested"}
    opener = build_opener(_RedirectBlocker(), DeadlineHTTPHandler(), DeadlineHTTPSHandler())
    transport = upstream_open or opener.open
    attempts = 0

    def counted(request, *, timeout):
        nonlocal attempts
        attempts += 1
        response = transport(request, timeout=timeout)
        if streaming:
            return response
        with response:
            raw = response.read(LIMIT + 1)
        result["upstream_shape"] = shape(raw)
        return io.BytesIO(raw)

    try:
        with session(config, counted) as origin:
            req = Request(
                origin + "/v1/responses", json.dumps(body).encode(), {"Authorization": "Bearer " + config.inbound_token, "Content-Type": "application/json"}
            )
            with build_opener(ProxyHandler({})).open(req, timeout=40) as response:
                result["http_status"] = response.status
                if not streaming:
                    raw = response.read(LIMIT + 1)
                    if len(raw) > LIMIT:
                        raise ValueError("response limit")
                    decoded = json.loads(raw)
                    chat = convert_response(decoded, "responses", "chat", text_logprobs=True, max_top_logprobs=2)
                    count = len((chat["choices"][0].get("logprobs") or {}).get("content") or [])
                    incomplete = decoded["status"] != "completed"
                else:
                    pipe = StreamTranscoder("responses", "chat", text_logprobs=True, max_top_logprobs=2, max_stream_bytes=LIMIT)
                    parser, count, incomplete = SSEParser(), 0, False

                    def consume(raw):
                        nonlocal count, incomplete
                        for event in parser.feed(raw):
                            if event.data == "[DONE]":
                                continue
                            chunk = json.loads(event.data)
                            for choice in chunk.get("choices", []):
                                count += len((choice.get("logprobs") or {}).get("content") or [])
                                incomplete |= choice.get("finish_reason") == "length"

                    while raw := response.read(4096):
                        consume(pipe.feed(raw))
                    consume(pipe.finish())
                    parser.finish(strict=True)
                result.update(
                    token_records=count,
                    passed=count > 0 and not incomplete,
                    outcome="incomplete" if incomplete else "passed" if count else "missing_measurements",
                )
    except HTTPError as exc:
        result.update(http_status=exc.code, outcome="http_failure")
        try:
            payload = json.loads(exc.read(LIMIT))
            error = payload.get("error", {})
            code = error.get("code") if isinstance(error, dict) else None
            if code in CODES:
                result["code"] = code
            phase = error.get("phase") if isinstance(error, dict) else None
            if phase in {"request", "preflight", "admission", "upstream", "response", "stream"}:
                result["phase"] = phase
        except (ValueError, TypeError, AttributeError):
            pass
        finally:
            exc.close()
    except (OSError, ValueError, TypeError, KeyError, RecursionError, ConversionError, StreamError):
        result["outcome"] = "transport_or_contract_failure"
    result["upstream_requests"] = attempts
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--key-env", required=True)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--upstream-reasoning", choices=("provider_default", "openrouter_disabled"), default="provider_default")
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("Live calls require --allow-live")
    key = os.environ.get(args.key_env)
    if not key:
        parser.error("The selected key environment variable is empty")
    result = probe(args.base_url, args.model, key, streaming=args.stream, upstream_reasoning=args.upstream_reasoning)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
