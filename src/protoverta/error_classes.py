"""Provider error classes and the retry policy built on them.

One classifier is shared by the gateway (``GatewayConfig.retry_policy``) and by
the launchers' direct-mode relay, so both decide the same way. Classification
uses the HTTP status, the structured ``error.type``/``error.code`` fields and a
small set of message patterns seen from real providers. It never changes what
the client receives; it only decides whether the same request is sent again.
"""

from __future__ import annotations

import json
import math
import random
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

ErrorClass = Literal[
    "rate_limit",
    "overloaded",
    "server",
    "network",
    "stream_interrupted",
    "backend_mismatch",
    "auth",
    "billing",
    "model_not_found",
    "context_length",
    "content_policy",
    "invalid_request",
    "unknown",
]

ERROR_CLASSES: tuple[ErrorClass, ...] = (
    "rate_limit",
    "overloaded",
    "server",
    "network",
    "stream_interrupted",
    "backend_mismatch",
    "auth",
    "billing",
    "model_not_found",
    "context_length",
    "content_policy",
    "invalid_request",
    "unknown",
)

# Transient failures, plus errors whose cause is not known: the original
# request ("many other red errors should be retried") is kept for those.
DEFAULT_RETRY_CLASSES: frozenset[ErrorClass] = frozenset({"rate_limit", "overloaded", "server", "network", "stream_interrupted", "backend_mismatch", "unknown"})

_TYPE_CLASSES: dict[str, ErrorClass] = {
    "rate_limit_error": "rate_limit",
    "rate_limit_exceeded": "rate_limit",
    "overloaded_error": "overloaded",
    "server_error": "server",
    "api_error": "server",
    "authentication_error": "auth",
    "permission_error": "auth",
    "invalid_api_key": "auth",
    "billing_error": "billing",
    "insufficient_quota": "billing",
    "model_not_found": "model_not_found",
    "not_found_error": "model_not_found",
    "context_length_exceeded": "context_length",
    "context_window_exceeded": "context_length",
    "request_too_large": "context_length",
    "content_filter": "content_policy",
    "content_policy_violation": "content_policy",
    "policy_refusal": "content_policy",
}

_CONTENT_POLICY = re.compile(r"content (filter|policy|management)|safety system|flagged")

# Ordered: the first matching pattern wins. Patterns are lower-case.
_MESSAGE_RULES: tuple[tuple[str, ErrorClass], ...] = (
    (r"different azure openai resource|created under a different|item with id .* not found", "backend_mismatch"),
    (r"unauthori[sz]ed client|invalid api key|incorrect api key|invalid x-api-key|api key (is )?(invalid|expired|revoked)", "auth"),
    (r"insufficient (credits|quota|balance|funds)|payment required|out of credits|exceeded your current quota|billing", "billing"),
    (r"context (length|window)|maximum context|too many tokens|prompt is too long|input is too long|max_tokens .* exceed", "context_length"),
    (r"model .*(not found|does not exist|not available|is not supported)|not a valid model|unknown model|no such model|invalid model", "model_not_found"),
    (r"无可用渠道|no available (channel|provider|endpoint)|temporarily unavailable|overloaded|capacity|try again later", "overloaded"),
    (r"rate.?limit|too many requests", "rate_limit"),
    (_CONTENT_POLICY.pattern, "content_policy"),
    (
        r"\bis required\b|extra inputs|unsupported (parameter|value|field)|invalid (type|value|request|json)|must be (a|an|one of)|unexpected value",
        "invalid_request",
    ),
)
_MESSAGE_CLASSES: tuple[tuple[re.Pattern[str], ErrorClass], ...] = tuple((re.compile(pattern), kind) for pattern, kind in _MESSAGE_RULES)

_STATUS_CLASSES: dict[int, ErrorClass] = {
    401: "auth",
    403: "auth",
    402: "billing",
    404: "model_not_found",
    408: "network",
    409: "server",
    413: "context_length",
    429: "rate_limit",
    500: "server",
    502: "server",
    503: "overloaded",
    504: "server",
    529: "overloaded",
}


def error_detail(body: object) -> tuple[str, str]:
    """Return ``(type_or_code, message)`` from any common provider error shape."""
    payload: object = body
    if isinstance(body, bytes | bytearray):
        body = bytes(body[:65536]).decode("utf-8", "replace")
    if isinstance(body, str):
        try:
            payload = json.loads(body)  # Error bodies and SSE event data are usually JSON.
        except ValueError:
            return "", body[:2000]
    if isinstance(payload, str):
        return "", payload[:2000]
    if not isinstance(payload, dict):
        return "", ""
    error = payload.get("error")
    if isinstance(payload.get("response"), dict) and isinstance(payload["response"].get("error"), dict):
        error = payload["response"]["error"]  # Responses ``response.failed``.
    if isinstance(error, str):
        return "", error[:2000]
    if not isinstance(error, dict):
        error = payload
    kind = next((value for key in ("code", "type") if isinstance(value := error.get(key), str) and value), "")
    message = error.get("message")
    return kind.lower(), message[:2000] if isinstance(message, str) else ""


def classify(status: int | None, body: object = None, *, transport: bool = False, stream_before_output: bool = False) -> ErrorClass:
    """Classify one failed attempt.

    ``transport`` marks a connection failure (no HTTP answer); ``stream_before_output``
    marks a stream that failed or ended before producing any output.
    """
    if transport:
        return "network"
    kind, message = error_detail(body)
    text = message.lower()
    # Statuses whose meaning is unambiguous win over message wording: a 429
    # "too many tokens per minute" is a rate limit, not a context overflow.
    if status in (401, 403):
        return "content_policy" if _CONTENT_POLICY.search(text) else "auth"
    if status in (402, 429, 529):
        return _STATUS_CLASSES[status]
    for pattern, error_class in _MESSAGE_CLASSES:
        if pattern.search(text):
            return error_class
    if kind in _TYPE_CLASSES:
        mapped = _TYPE_CLASSES[kind]
        # ``api_error``/``server_error`` say little on a 4xx; trust the status.
        if not (mapped == "server" and status is not None and 400 <= status < 500):
            return mapped
    if status is not None and status in _STATUS_CLASSES:
        return _STATUS_CLASSES[status]
    if stream_before_output:
        return "stream_interrupted"
    if status is not None and 500 <= status < 600:
        return "server"
    if status in (400, 422) and kind in ("invalid_request_error", "invalid_request"):
        # A structured "invalid request" whose wording matched no transient
        # pattern above (such as a backend mismatch) is the request itself.
        return "invalid_request"
    return "unknown"


def _bounded_int(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")
    return value


def _bounded_number(value: object, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a number from {low} to {high}")
    return float(value)


def _classes(values: Iterable[object], name: str) -> frozenset[ErrorClass]:
    result: set[ErrorClass] = set()
    for value in values:
        if value not in ERROR_CLASSES:
            raise ValueError(f"{name} contains an unknown error class: {value!r}")
        result.add(value)
    return frozenset(result)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How long to keep resending one request whose provider attempt failed.

    Attempts wait ``delay_seconds`` (± ``jitter`` as a fraction, at least the
    provider's ``Retry-After``) and stop after ``max_retries`` retries or
    ``max_total_seconds`` of waiting. ``skip_status`` keeps older configs
    working: those statuses are never retried whatever their class.
    """

    enabled: bool = True
    max_retries: int = 10
    delay_seconds: float = 60.0
    jitter: float = 0.2
    max_total_seconds: float = 1800.0
    retry_classes: frozenset[ErrorClass] = DEFAULT_RETRY_CLASSES
    skip_status: frozenset[int] = frozenset()
    give_up_window_seconds: float = 30.0
    random: Callable[[], float] = field(default=random.random, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("retry enabled must be a boolean")
        _bounded_int(self.max_retries, "max_retries", 0, 100)
        _bounded_number(self.delay_seconds, "delay_seconds", 0, 3600)
        _bounded_number(self.jitter, "jitter", 0, 1)
        _bounded_number(self.max_total_seconds, "max_total_seconds", 0, 86400)
        _bounded_number(self.give_up_window_seconds, "give_up_window_seconds", 0, 3600)
        object.__setattr__(self, "retry_classes", _classes(self.retry_classes, "retry_classes"))
        object.__setattr__(self, "skip_status", frozenset(_bounded_int(status, "skip_status", 400, 599) for status in self.skip_status))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> RetryPolicy:
        """Build from a ``[retry]`` config table; unknown keys are rejected."""
        allowed = {"enabled", "max_retries", "delay_seconds", "jitter", "max_total_seconds", "retry_classes", "no_retry_classes", "skip_status"}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"[retry] has unknown settings: {', '.join(sorted(unknown))}")
        classes = set(DEFAULT_RETRY_CLASSES)
        for key, add in (("retry_classes", True), ("no_retry_classes", False)):
            values = raw.get(key, [])
            if not isinstance(values, list):
                raise ValueError(f"{key} must be a list of error classes")  # noqa: TRY004 - config errors are ValueError
            chosen = _classes(values, key)
            classes = classes | chosen if add else classes - chosen
        statuses = raw.get("skip_status", [])
        if not isinstance(statuses, list):
            raise ValueError("skip_status must be a list of HTTP status codes")  # noqa: TRY004 - config errors are ValueError
        return cls(
            enabled=raw.get("enabled", True),  # type: ignore[arg-type]
            max_retries=raw.get("max_retries", 10),  # type: ignore[arg-type]
            delay_seconds=raw.get("delay_seconds", 60),  # type: ignore[arg-type]
            jitter=raw.get("jitter", 0.2),  # type: ignore[arg-type]
            max_total_seconds=raw.get("max_total_seconds", 1800),  # type: ignore[arg-type]
            retry_classes=frozenset(classes),
            skip_status=frozenset(statuses),
        )

    def retryable(self, error_class: ErrorClass, status: int | None = None, headers: Mapping[str, str] | None = None) -> bool:
        if not self.enabled or self.max_retries == 0 or (status is not None and status in self.skip_status):
            return False
        hint = (headers.get("x-should-retry") or "").strip().lower() if headers is not None else ""
        if hint == "false":
            return False
        return hint == "true" or error_class in self.retry_classes

    def delay(self, retry_after: float | None = None) -> float:
        base = self.delay_seconds * (1 + self.jitter * (2 * self.random() - 1))
        return max(0.0, base, retry_after or 0.0)


def describe(error_class: ErrorClass, status: int | None, body: object) -> str:
    """One bounded log line: class, status and the provider's message."""
    _, message = error_detail(body)
    label = f"HTTP {status}" if status is not None else "no HTTP answer"
    return f"{error_class} ({label}){': ' + ' '.join(message.split())[:200] if message else ''}"
