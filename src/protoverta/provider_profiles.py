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


class ProviderProfiles:
    def __init__(self, records=()):
        self._records = {}
        for record in records:
            if not isinstance(record, ProviderProfile):
                raise TypeError("provider profiles require typed records")
            key = record.base_url, record.protocol, record.model
            if key in self._records:
                raise ValueError("duplicate provider profile")
            self._records[key] = record

    def fingerprint(self):
        values = []
        for key in sorted(self._records):
            record = self._records[key]
            values.append({field.name: dict(getattr(record, field.name)) if isinstance(getattr(record, field.name), Mapping) else getattr(record, field.name) for field in fields(record)})
        return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def supports_prompt_cache(self, base_url, protocol, model):
        record = self._records.get((_prefix(base_url), Protocol(protocol), model))
        return record is not None and record.prompt_cache_semantics == "anthropic_ephemeral_system_end"

    @classmethod
    def load(cls, path):
        with Path(path).open("rb") as source:
            raw = source.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("provider profiles exceed 2 MiB")
        body = json.loads(raw)
        if not isinstance(body, dict) or set(body) != {"schema_version", "profiles"} or type(body["schema_version"]) is not int or body["schema_version"] != 1 or not isinstance(body["profiles"], list):
            raise ValueError("invalid provider profile document")
        return cls(tuple(ProviderProfile(**item) for item in body["profiles"]))

    def apply(self, base_url, protocol, body):
        model = body.get("model")
        if not isinstance(model, str):
            raise ConversionError("provider profile requires an exact model ID")
        record = self._records.get((_prefix(base_url), Protocol(protocol), model))
        if record is None:
            return body
        result = copy.deepcopy(body)
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
        return result
