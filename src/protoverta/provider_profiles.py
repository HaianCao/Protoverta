"""Evidence-scoped wire quirks; never strip unsupported request semantics."""

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path
from types import MappingProxyType

from .capabilities import _prefix
from .core import Protocol
from .errors import ConversionError


@dataclass(frozen=True)
class ProviderProfile:
    base_url: str
    protocol: str
    model: str
    checked_at: str
    evidence: str
    chat_token_field: str
    token_budget_semantics: str
    reasoning_field: str | None = None
    reasoning_values: Mapping | None = None
    reasoning_semantics: str | None = None
    prompt_cache_semantics: str | None = None
    parameter_aliases: Mapping[str, Mapping[str, str]] | None = None
    sampling_defaults: Mapping[str, float] | None = None

    def __post_init__(self):
        object.__setattr__(self, "base_url", _prefix(self.base_url))
        object.__setattr__(self, "protocol", Protocol(self.protocol))
        if self.protocol != Protocol.CHAT:
            raise ValueError("chat token profiles require a Chat upstream")
        if not isinstance(self.model, str) or not self.model or any(c.isspace() for c in self.model):
            raise ValueError("profile model must be an exact model ID")
        if not isinstance(self.checked_at, str):
            raise TypeError("profile checked_at must be an ISO date")
        date.fromisoformat(self.checked_at)
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError("profile requires documentation/probe evidence")
        if self.sampling_defaults is not None:
            from .core import _optional_temperature, _optional_top_p

            if not isinstance(self.sampling_defaults, Mapping) or not self.sampling_defaults or set(self.sampling_defaults) - {"temperature", "top_p"}:
                raise ValueError("sampling defaults require explicit temperature/top_p values")
            normalized = {}
            for key, value in self.sampling_defaults.items():
                checked = (_optional_temperature if key == "temperature" else _optional_top_p)(value)
                if checked is None:
                    raise ValueError("a configured sampling default cannot be unknown")
                normalized[key] = checked
            object.__setattr__(self, "sampling_defaults", MappingProxyType(normalized))
        if self.chat_token_field not in ("max_tokens", "max_completion_tokens"):
            raise ValueError("unsupported Chat output-token field")
        if self.token_budget_semantics != "completion_total":
            raise ValueError("profile requires evidence of equivalent completion-total budget semantics")
        if self.reasoning_field is not None:
            if not isinstance(self.reasoning_field, str) or not re.fullmatch(r"(?:reasoning|thinking|reasoning_effort)(?:\.[a-z_]+){0,2}", self.reasoning_field):
                raise ValueError("reasoning profile requires an explicit bounded vendor field")
            if self.reasoning_semantics != "effort_equivalent" or not isinstance(self.reasoning_values, Mapping) or not self.reasoning_values or set(self.reasoning_values) - {"none", "low", "medium", "high"}:
                raise ValueError("reasoning mapping requires equivalent-effort evidence")
            if any(type(value) not in (str, bool, int) or isinstance(value, str) and (not value or len(value) > 64) for value in self.reasoning_values.values()):
                raise ValueError("reasoning values must be bounded JSON scalars")
            if len({(type(value), value) for value in self.reasoning_values.values()}) != len(self.reasoning_values):
                raise ValueError("reasoning mapping cannot collapse distinct effort levels")
            object.__setattr__(self, "reasoning_values", MappingProxyType(dict(self.reasoning_values)))
        elif self.reasoning_values is not None or self.reasoning_semantics is not None:
            raise ValueError("reasoning profile fields must be configured together")
        if self.prompt_cache_semantics not in (None, "anthropic_ephemeral_system_end"):
            raise ValueError("unsupported cache contract")
        if self.parameter_aliases is not None:
            if not isinstance(self.parameter_aliases, Mapping) or not self.parameter_aliases:
                raise ValueError("parameter_aliases must be a non-empty mapping of tool names to parameter maps")
            frozen_aliases = {}
            for tool_name, param_map in self.parameter_aliases.items():
                if not isinstance(tool_name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tool_name):
                    raise ValueError("parameter_aliases tool name must be a valid portable function name")
                if not isinstance(param_map, Mapping) or not param_map:
                    raise ValueError("parameter_aliases entry must be a non-empty parameter mapping")
                for src_param, dst_param in param_map.items():
                    if not isinstance(src_param, str) or not src_param or not isinstance(dst_param, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", dst_param) or src_param == dst_param:
                        raise ValueError("parameter_aliases requires distinct non-empty source and portable target parameter names")
                if len(set(param_map.values())) != len(param_map):
                    raise ValueError("parameter_aliases mapping must be injective")
                if set(param_map.keys()) & set(param_map.values()):
                    raise ValueError("parameter_aliases source and target parameter names must be disjoint")
                frozen_aliases[tool_name] = MappingProxyType(dict(sorted(param_map.items())))
            object.__setattr__(self, "parameter_aliases", MappingProxyType(dict(sorted(frozen_aliases.items()))))


@dataclass(frozen=True)
class GenerationProfile:
    """Explicit generation contracts, independent of Chat token-field quirks."""

    base_url: str
    protocol: str
    model: str
    checked_at: str
    evidence: str
    sampling_defaults: Mapping[str, float] | None = None
    responses_penalties: bool = False

    def __post_init__(self):
        object.__setattr__(self, "base_url", _prefix(self.base_url))
        object.__setattr__(self, "protocol", Protocol(self.protocol))
        if not isinstance(self.model, str) or not self.model or any(c.isspace() for c in self.model):
            raise ValueError("profile model must be an exact model ID")
        if not isinstance(self.checked_at, str):
            raise TypeError("profile checked_at must be an ISO date")
        date.fromisoformat(self.checked_at)
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError("profile requires documentation/probe evidence")
        if type(self.responses_penalties) is not bool:
            raise ValueError("responses_penalties must be a boolean")
        if self.responses_penalties and self.protocol != Protocol.RESPONSES:
            raise ValueError("Responses penalty contract requires a Responses upstream")
        if self.sampling_defaults is None and not self.responses_penalties:
            raise ValueError("generation profile requires an explicit contract")
        if self.sampling_defaults is not None:
            from .core import _optional_temperature, _optional_top_p

            if not isinstance(self.sampling_defaults, Mapping) or not self.sampling_defaults or set(self.sampling_defaults) - {"temperature", "top_p"}:
                raise ValueError("sampling defaults require explicit temperature/top_p values")
            normalized = {}
            for key, value in self.sampling_defaults.items():
                checked = (_optional_temperature if key == "temperature" else _optional_top_p)(value)
                if checked is None:
                    raise ValueError("a configured sampling default cannot be unknown")
                if self.protocol == Protocol.MESSAGES and key == "temperature" and checked > 1:
                    raise ValueError("Messages temperature must be between 0 and 1")
                normalized[key] = checked
            object.__setattr__(self, "sampling_defaults", MappingProxyType(normalized))


class ProviderProfiles:
    def __init__(self, records=()):
        self._records = {}
        for record in records:
            if not isinstance(record, ProviderProfile | GenerationProfile):
                raise TypeError("provider profiles require typed records")
            key = record.base_url, record.protocol, record.model
            if key in self._records:
                raise ValueError("duplicate provider profile")
            self._records[key] = record

    def fingerprint(self):
        def _plain(val):
            if isinstance(val, Mapping):
                return {k: _plain(v) for k, v in val.items()}
            return val

        values = []
        for key in sorted(self._records):
            record = self._records[key]
            value = {field.name: _plain(getattr(record, field.name)) for field in fields(record)}
            if isinstance(record, GenerationProfile):
                value["kind"] = "generation"
            values.append(value)
        return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def supports_prompt_cache(self, base_url, protocol, model):
        record = self._records.get((_prefix(base_url), Protocol(protocol), model))
        return record is not None and getattr(record, "prompt_cache_semantics", None) == "anthropic_ephemeral_system_end"

    def supports_responses_penalties(self, base_url, protocol, model):
        if not isinstance(model, str):
            return False
        record = self._records.get((_prefix(base_url), Protocol(protocol), model))
        return isinstance(record, GenerationProfile) and record.responses_penalties

    @classmethod
    def load(cls, path):
        with Path(path).open("rb") as source:
            raw = source.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("provider profiles exceed 2 MiB")
        body = json.loads(raw)
        if not isinstance(body, dict) or set(body) != {"schema_version", "profiles"} or type(body["schema_version"]) is not int or body["schema_version"] != 1 or not isinstance(body["profiles"], list):
            raise ValueError("invalid provider profile document")
        records = []
        for item in body["profiles"]:
            if not isinstance(item, dict):
                raise TypeError("profile must be an object")
            if "kind" not in item:
                records.append(ProviderProfile(**item))
            else:
                item = dict(item)
                if item.pop("kind") != "generation":
                    raise ValueError("unknown provider profile kind")
                records.append(GenerationProfile(**item))
        return cls(tuple(records))

    def apply(self, base_url, protocol, body):
        model = body.get("model")
        if not isinstance(model, str):
            raise ConversionError("provider profile requires an exact model ID")
        record = self._records.get((_prefix(base_url), Protocol(protocol), model))
        if record is None:
            return body
        result = copy.deepcopy(body)
        for key, value in (record.sampling_defaults or {}).items():
            if result.get(key) is None:
                result[key] = value
        if isinstance(record, GenerationProfile):
            return result
        if "max_tokens" in result and "max_completion_tokens" in result:
            raise ConversionError("ambiguous Chat output-token fields")
        source = "max_completion_tokens" if record.chat_token_field == "max_tokens" else "max_tokens"
        if source in result:
            value = result.pop(source)
            if type(value) is not int or value <= 0:
                raise ConversionError("profile output token limit must be a positive integer")
            result[record.chat_token_field] = value
        if record.reasoning_field is not None and "reasoning_effort" in result:
            effort = result.pop("reasoning_effort")
            if not isinstance(effort, str) or effort not in record.reasoning_values:
                raise ConversionError("profile does not certify the requested reasoning effort")
            target = result
            path = record.reasoning_field.split(".")
            for key in path[:-1]:
                if key in target and not isinstance(target[key], dict):
                    raise ConversionError("conflicting vendor reasoning field")
                target = target.setdefault(key, {})
            if path[-1] in target:
                raise ConversionError("conflicting vendor reasoning field")
            target[path[-1]] = record.reasoning_values[effort]
        if record.parameter_aliases is not None:
            _apply_parameter_aliases_request(result, record.parameter_aliases)
        return result

    def restore(self, base_url, protocol, model, body):
        if not isinstance(model, str):
            return body
        record = self._records.get((_prefix(base_url), Protocol(protocol), model))
        if record is None or getattr(record, "parameter_aliases", None) is None:
            return body
        result = copy.deepcopy(body)
        _restore_parameter_aliases_response(result, record.parameter_aliases)
        return result

    def stream_delta_restorer(self, base_url, protocol, model):
        if not isinstance(model, str):
            return None
        record = self._records.get((_prefix(base_url), Protocol(protocol), model))
        if record is None or getattr(record, "parameter_aliases", None) is None:
            return None
        reverse_aliases = {tool: {dst: src for src, dst in param_map.items()} for tool, param_map in record.parameter_aliases.items()}
        return _StreamParameterAliasRestorer(reverse_aliases).feed


class _StreamParameterAliasRestorer:
    def __init__(self, reverse_aliases: Mapping[str, Mapping[str, str]]) -> None:
        self.reverse_aliases = reverse_aliases
        self._active: dict[int, tuple[str, list[str]]] = {}

    def feed(self, delta):
        from dataclasses import replace

        from .streaming import Delta, StreamError

        if delta.kind == "tool_start" and delta.tool_type == "function" and delta.name in self.reverse_aliases:
            self._active[delta.index] = (delta.name, [])
            return [delta]
        if delta.kind == "tool_delta" and delta.index in self._active:
            self._active[delta.index][1].append(delta.text)
            return []
        if delta.kind == "tool_end" and delta.index in self._active:
            name, chunks = self._active.pop(delta.index)
            raw_args = "".join(chunks)
            try:
                remapped = _remap_json_arguments(raw_args, self.reverse_aliases[name], direction="stream")
            except ConversionError as exc:
                raise StreamError(str(exc)) from exc
            return [Delta("tool_delta", index=delta.index, text=remapped), replace(delta)]
        return [delta]


def _remap_json_arguments(raw_args, mapping: Mapping[str, str], *, direction: str) -> str:
    if not isinstance(raw_args, str):
        raise ConversionError("tool call arguments must be a JSON string")
    try:
        parsed = json.loads(raw_args)
    except ValueError as exc:
        raise ConversionError(f"invalid JSON in {direction} tool call arguments") from exc
    if not isinstance(parsed, dict):
        raise ConversionError("tool call arguments must decode to a JSON object")
    out = {}
    for key, val in parsed.items():
        if key in mapping:
            target_key = mapping[key]
            if target_key in parsed or target_key in out:
                raise ConversionError(f"conflicting parameter alias {target_key!r} in {direction} tool call arguments")
            out[target_key] = val
        elif key in set(mapping.values()):
            raise ConversionError(f"collision with reserved parameter alias {key!r} in {direction} tool call arguments")
        else:
            out[key] = val
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


def _apply_parameter_aliases_request(body: dict, aliases: Mapping[str, Mapping[str, str]]) -> None:
    for tool in body.get("tools", []) if isinstance(body.get("tools"), list) else []:
        if not isinstance(tool, dict) or tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
            continue
        fn = tool["function"]
        name = fn.get("name")
        if name not in aliases:
            continue
        param_map = aliases[name]
        params = fn.get("parameters")
        if isinstance(params, dict):
            props = params.get("properties")
            if isinstance(props, dict):
                new_props = {}
                for k, v in props.items():
                    if k in param_map:
                        dst = param_map[k]
                        if dst in props or dst in new_props:
                            raise ConversionError(f"conflicting parameter alias {dst!r} in tool schema {name!r}")
                        new_props[dst] = v
                    elif k in set(param_map.values()):
                        raise ConversionError(f"collision with reserved parameter alias {k!r} in tool schema {name!r}")
                    else:
                        new_props[k] = v
                params["properties"] = new_props
            req = params.get("required")
            if isinstance(req, list):
                new_req = []
                for item in req:
                    if item in param_map:
                        new_req.append(param_map[item])
                    elif item in set(param_map.values()):
                        raise ConversionError(f"collision with reserved parameter alias {item!r} in required list for {name!r}")
                    else:
                        new_req.append(item)
                if len(set(new_req)) != len(new_req):
                    raise ConversionError(f"duplicate required parameter after aliasing in {name!r}")
                params["required"] = new_req
    for msg in body.get("messages", []) if isinstance(body.get("messages"), list) else []:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls", []) if isinstance(msg.get("tool_calls"), list) else []:
            if not isinstance(call, dict) or call.get("type") != "function" or not isinstance(call.get("function"), dict):
                continue
            fn = call["function"]
            name = fn.get("name")
            if name in aliases and "arguments" in fn:
                fn["arguments"] = _remap_json_arguments(fn["arguments"], aliases[name], direction="request")


def _restore_parameter_aliases_response(body: dict, aliases: Mapping[str, Mapping[str, str]]) -> None:
    reverse_aliases = {tool: {dst: src for src, dst in param_map.items()} for tool, param_map in aliases.items()}
    for choice in body.get("choices", []) if isinstance(body.get("choices"), list) else []:
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            continue
        msg = choice["message"]
        for call in msg.get("tool_calls", []) if isinstance(msg.get("tool_calls"), list) else []:
            if not isinstance(call, dict) or call.get("type") != "function" or not isinstance(call.get("function"), dict):
                continue
            fn = call["function"]
            name = fn.get("name")
            if name in reverse_aliases and "arguments" in fn:
                fn["arguments"] = _remap_json_arguments(fn["arguments"], reverse_aliases[name], direction="response")
