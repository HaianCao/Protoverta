"""Opt-in vision sample using a generated red/blue PNG, no user image/files."""

from __future__ import annotations

import argparse
import base64
import json
import os
import struct
import zlib
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from protoverta import Protocol, StreamDecoder, convert_request, convert_response
from protoverta.server import GatewayConfig, gateway_session
from protoverta.sse import SSEParser


def synthetic_png() -> str:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = (b"\x00" + b"\xff\x00\x00" * 16 + b"\x00\x00\xff" * 16) * 32
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def probe(source: Protocol, upstream: Protocol, base_url: str, model: str, key: str, *, stream: bool, public_reasoning: bool = False) -> dict:
    template = {"model": model, "max_tokens": 1024, "stream": stream, "messages": [{"role": "user", "content": [{"type": "text", "text": "Name the dominant color on the LEFT, then on the RIGHT of this image. Reply with those two color names only."}, {"type": "image_url", "image_url": {"url": synthetic_png()}}]}]}
    body = convert_request(template, "chat", source)
    config = GatewayConfig(upstream, base_url, key, "synthetic-vision-probe", public_reasoning=public_reasoning, messages_usage_policy="deferred" if source == Protocol.MESSAGES else "strict")
    endpoint = {Protocol.CHAT: "/v1/chat/completions", Protocol.MESSAGES: "/v1/messages", Protocol.RESPONSES: "/v1/responses"}[source]
    with gateway_session(config) as origin:
        request = Request(origin + endpoint, json.dumps(body).encode(), {"Authorization": "Bearer synthetic-vision-probe", "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=120) as response:
                if stream:
                    decoder, parser = StreamDecoder(source, public_reasoning=public_reasoning), SSEParser()
                    parts = []
                    while data := response.read(4096):
                        for event in parser.feed(data):
                            parts.extend(delta.text for delta in decoder.feed(event) if delta.kind == "text")
                    parser.finish(strict=True)
                    decoder.finish()
                    answer = "".join(parts)
                else:
                    result = convert_response(json.load(response), source, "chat", public_reasoning=public_reasoning)
                    answer = result["choices"][0]["message"]["content"] or ""
        except HTTPError as exc:
            exc.close()
            return {"passed": False, "http_status": exc.code, "source": source.value, "upstream": upstream.value}
        except (URLError, OSError, ValueError):
            return {"passed": False, "error": "transport_or_conversion_error", "source": source.value, "upstream": upstream.value}
    normalized = answer.lower()
    passed = "red" in normalized and "blue" in normalized and normalized.index("red") < normalized.index("blue")
    return {"passed": passed, "source": source.value, "upstream": upstream.value, "model": model, "stream": stream, "synthetic_colors_recognized": passed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--upstream", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--key-env", required=True)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--public-reasoning", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("Live API calls require --allow-live")
    key = os.environ.get(args.key_env)
    if not key:
        parser.error("The configured key environment variable is empty")
    result = probe(args.source, args.upstream, args.base_url, args.model, key, stream=args.stream, public_reasoning=args.public_reasoning)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
