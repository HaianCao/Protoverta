"""Opt-in context preflight; estimates are never labelled provider-exact counts."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass

from .core import Protocol
from .errors import ConversionError


@dataclass(frozen=True, slots=True)
class TokenCount:
    tokens: int
    exact: bool = False

    def __post_init__(self):
        if type(self.tokens) is not int or self.tokens < 0 or type(self.exact) is not bool:
            raise ValueError("token count must be nonnegative with an explicit exactness flag")


@dataclass(frozen=True, slots=True)
class ContextAssessment:
    input_tokens: int | None
    reserved_output_tokens: int | None
    context_window_tokens: int | None
    exact: bool
    verdict: str
    count_kind: str = "utf8-json-byte-estimate"

    def headers(self):
        kind = "unavailable" if self.input_tokens is None else "operator-exact" if self.exact else self.count_kind
        category = "unavailable" if self.input_tokens is None else "exact" if self.exact else "estimate"
        values = {"X-Protoverta-Context-Verdict": self.verdict,
                  "X-Protoverta-Context-Count-Kind": kind,
                  "X-Protoverta-Context-Count-Category": category}
        for name, value in (("Input", self.input_tokens), ("Output-Reserve", self.reserved_output_tokens), ("Limit", self.context_window_tokens)):
            if value is not None:
                values["X-Protoverta-Context-" + name] = str(value)
        return values


def _has_media(body):
    # Inspect input content, never tool schemas (which may have fields named image).
    pending = [body.get(key) for key in ("input", "messages", "system")]
    while pending:
        item = pending.pop()
        if isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            if any(item.get(key) is not None for key in ("audio", "video", "file", "document")):
                return True
            kind = item.get("type", "")
            if isinstance(kind, str) and any(word in kind for word in ("image", "audio", "video", "file", "document")):
                return True
            pending.extend(item.get(key) for key in ("content", "output"))
    return False


def assess_context(body, protocol, limit, *, policy="estimate", counter: Callable | None = None):
    """Byte-count heuristic is a labelled proxy, NOT a guaranteed upper bound.

    Exact operator callbacks must cover the complete model-specific input,
    including tools/system/history/multimodal overhead. They are trusted code;
    this library never calls a count endpoint or downloads a tokenizer by default.
    """
    protocol = Protocol(protocol)
    if policy not in ("estimate", "reject_estimate", "exact"):
        raise ValueError("unknown context preflight policy")
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("context limit must be positive")
    output = body.get("max_completion_tokens", body.get("max_tokens")) if protocol == Protocol.CHAT else body.get("max_output_tokens" if protocol == Protocol.RESPONSES else "max_tokens")
    if output is not None and (type(output) is not int or output <= 0):
        raise ConversionError("context preflight requires a positive explicit output reserve")
    count = None
    if counter is not None:
        try:
            count = counter(copy.deepcopy(body), protocol)
        except Exception:  # noqa: BLE001 -- never echo trusted callback exception contents
            raise ConversionError("operator token counter failed") from None
        if not isinstance(count, TokenCount):
            raise ConversionError("operator token counter must return TokenCount")
    elif not _has_media(body):
        # Retain every field; do not rewrite/truncate history to fit a budget.
        count = TokenCount(len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")))
    if policy == "exact" and (count is None or not count.exact):
        raise ConversionError("exact context preflight requires a verified operator counter")
    exact = count is not None and count.exact
    verdict = "unknown"
    if count is not None and output is not None and limit is not None:
        over = count.tokens + output > limit
        verdict = ("over" if over else "within") + ("-exact" if exact else "-estimate")
    result = ContextAssessment(count.tokens if count else None, output, limit, exact, verdict, "operator-estimate" if counter else "utf8-json-byte-estimate")
    if policy in ("reject_estimate", "exact") and (verdict == "unknown" or verdict.startswith("over")):
        raise ConversionError("context preflight rejected: " + verdict + "; choose an explicit smaller request/budget (history was not changed)")
    return result
