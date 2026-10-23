"""Request-scoped reversible aliases for Responses function namespaces.

Only synchronous function tools are supported. No tools are executed here.
Mappings are rebuilt from each request, never shared between clients/sessions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

from .core import ConversionError

_PREFIX = "pv_ns_"


def _name(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ConversionError(f"invalid {label}")
    return value


class NamespaceMap:
    """Flatten namespace definitions/history and restore Responses call items."""

    def __init__(self):
        self.aliases: dict[str, tuple[str, str]] = {}
        self.flat_names: set[str] = set()

    def alias(self, namespace: str, name: str) -> str:
        pair = (_name(namespace, "namespace"), _name(name, "function name"))
        alias = _PREFIX + hashlib.sha256(json.dumps(pair, separators=(",", ":")).encode()).hexdigest()[:48]
        if alias in self.flat_names or (alias in self.aliases and self.aliases[alias] != pair):
            raise ConversionError("namespace alias collides with another tool")
        self.aliases[alias] = pair
        return alias

    def prepare(self, body: dict) -> dict:
        result = copy.deepcopy(body)
        tools = result.get("tools", [])
        if not isinstance(tools, list):
            raise ConversionError("tools must be an array")
        for tool in tools:
            if isinstance(tool, dict) and tool.get("type") == "function":
                name = _name(tool.get("name"), "function name")
                if name in self.flat_names:
                    raise ConversionError("duplicate function name")
                self.flat_names.add(name)
        inputs = result.get("input")
        if isinstance(inputs, list):
            for item in inputs:
                if isinstance(item, dict) and item.get("type") == "function_call" and item.get("namespace") is None:
                    self.flat_names.add(_name(item.get("name"), "historical function name"))
        flattened = []
        seen: set[tuple[str, str]] = set()
        seen_namespaces: set[str] = set()
        for tool in tools:
            if not isinstance(tool, dict) or tool.get("type") != "namespace":
                flattened.append(tool)
                continue  # The strict converter validates ordinary tools.
            if set(tool) - {"type", "name", "description", "tools"}:
                raise ConversionError("unsupported namespace fields")
            namespace = _name(tool.get("name"), "namespace")
            if namespace in seen_namespaces:
                raise ConversionError("duplicate namespace")
            seen_namespaces.add(namespace)
            description = tool.get("description", "")
            if not isinstance(description, str):
                raise ConversionError("namespace description must be text")
            children = tool.get("tools")
            if not isinstance(children, list) or not children:
                raise ConversionError("namespace must contain function tools")
            for child in children:
                if not isinstance(child, dict) or child.get("type") != "function" or set(child) - {"type", "name", "description", "parameters", "strict"}:
                    raise ConversionError("namespace mapping supports only synchronous function tools")
                if child.get("strict") not in (None, False):
                    raise ConversionError("strict namespaced functions are not portable")
                name = _name(child.get("name"), "function name")
                pair = (namespace, name)
                if pair in seen:
                    raise ConversionError("duplicate namespaced function")
                seen.add(pair)
                child_description = child.get("description", "")
                if not isinstance(child_description, str):
                    raise ConversionError("function description must be text")
                mapped = dict(child, name=self.alias(namespace, name))
                mapped["description"] = "\n\n".join(part for part in (description, child_description) if part)
                flattened.append(mapped)
        if "tools" in result:
            result["tools"] = flattened
        if isinstance(inputs, list):
            for item in inputs:
                if isinstance(item, dict) and item.get("type") == "function_call" and item.get("namespace") is not None:
                    item["name"] = self.alias(item.pop("namespace"), item.get("name"))
                elif isinstance(item, dict) and item.get("type") == "function_call":
                    item.pop("namespace", None)
        choice = result.get("tool_choice")
        if isinstance(choice, dict) and choice.get("namespace") is not None:
            if set(choice) != {"type", "namespace", "name"} or choice.get("type") != "function":
                raise ConversionError("invalid namespaced tool_choice")
            pair = (_name(choice["namespace"], "namespace"), _name(choice["name"], "function name"))
            if pair not in seen:
                raise ConversionError("namespaced tool_choice must name a declared function")
            result["tool_choice"] = {"type": "function", "name": self.alias(*pair)}
        return result

    def restore(self, body: dict) -> dict:
        """Restore JSON output, SSE item events and terminal response snapshots."""
        if not self.aliases:
            return body
        result = copy.deepcopy(body)

        def restore_item(item):
            if not isinstance(item, dict) or item.get("type") != "function_call":
                return
            name = item.get("name")
            if not isinstance(name, str):
                raise ConversionError("invalid function name in namespace result")
            if name in self.aliases:
                namespace, original = self.aliases[name]
                item.update(name=original, namespace=namespace)
            elif isinstance(name, str) and name.startswith(_PREFIX) and name not in self.flat_names:
                raise ConversionError("upstream returned an unknown namespace alias")

        restore_item(result.get("item"))
        for item in result.get("output", []):
            restore_item(item)
        response = result.get("response")
        if isinstance(response, dict):
            for item in response.get("output", []):
                restore_item(item)
        return result
