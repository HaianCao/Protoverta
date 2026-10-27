"""Operator-recorded capabilities, scoped to exact API prefix/protocol/model.

Loading and checking records never sends requests or treats /models as evidence.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit, urlunsplit

from .core import Protocol
from .errors import ConversionError

FEATURES = frozenset({"text", "streaming", "tools", "custom_tools", "forced_tool_choice", "json_object", "json_schema", "strict_json_schema", "reasoning_effort", "public_reasoning", "images", "mid_conversation_system", "top_p", "stop_sequences", "strict_tools", "image_tool_results"})
FEATURES = FEATURES | {"documents", "compaction", "prompt_caching", "audio", "video", "hosted_tools"}

POLICY_VALUES = {
    "messages_usage_policy": ("strict", "deferred"),
    "upstream_reasoning": ("provider_default", "disabled", "openrouter_disabled"),
    "namespace_tools": ("reject", "flatten"),
    "tool_names": ("reject", "alias"),
    "client_tool_search": ("reject", "function"),
    "public_reasoning": (False, True),
}


def route_policies(config) -> dict:
    """Resolved policy fingerprint; unknown/missing policy values never wildcard."""
    return {key: getattr(config, key) for key in POLICY_VALUES}


@dataclass(frozen=True, slots=True)
class RouteCapabilityRecord:
    base_url: str
    protocol: Protocol | str
    model: str
    source_protocol: Protocol | str
    streaming: bool
    client_profile: str
    policies: Mapping
    features: Mapping[str, bool]
    checked_at: str
    evidence: str
    verified_with: str
    identity: str | None = None

    def __post_init__(self):
        record = CapabilityRecord(self.base_url, self.protocol, self.model, self.features, self.checked_at, self.evidence)
        object.__setattr__(self, "base_url", record.base_url)
        object.__setattr__(self, "protocol", record.protocol)
        object.__setattr__(self, "source_protocol", Protocol(self.source_protocol))
        object.__setattr__(self, "features", record.features)
        if type(self.streaming) is not bool or self.client_profile not in ("strict", "codex", "claude"):
            raise ValueError("route requires a boolean streaming mode and known client profile")
        if not isinstance(self.verified_with, str) or not self.verified_with.strip():
            raise ValueError("route evidence must record tested core/client versions")
        _policy_key(self.policies)
        if self.identity is not None and (not isinstance(self.identity, str) or not re.fullmatch(r"[a-f0-9]{64}", self.identity)):
            raise ValueError("evidence identity must be a SHA-256 digest")
        object.__setattr__(self, "policies", MappingProxyType(dict(self.policies)))


def _policy_key(policies):
    if not isinstance(policies, Mapping) or set(policies) != set(POLICY_VALUES):
        raise ValueError("route policies must explicitly declare every known policy")
    for key, allowed in POLICY_VALUES.items():
        value = policies[key]
        if type(value) is not type(allowed[0]) or value not in allowed:
            raise ValueError("invalid route policy value")
    return tuple((key, policies[key]) for key in sorted(POLICY_VALUES))


def _prefix(value: str) -> str:
    if not isinstance(value, str) or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("capability base_url must be an API prefix")
    parsed = urlsplit(value)
    port = parsed.port
    if port is not None and port < 1:
        raise ValueError("capability base_url port must be between 1 and 65535")
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
    max_output_tokens: int | None = None
    context_window_tokens: int | None = None
    reasoning_efforts: tuple[str, ...] | None = None

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
        for name in ("max_output_tokens", "context_window_tokens"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"{name} must be a positive integer")
        if self.reasoning_efforts is not None and (not isinstance(self.reasoning_efforts, tuple) or not self.reasoning_efforts or any(not isinstance(effort, str) or effort not in {"none", "minimal", "low", "medium", "high", "xhigh", "max"} for effort in self.reasoning_efforts) or len(set(self.reasoning_efforts)) != len(self.reasoning_efforts)):
            raise ValueError("reasoning_efforts must contain distinct known effort values")


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
    tools = body.get("tools")
    if tools is not None and (not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools)):
        raise ConversionError("capability preflight requires tools to be an array of objects")
    features = {"text"}
    if body.get("top_p") is not None:
        features.add("top_p")
    if body.get("stream") is True:
        features.add("streaming")
    if tools:
        features.add("tools")
        def hosted(tool):
            kind = tool.get("type", "function")
            if kind == "namespace":
                nested = tool.get("tools", [])
                return isinstance(nested, list) and any(isinstance(item, dict) and hosted(item) for item in nested)
            return kind not in ("function", "custom")
        if any(hosted(tool) for tool in tools):
            features.add("hosted_tools")
        if any(isinstance(tool, dict) and tool.get("type") == "custom" for tool in body["tools"]):
            features.add("custom_tools")
        if any(isinstance(tool, dict) and (tool.get("function", tool) if protocol == Protocol.CHAT else tool).get("strict") is True for tool in body["tools"] if isinstance(tool, dict) and (protocol != Protocol.CHAT or isinstance(tool.get("function"), dict))):
            features.add("strict_tools")
    if body.get("stop" if protocol == Protocol.CHAT else "stop_sequences") is not None:
        features.add("stop_sequences")
    choice = body.get("tool_choice")
    if choice == "required" or isinstance(choice, dict) and isinstance(choice.get("type"), str) and choice["type"] in {"function", "custom", "tool", "any"}:
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
    def contains_document(value):
        if isinstance(value, list):
            return any(contains_document(item) for item in value)
        if isinstance(value, dict):
            return value.get("type") in ("file", "input_file", "document") or contains_document(value.get("content")) or contains_document(value.get("output"))
        return False
    if contains_document(body.get("messages", body.get("input"))):
        features.add("documents")
    def media_types(value):
        if isinstance(value, list):
            for item in value:
                media_types(item)
        elif isinstance(value, dict):
            kind = value.get("type")
            if isinstance(kind, str):
                if "audio" in kind:
                    features.add("audio")
                if "video" in kind:
                    features.add("video")
            if value.get("audio") is not None:
                features.add("audio")
            media_types(value.get("content"))
            media_types(value.get("output"))
    media_types(body.get("messages", body.get("input")))
    if body.get("audio") is not None or isinstance(body.get("modalities"), list) and "audio" in body["modalities"]:
        features.add("audio")
    def rich_result(value):
        if isinstance(value, list):
            return any(rich_result(item) for item in value)
        if isinstance(value, dict):
            if isinstance(value.get("type"), str) and value["type"] in {"tool_result", "function_call_output", "custom_tool_call_output"}:
                return contains_image(value.get("content", value.get("output")))
            return rich_result(value.get("content"))
        return False
    if rich_result(body.get("messages", body.get("input"))):
        features.add("image_tool_results")
    history = body.get("messages" if protocol != Protocol.RESPONSES else "input")
    if isinstance(history, list) and any(isinstance(item, dict) and (item.get("type") in ("custom_tool_call", "custom_tool_call_output") or any(isinstance(call, dict) and call.get("type") == "custom" for call in (item.get("tool_calls") if isinstance(item.get("tool_calls"), list) else []))) for item in history):
        features.add("custom_tools")
    if isinstance(history, list) and any(isinstance(item, dict) and (item.get("role") == "assistant" and item.get("reasoning_content") is not None or item.get("type") == "reasoning" and bool(item.get("content"))) for item in history):
        features.add("public_reasoning")
    return frozenset(features)


class CapabilityRegistry:
    def __init__(self, records: tuple[CapabilityRecord, ...] = (), routes: tuple[RouteCapabilityRecord, ...] = ()):
        index = {}
        for record in records:
            if not isinstance(record, CapabilityRecord):
                raise TypeError("registry entries must be CapabilityRecord instances")
            key = (record.base_url, record.protocol, record.model)
            if key in index:
                raise ValueError("duplicate capability entry for API prefix/protocol/model")
            index[key] = record
        self._records = MappingProxyType(index)
        route_index = {}
        for route in routes:
            if not isinstance(route, RouteCapabilityRecord):
                raise TypeError("route entries must be RouteCapabilityRecord instances")
            key = (route.base_url, route.protocol, route.model, route.source_protocol,
                   route.streaming, route.client_profile, _policy_key(route.policies))
            if key in route_index:
                raise ValueError("duplicate exact route capability record")
            route_index[key] = route
        self._routes = MappingProxyType(route_index)

    @classmethod
    def from_dict(cls, body: dict) -> CapabilityRegistry:
        if not isinstance(body, dict) or type(body.get("schema_version")) is not int or body["schema_version"] not in {1, 2, 3} or set(body) != ({"schema_version", "models", "routes"} if body["schema_version"] == 3 else {"schema_version", "models"}) or not isinstance(body["models"], list):
            raise ValueError("invalid capability registry schema")
        fields = {"base_url", "protocol", "model", "features", "checked_at", "evidence"}
        optional = {"max_output_tokens", "context_window_tokens", "reasoning_efforts"} if body["schema_version"] >= 2 else set()
        records = []
        for item in body["models"]:
            if not isinstance(item, dict) or not fields <= set(item) or set(item) - fields - optional:
                raise ValueError("invalid capability record fields")
            item = dict(item)
            if "reasoning_efforts" in item and item["reasoning_efforts"] is not None:
                if not isinstance(item["reasoning_efforts"], list):
                    raise ValueError("reasoning_efforts must be an array")
                item["reasoning_efforts"] = tuple(item["reasoning_efforts"])
            records.append(CapabilityRecord(**item))
        routes = body.get("routes", [])
        if not isinstance(routes, list) or any(not isinstance(item, dict) for item in routes):
            raise ValueError("routes must be an array of route records")
        try:
            return cls(tuple(records), tuple(RouteCapabilityRecord(**item) for item in routes))
        except TypeError as exc:
            raise ValueError("invalid route record fields") from exc

    def assess_route(self, base_url, protocol, model, features, *, source_protocol, streaming, client_profile, policies, current_identity=None, max_age_days=None) -> CapabilityReport:
        if not isinstance(model, str) or not model or type(streaming) is not bool or client_profile not in ("strict", "codex", "claude"):
            raise ValueError("invalid exact route selector")
        requested = frozenset(features)
        if requested - FEATURES:
            raise ValueError("unknown requested route capability")
        key = (_prefix(base_url), Protocol(protocol), model, Protocol(source_protocol), streaming, client_profile, _policy_key(policies))
        record = self._routes.get(key)
        values = record.features if record else {}
        if max_age_days is not None and (type(max_age_days) is not int or max_age_days < 0):
            raise ValueError("evidence age limit must be a nonnegative number of days")
        if record and (current_identity is not None and current_identity != record.identity or max_age_days is not None and not 0 <= (date.today() - date.fromisoformat(record.checked_at)).days <= max_age_days):
            values = {key: value for key, value in values.items() if value is False}
        return CapabilityReport(tuple(sorted(name for name in requested if values.get(name) is True)),
                                tuple(sorted(name for name in requested if values.get(name) is False)),
                                tuple(sorted(name for name in requested if name not in values)))

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

    def lookup(self, base_url: str, protocol: Protocol | str, model: str) -> CapabilityRecord | None:
        """Return an immutable exact-match record, never an inferred catalog claim."""
        if not isinstance(model, str) or not model:
            raise ValueError("capability lookup requires an exact model ID")
        return self._records.get((_prefix(base_url), Protocol(protocol), model))

    def preflight(self, base_url: str, protocol: Protocol | str, body: dict, *, require_verified: bool = False) -> CapabilityReport:
        features = request_features(body, protocol)
        report = self.assess(base_url, protocol, body.get("model"), features)
        report.require(verified=require_verified)
        protocol = Protocol(protocol)
        record = self._records.get((_prefix(base_url), protocol, body.get("model")))
        if record is not None:
            budget = body.get("max_completion_tokens", body.get("max_tokens")) if protocol == Protocol.CHAT else body.get("max_tokens" if protocol == Protocol.MESSAGES else "max_output_tokens")
            if budget is not None and (type(budget) is not int or budget <= 0):
                raise ConversionError("capability preflight requires a positive output token limit")
            if budget is not None and record.max_output_tokens is not None and budget > record.max_output_tokens:
                raise ConversionError(f"upstream max_output_tokens limit exceeded ({record.max_output_tokens}); choose an explicit smaller budget")
            settings = body.get("reasoning" if protocol == Protocol.RESPONSES else "output_config")
            effort = body.get("reasoning_effort") if protocol == Protocol.CHAT else settings.get("effort") if isinstance(settings, dict) else None
            if effort is not None and record.reasoning_efforts is not None and (not isinstance(effort, str) or effort not in record.reasoning_efforts):
                raise ConversionError("upstream does not support the requested reasoning effort")
        return report
