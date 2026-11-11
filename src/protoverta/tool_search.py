"""Opt-in client-search adaptation, not hosted search or a tool executor.

Each request reconstructs the active tool set from explicit, paired history.
Loaded definitions are exposed eagerly for this generation; native cache/context
placement is not reproduced. Search arguments are buffered per call, not exposed
as invented function-argument events on the Responses search item.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .errors import ConversionError

if TYPE_CHECKING:
    from .streaming import Delta

_NAME = "pv_client_tool_search"


def _only(value: Any, allowed: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) - allowed:
        raise ConversionError(f"invalid {label}")


def _call_id(value: dict[str, Any]) -> str:
    result = value.get("call_id")
    if not isinstance(result, str) or not result:
        raise ConversionError("client tool search requires a nonempty call_id")
    if value.get("execution") != "client" or value.get("status") != "completed":
        raise ConversionError("only completed explicit client tool search is supported")
    return result


def _definition(tool: Any, *, loaded: bool = False) -> dict[str, Any] | None:
    if not isinstance(tool, dict) or tool.get("type") not in ("function", "custom", "namespace"):
        raise ConversionError("loaded search tools must be function, custom or namespace definitions")
    result = copy.deepcopy(tool)
    if not isinstance(result.get("name"), str) or not result["name"]:
        raise ConversionError("loaded tool name must be nonempty")
    if "defer_loading" in result and not isinstance(result.pop("defer_loading"), bool):
        raise ConversionError("defer_loading must be a boolean")
    if result.get("name") == _NAME:
        raise ConversionError("reserved client tool search name collision")
    if result["type"] == "namespace":
        if "defer_loading" in tool:
            raise ConversionError("defer_loading applies to namespace children, not the namespace")
        if not isinstance(result.get("tools"), list):
            raise ConversionError("search namespace requires a tools array")
        children: list[dict[str, Any]] = []
        names: set[Any] = set()
        for child in result["tools"]:
            if not isinstance(child, dict) or child.get("type") not in ("function", "custom"):
                raise ConversionError("search namespace children must be function or custom tools")
            normalized = _definition(child, loaded=loaded)
            name = child.get("name")
            if name in names:
                raise ConversionError("duplicate namespace tool name")
            names.add(name)
            if normalized is not None:
                children.append(normalized)
        result["tools"] = children
        if not children:
            return None
    elif not loaded and tool.get("defer_loading") is True:
        return None
    return result


class ClientToolSearchMap:
    """One-use request map for explicit client-side tool search."""

    def __init__(self) -> None:
        self.enabled = False
        self._used = False
        self._available = False
        self._snapshot_tools: list[dict[str, Any]] = []

    def prepare(self, body: dict[str, Any]) -> dict[str, Any]:
        if self._used:
            raise ConversionError("client search mappings cannot be reused")
        self._used = True
        result = copy.deepcopy(body)
        tools = result.get("tools", [])
        if not isinstance(tools, list):
            raise ConversionError("tools must be an array")
        searches = [tool for tool in tools if isinstance(tool, dict) and tool.get("type") == "tool_search"]
        inputs = result.get("input")
        history = inputs if isinstance(inputs, list) else []
        has_history = any(isinstance(item, dict) and item.get("type") in ("tool_search_call", "tool_search_output") for item in history)
        if not searches and not has_history:
            return result
        if len(searches) > 1:
            raise ConversionError("at most one client tool search definition is supported")
        self.enabled = True
        self._available = bool(searches)
        definitions: dict[str, dict[str, Any]] = {}

        def add(tool: Any, *, loaded: bool = False) -> None:
            normalized = _definition(tool, loaded=loaded)
            if normalized is None:
                return
            name = normalized.get("name")
            if not isinstance(name, str) or not name:
                raise ConversionError("loaded tool name must be nonempty")
            if name in definitions and definitions[name] != normalized:
                previous = definitions[name]
                if previous.get("type") != "namespace" or normalized["type"] != "namespace":
                    raise ConversionError("conflicting loaded tool definitions")
                for key in (set(previous) & set(normalized)) - {"tools"}:
                    if previous[key] != normalized[key]:
                        raise ConversionError("conflicting loaded namespace definitions")
                children = {child["name"]: child for child in previous["tools"]}
                for child in normalized["tools"]:
                    if child["name"] in children and children[child["name"]] != child:
                        raise ConversionError("conflicting loaded tool definitions")
                    children[child["name"]] = child
                normalized = {**previous, **normalized, "tools": list(children.values())}
            definitions[name] = normalized

        for tool in tools:
            if tool in searches:
                continue
            add(tool)
        pending: set[str] = set()
        seen: set[str] = set()
        rewritten: list[Any] = []
        for item in history:
            if not isinstance(item, dict) or item.get("type") not in ("tool_search_call", "tool_search_output"):
                if isinstance(item, dict) and item.get("type") in ("function_call", "custom_tool_call") and item.get("name") == _NAME:
                    raise ConversionError("reserved client search name in ordinary history")
                rewritten.append(item)
                continue
            call_id = _call_id(item)
            if item["type"] == "tool_search_call":
                _only(item, {"type", "id", "execution", "call_id", "status", "arguments"}, "tool_search_call")
                if call_id in seen or not isinstance(item.get("arguments"), dict):
                    raise ConversionError("duplicate search call ID or non-object arguments")
                seen.add(call_id)
                pending.add(call_id)
                rewritten.append(
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": _NAME,
                        "arguments": json.dumps(item["arguments"], ensure_ascii=False),
                        "status": "completed",
                    }
                )
            else:
                _only(item, {"type", "id", "execution", "call_id", "status", "tools"}, "tool_search_output")
                if call_id not in pending or not isinstance(item.get("tools"), list):
                    raise ConversionError("search output has no unique preceding call or tools array")
                pending.remove(call_id)
                for tool in item["tools"]:
                    add(tool, loaded=True)
                rewritten.append({"type": "function_call_output", "call_id": call_id, "output": json.dumps({"tools": item["tools"]}, ensure_ascii=False)})
        if pending:
            raise ConversionError("unfinished client search history must not be replayed")
        self._snapshot_tools = copy.deepcopy([*definitions.values(), *searches])
        result["tools"] = list(definitions.values())
        if searches:
            search = searches[0]
            _only(search, {"type", "execution", "description", "parameters"}, "client tool search definition")
            if search.get("execution") != "client" or not isinstance(search.get("parameters"), dict) or search["parameters"].get("type") != "object":
                raise ConversionError("client search requires explicit client execution and an object argument schema")
            if not isinstance(search.get("description", ""), str):
                raise ConversionError("search description must be text")
            result["tools"].append(
                {"type": "function", "name": _NAME, "description": search.get("description", ""), "parameters": search["parameters"], "strict": False}
            )
        if isinstance(inputs, list):
            result["input"] = rewritten
        return result

    def snapshot_tools(self) -> list[dict[str, Any]]:
        """Active logical definitions, before namespace/wire aliasing."""
        return copy.deepcopy(self._snapshot_tools)

    def stream_delta(self, delta: Delta) -> Delta:
        if self.enabled and delta.kind == "tool_start" and delta.name == _NAME:
            if not self._available or delta.tool_type != "function":
                raise ConversionError("upstream called an unavailable client search tool")
            return replace(delta, tool_type="tool_search")
        return delta

    def restore(self, body: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(body)
        if not self.enabled:
            return result
        for item in result.get("output", []):
            if item.get("type") != "function_call" or item.get("name") != _NAME:
                continue
            if not self._available:
                raise ConversionError("upstream called an unavailable client search tool")
            try:
                arguments = json.loads(item["arguments"])
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ConversionError("invalid client search arguments") from exc
            if not isinstance(arguments, dict) or item.get("status") != "completed":
                raise ConversionError("client search output must be a completed object call")
            item.pop("name")
            item["type"] = "tool_search_call"
            item["execution"] = "client"
            item["arguments"] = arguments
        return result
