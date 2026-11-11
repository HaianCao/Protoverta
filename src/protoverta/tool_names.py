"""Explicit request-local function aliases, independent of provider quirks.

Only protocol-defined name locations are edited. Arguments, descriptions,
schemas, text, IDs and results are never searched or replaced.
"""

from __future__ import annotations

import copy
import hashlib
import re
from typing import Any

from .core import Protocol
from .errors import ConversionError

_PORTABLE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_PREFIX = "pv_tool_"


def _objects(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _name_locations(body: dict[str, Any], protocol: Protocol, *, request: bool) -> list[dict[str, Any]]:
    locations: list[dict[str, Any]] = []
    if protocol == Protocol.CHAT:
        if request:
            locations.extend(
                tool[tool["type"]]
                for tool in _objects(body.get("tools"))
                if tool.get("type") in ("function", "custom") and isinstance(tool.get(tool["type"]), dict)
            )
            messages = _objects(body.get("messages"))
            choice = body.get("tool_choice")
            if isinstance(choice, dict) and choice.get("type") in ("function", "custom") and isinstance(choice.get(choice["type"]), dict):
                locations.append(choice[choice["type"]])
        else:
            messages = [choice["message"] for choice in _objects(body.get("choices")) if isinstance(choice.get("message"), dict)]
        for message in messages:
            if message.get("role", "assistant") == "assistant":
                locations.extend(
                    call[call["type"]]
                    for call in _objects(message.get("tool_calls"))
                    if call.get("type") in ("function", "custom") and isinstance(call.get(call["type"]), dict)
                )
    elif protocol == Protocol.MESSAGES:
        if request:
            locations.extend(_objects(body.get("tools")))
            messages = _objects(body.get("messages"))
            choice = body.get("tool_choice")
            if isinstance(choice, dict) and choice.get("type") == "tool":
                locations.append(choice)
        else:
            messages = [body]
        for message in messages:
            if message.get("role", "assistant") == "assistant":
                locations.extend(block for block in _objects(message.get("content")) if block.get("type") == "tool_use")
    else:
        if request:
            locations.extend(tool for tool in _objects(body.get("tools")) if tool.get("type") in ("function", "custom"))
            choice = body.get("tool_choice")
            if isinstance(choice, dict) and choice.get("type") in ("function", "custom"):
                locations.append(choice)
        locations.extend(item for item in _objects(body.get("input" if request else "output")) if item.get("type") in ("function_call", "custom_tool_call"))
    return locations


class ToolNameMap:
    """Rebuild deterministic aliases from definitions, history and forced choice.

    Create one instance per request. Native forwarding must not use this policy.
    """

    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.originals: set[str] = set()
        self._used = False

    def prepare(self, body: dict[str, Any], protocol: Protocol | str) -> dict[str, Any]:
        if self._used:
            raise ConversionError("tool name mappings cannot be reused across requests")
        self._used = True
        if not isinstance(body, dict):
            raise ConversionError("tool name mapping requires an object request")
        result = copy.deepcopy(body)
        locations = _name_locations(result, Protocol(protocol), request=True)
        names = [item.get("name") for item in locations]
        if any(not isinstance(name, str) or not name for name in names):
            raise ConversionError("function names must be nonempty strings")
        self.originals = {name for name in names if isinstance(name, str)}
        reserved = set(self.originals)
        encoded: dict[str, str] = {}
        for name in sorted(self.originals):
            if _PORTABLE.fullmatch(name):
                continue
            attempt = 0
            while True:
                try:
                    digest = hashlib.sha256((str(attempt) + ":" + name).encode("utf-8")).hexdigest()
                except UnicodeError as exc:
                    raise ConversionError("function name is not valid Unicode") from exc
                alias = _PREFIX + digest[:48]
                if alias not in reserved:
                    break
                attempt += 1
            reserved.add(alias)
            encoded[name] = alias
            self.aliases[alias] = name
        for item in locations:
            item["name"] = encoded.get(item["name"], item["name"])
        return result

    def decode(self, name: str) -> str:
        if name in self.aliases:
            return self.aliases[name]
        if name.startswith(_PREFIX) and name not in self.originals:
            raise ConversionError("upstream returned an unknown function alias")
        return name

    def restore(self, body: dict[str, Any], protocol: Protocol | str) -> dict[str, Any]:
        result = copy.deepcopy(body)
        for item in _name_locations(result, Protocol(protocol), request=False):
            name = item.get("name")
            if not isinstance(name, str):
                raise ConversionError("invalid function name in upstream result")
            item["name"] = self.decode(name)
        return result
