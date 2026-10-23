"""Synthetic harness instrumentation; records shapes, never headers or bodies."""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

from protoverta import server
from protoverta.streaming import StreamDecoder, StreamError


def _array(value):
    return value if isinstance(value, list) else []


def summarize(body: dict) -> dict:
    messages = []
    inputs = _array(body.get("input"))
    for index, item in enumerate(inputs):
        if not isinstance(item, dict) or item.get("role") != "developer":
            continue
        content = item.get("content", "")
        text = content if isinstance(content, str) else "".join(block["text"] for block in _array(content) if isinstance(block, dict) and isinstance(block.get("text"), str))
        messages.append({"index": index, "tags": sorted(set(re.findall(r"</?([a-zA-Z_][a-zA-Z0-9_]*)", text)))})
    public_reasoning_items = sum(isinstance(item, dict) and item.get("type") == "reasoning" and any(isinstance(part, dict) and part.get("type") == "reasoning_text" and isinstance(part.get("text"), str) for part in _array(item.get("content"))) for item in inputs)
    reasoning_shapes = [{"known_fields": sorted(set(item) & {"type", "id", "summary", "content", "status", "encrypted_content"}), "unknown_fields": bool(set(item) - {"type", "id", "summary", "content", "status", "encrypted_content"}), "opaque_present": item.get("encrypted_content") is not None, "summary_is_empty": item.get("summary") == [], "content_types": ["reasoning_text" if part.get("type") == "reasoning_text" else "other" for part in _array(item.get("content")) if isinstance(part, dict)]} for item in inputs if isinstance(item, dict) and item.get("type") == "reasoning"]
    return {"model": body.get("model"), "public_reasoning_items": public_reasoning_items, "reasoning_shapes": reasoning_shapes, "developer_messages": messages, "tools": [{"type": tool.get("type"), "name": tool.get("name"), "child_types": sorted({str(child.get("type")) for child in _array(tool.get("tools")) if isinstance(child, dict)})} for tool in _array(body.get("tools")) if isinstance(tool, dict)]}


def main() -> int:
    module_name, output = sys.argv[1:3]
    sys.argv = [module_name, *sys.argv[3:]]
    observations = []
    original = server.prepare_codex_request
    original_feed = StreamDecoder.feed

    def prepare(body, target, **kwargs):
        observations.append(summarize(body))
        return original(body, target, **kwargs)

    def feed(decoder, event):
        try:
            deltas = original_feed(decoder, event)
            if any(delta.kind == "reasoning_text" and delta.text for delta in deltas) and not any(item.get("public_reasoning_received") for item in observations):
                observations.append({"public_reasoning_received": True})
            return deltas
        except StreamError as exc:
            # Fixed categories only: an upstream field name/error message may
            # contain arbitrary data and must not enter diagnostic output.
            category = "unsupported_reasoning" if any(word in str(exc) for word in ("reasoning", "thinking")) else "invalid_stream"
            observations.append({"stream_error": category})
            raise

    server.prepare_codex_request = prepare
    StreamDecoder.feed = feed
    try:
        return importlib.import_module(module_name).main()
    finally:
        server.prepare_codex_request = original
        StreamDecoder.feed = original_feed
        Path(output).write_text(json.dumps(observations), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
