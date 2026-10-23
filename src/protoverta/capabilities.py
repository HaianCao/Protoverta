"""Operator-recorded capabilities, scoped to exact API prefix/protocol/model.

Loading and checking records never sends requests or treats /models as evidence.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit, urlunsplit

from .core import Protocol
from .errors import ConversionError

FEATURES = frozenset({"text", "streaming", "tools", "forced_tool_choice", "json_object", "json_schema", "strict_json_schema", "reasoning_effort", "public_reasoning", "images", "mid_conversation_system"})


def _prefix(value: str) -> str:
    if not isinstance(value, str) or any(char.isspace() for char in value):
        raise ValueError("capability base_url must be an API prefix")
    parsed = urlsplit(value)
    _ = parsed.port
    local = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not parsed.hostname or (parsed.scheme != "https" and not local) or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("capability base_url requires HTTPS or loopback HTTP, without credentials/query/fragment")
    path = parsed.path.rstrip("/")
    if path.endswith(("/responses", "/messages", "/chat/completions")):
        raise ValueError("capability base_url must omit the endpoint suffix")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


@dataclass(frozen=True, slots=True)
class CapabilityRecord:
    base_url: str
    protocol: Protocol | str
    model: str
    features: Mapping[str, bool]
    checked_at: str
    evidence: str

    def __post_init__(self):
        object.__setattr__(self, "base_url", _prefix(self.base_url))
        object.__setattr__(self, "protocol", Protocol(self.protocol))
        if not isinstance(self.model, str) or not self.model or any(char.isspace() for char in self.model):
            raise ValueError("capability model must be an exact nonempty model ID")
        if not isinstance(self.features, Mapping) or set(self.features) - FEATURES or any(not isinstance(value, bool) for value in self.features.values()):
            raise ValueError("capabilities require known feature names and boolean values")
        if not isinstance(self.checked_at, str):
            raise TypeError("checked_at must be an ISO date string")
        date.fromisoformat(self.checked_at)
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError("capability evidence must identify a documentation or probe result")
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    supported: tuple[str, ...]
    unsupported: tuple[str, ...]
    unknown: tuple[str, ...]

    def require(self, *, verified: bool = False) -> None:
        problems = []
        if self.unsupported:
            problems.append("unsupported=" + ",".join(self.unsupported))
        if verified and self.unknown:
            problems.append("unverified=" + ",".join(self.unknown))
        if problems:
            raise ConversionError("upstream capability preflight rejected: " + "; ".join(problems))


def request_features(body: dict, protocol: Protocol | str) -> frozenset[str]:
    """Inspect outbound wire options; this is not protocol/schema validation."""
    protocol = Protocol(protocol)
    if not isinstance(body, dict):
        raise ConversionError("capability preflight requires an object request")
    features = {"text"}
    if body.get("stream") is True:
        features.add("streaming")
    if body.get("tools"):
        features.add("tools")
    choice = body.get("tool_choice")
    if choice == "required" or isinstance(choice, dict) and isinstance(choice.get("type"), str) and choice["type"] in {"function", "tool", "any"}:
        features.add("forced_tool_choice")
    if protocol == Protocol.CHAT:
        format_value = body.get("response_format")
        effort = body.get("reasoning_effort")
    elif protocol == Protocol.RESPONSES:
        text = body.get("text")
        format_value = text.get("format") if isinstance(text, dict) else None
        reasoning = body.get("reasoning")
        effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
    else:
        output_config = body.get("output_config")
        format_value = output_config.get("format") if isinstance(output_config, dict) else None
        effort = output_config.get("effort") if isinstance(output_config, dict) else None
        seen_conversation = False
        for item in body.get("messages", []) if isinstance(body.get("messages"), list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("role") == "system" and seen_conversation:
                features.add("mid_conversation_system")
            elif item.get("role") != "system":
                seen_conversation = True
    if effort is not None and effort != "none":
        features.add("reasoning_effort")
    if isinstance(format_value, dict):
        if format_value.get("type") == "json_object":
            features.add("json_object")
        elif format_value.get("type") == "json_schema":
            features.add("json_schema")
            detail = format_value.get("json_schema", {}) if protocol == Protocol.CHAT else format_value
            if protocol == Protocol.MESSAGES or isinstance(detail, dict) and detail.get("strict") is True:
                features.add("strict_json_schema")

    # Inspect message content only: tool schemas may themselves use an image
    # property and must not be mistaken for a multimodal request.
    def contains_image(value):
        if isinstance(value, list):
            return any(contains_image(item) for item in value)
        if isinstance(value, dict):
            return isinstance(value.get("type"), str) and value["type"] in {"image", "image_url", "input_image"} or contains_image(value.get("content")) or contains_image(value.get("output"))
        return False

    if contains_image(body.get("messages", body.get("input"))):
        features.add("images")
    history = body.get("messages" if protocol != Protocol.RESPONSES else "input")
    if isinstance(history, list) and any(isinstance(item, dict) and (item.get("role") == "assistant" and item.get("reasoning_content") is not None or item.get("type") == "reasoning" and bool(item.get("content"))) for item in history):
        features.add("public_reasoning")
    return frozenset(features)


class CapabilityRegistry:
    def __init__(self, records: tuple[CapabilityRecord, ...] = ()):
        index = {}
        for record in records:
            if not isinstance(record, CapabilityRecord):
                raise TypeError("registry entries must be CapabilityRecord instances")
            key = (record.base_url, record.protocol, record.model)
            if key in index:
                raise ValueError("duplicate capability entry for API prefix/protocol/model")
            index[key] = record
        self._records = MappingProxyType(index)

    @classmethod
    def from_dict(cls, body: dict) -> CapabilityRegistry:
        if not isinstance(body, dict) or set(body) != {"schema_version", "models"} or type(body["schema_version"]) is not int or body["schema_version"] != 1 or not isinstance(body["models"], list):
            raise ValueError("invalid capability registry schema")
        fields = {"base_url", "protocol", "model", "features", "checked_at", "evidence"}
        records = []
        for item in body["models"]:
            if not isinstance(item, dict) or set(item) != fields:
                raise ValueError("invalid capability record fields")
            records.append(CapabilityRecord(**item))
        return cls(tuple(records))

    @classmethod
    def load(cls, path: str | Path) -> CapabilityRegistry:
        with Path(path).open("rb") as source:
            raw = source.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("capability registry exceeds 2 MiB")
        return cls.from_dict(json.loads(raw))

    def assess(self, base_url: str, protocol: Protocol | str, model: str, features) -> CapabilityReport:
        if not isinstance(model, str) or not model:
            raise ConversionError("capability preflight requires an exact model ID")
        requested = frozenset(features)
        if requested - FEATURES:
            raise ValueError("unknown requested capability")
        record = self._records.get((_prefix(base_url), Protocol(protocol), model))
        values = record.features if record else {}
        return CapabilityReport(
            tuple(sorted(name for name in requested if values.get(name) is True)),
            tuple(sorted(name for name in requested if values.get(name) is False)),
            tuple(sorted(name for name in requested if name not in values)),
        )

    def preflight(self, base_url: str, protocol: Protocol | str, body: dict, *, require_verified: bool = False) -> CapabilityReport:
        features = request_features(body, protocol)
        report = self.assess(base_url, protocol, body.get("model"), features)
        report.require(verified=require_verified)
        return report
