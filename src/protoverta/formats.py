"""Output-format mapping without prompt emulation or weakened schemas."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from .errors import ConversionError


@dataclass(frozen=True, slots=True)
class OutputFormat:
    kind: str
    schema: dict[str, Any] | None = None
    name: str = "protoverta_output"
    strict: bool | None = None
    description: str | None = None


def _object(value, allowed, label):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ConversionError(f"invalid or unsupported {label} fields")
    return value


def _portable_schema(schema, *, root=True, depth=0):
    """Small shared strict subset; never rewrite constraints to fit a provider."""
    if depth > 32:
        raise ConversionError("structured output schema nesting exceeds 32")
    allowed = {"type", "properties", "required", "additionalProperties", "items", "enum", "anyOf", "title", "description"}
    node = _object(schema, allowed, "portable structured output schema")
    for key in ("title", "description"):
        if key in node and not isinstance(node[key], str):
            raise ConversionError(f"schema {key} must be text")
    kind = node.get("type")
    types = kind if isinstance(kind, list) else [kind] if kind else []
    if any(not isinstance(item, str) or item not in {"object", "array", "string", "integer", "number", "boolean", "null"} for item in types):
        raise ConversionError("unsupported structured output schema type")
    if root and kind != "object":
        raise ConversionError("portable structured output schema requires an object root")
    if not types and "anyOf" not in node:
        raise ConversionError("structured output schema needs type or anyOf")
    if "object" in types:
        properties = node.get("properties", {})
        required = node.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list) or any(not isinstance(name, str) for name in required) or len(required) != len(set(required)) or set(required) != set(properties) or node.get("additionalProperties") is not False:
            raise ConversionError("portable strict objects require every property and additionalProperties=false")
        for child in properties.values():
            _portable_schema(child, root=False, depth=depth + 1)
    elif set(node) & {"properties", "required", "additionalProperties"}:
        raise ConversionError("object schema fields require object type")
    if "array" in types:
        _portable_schema(node.get("items"), root=False, depth=depth + 1)
    elif "items" in node:
        raise ConversionError("items requires array type")
    if "enum" in node:
        values = node["enum"]
        if not isinstance(values, list) or not values or any(isinstance(value, (dict, list)) for value in values):
            raise ConversionError("portable enum requires nonempty scalar values")
    if "anyOf" in node:
        children = node["anyOf"]
        if not isinstance(children, list) or not children:
            raise ConversionError("anyOf must contain schema alternatives")
        for child in children:
            _portable_schema(child, root=False, depth=depth + 1)


def decode_output_format(body: dict, protocol: str) -> OutputFormat | None:
    if protocol == "chat":
        value = body.get("response_format")
    elif protocol == "responses":
        text = _object(body.get("text", {}), {"format"}, "Responses text")
        value = text.get("format")
    else:
        value = body.get("output_config", {}).get("format")
    if value is None:
        return None
    value = _object(value, {"type", "json_schema"} if protocol == "chat" else {"type", "schema", "name", "strict", "description"}, "output format")
    kind = value.get("type")
    if isinstance(kind, str) and kind in {"text", "json_object"} and protocol != "messages":
        _object(value, {"type"}, "output format")
        return OutputFormat(kind) if kind == "json_object" else None
    if kind != "json_schema":
        raise ConversionError("unsupported output format type")
    details = _object(value.get("json_schema"), {"name", "schema", "strict", "description"}, "Chat json_schema") if protocol == "chat" else value
    if protocol == "messages":
        _object(value, {"type", "schema"}, "Messages output format")
    schema = details.get("schema")
    if not isinstance(schema, dict):
        raise ConversionError("output schema must be an object")
    name = details.get("name", "protoverta_output") if protocol == "messages" else details.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        raise ConversionError("output schema name must contain 1-64 letters, digits, _ or -")
    strict = True if protocol == "messages" else details.get("strict")
    if strict is not None and not isinstance(strict, bool):
        raise ConversionError("output schema strict must be boolean")
    description = details.get("description")
    if description is not None and not isinstance(description, str):
        raise ConversionError("output schema description must be text")
    if protocol == "messages":
        _portable_schema(schema)
    return OutputFormat(kind, copy.deepcopy(schema), name, strict, description)


def encode_output_format(value: OutputFormat | None, protocol: str) -> dict:
    if value is None:
        return {}
    if value.kind == "json_object":
        if protocol == "messages":
            raise ConversionError("Messages has no equivalent of unconstrained JSON object mode")
        wire = {"type": "json_object"}
    else:
        if protocol == "messages":
            if value.strict is not True:
                raise ConversionError("Messages structured output requires explicit strict=true")
            if value.description:
                raise ConversionError("Messages cannot preserve output-format description; put descriptions inside the schema")
            _portable_schema(value.schema)
            return {"output_config": {"format": {"type": "json_schema", "schema": copy.deepcopy(value.schema)}}}
        details = {"name": value.name, "schema": copy.deepcopy(value.schema)}
        if value.strict is not None:
            details["strict"] = value.strict
        if value.description is not None:
            details["description"] = value.description
        wire = {"type": "json_schema", "json_schema": details} if protocol == "chat" else {"type": "json_schema", **details}
    return {"response_format": wire} if protocol == "chat" else {"text": {"format": wire}}
