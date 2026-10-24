"""Evidence-scoped wire quirks; never strip unsupported request semantics."""

import copy
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

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
        return result
