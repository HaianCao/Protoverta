"""Synthetic harness instrumentation; records shapes, never headers or bodies."""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

from protoverta import server


def summarize(body: dict) -> dict:
    messages = []
    for index, item in enumerate(body.get("input", [])):
        if not isinstance(item, dict) or item.get("role") != "developer":
            continue
        content = item.get("content", "")
        text = content if isinstance(content, str) else "".join(block.get("text", "") for block in content if isinstance(block, dict))
        messages.append({"index": index, "tags": sorted(set(re.findall(r"</?([a-zA-Z_][a-zA-Z0-9_]*)", text)))})
    return {"model": body.get("model"), "developer_messages": messages, "tools": [{"type": tool.get("type"), "name": tool.get("name"), "child_types": sorted({str(child.get("type")) for child in tool.get("tools", []) if isinstance(child, dict)})} for tool in body.get("tools", []) if isinstance(tool, dict)]}


def main() -> int:
    module_name, output = sys.argv[1:3]
    sys.argv = [module_name, *sys.argv[3:]]
    observations = []
    original = server.prepare_codex_request

    def prepare(body, target, **kwargs):
        observations.append(summarize(body))
        return original(body, target, **kwargs)

    server.prepare_codex_request = prepare
    try:
        return importlib.import_module(module_name).main()
    finally:
        Path(output).write_text(json.dumps(observations), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
