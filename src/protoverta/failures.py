"""Protocol-neutral error classification and target-specific wire envelopes.

No retries, fallback, raw exception logging or provider-message heuristics live
here. Native upstream error bodies are deliberately handled outside this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FailureKind = Literal[
    "invalid_request", "context_window", "authentication", "permission", "not_found", "too_large", "rate_limit", "overloaded", "timeout", "upstream"
]
FailurePhase = Literal["request", "preflight", "admission", "upstream", "response", "stream"]

_CONTEXT_CODES = frozenset({"context_length_exceeded", "context_window_exceeded", "max_context_length_exceeded"})
_POLICY_REFUSAL_FEATURES = frozenset({"refusal", "content_filter"})
_RECOGNIZED_CODES = frozenset(
    {
        "tool_policy_violation",
        "unsupported_feature",
        "unsupported_reasoning",
        "policy_refusal",
        "truncated_output",
        "invalid_stream",
        "invalid_response",
        "upstream_transport",
        "upstream_timeout",
        "request_deadline",
        "progress_timeout",
        "stream_limit",
    }
)
_RECOGNIZED_PHASES = frozenset({"request", "preflight", "admission", "upstream", "response", "stream"})
_RECOGNIZED_FEATURES = frozenset(
    {
        "tool_policy",
        "refusal",
        "content_filter",
        "stop_sequence",
        "pause_turn",
        "stream_usage_policy",
        "codex_closed_resume",
        "compaction",
        "reasoning",
    }
)


@dataclass(frozen=True, slots=True)
class GatewayFailure:
    status: int
    kind: FailureKind
    message: str
    code: str
    phase: FailurePhase | None = None
    feature: str | None = None
    path: str | None = None

    def payload(self, target: str) -> dict[str, object]:
        message_types = {
            "invalid_request": "invalid_request_error",
            "context_window": "invalid_request_error",
            "authentication": "authentication_error",
            "permission": "permission_error",
            "not_found": "not_found_error",
            "too_large": "request_too_large",
            "rate_limit": "rate_limit_error",
            "overloaded": "overloaded_error",
            "timeout": "api_error",
            "upstream": "api_error",
        }
        openai_types = {
            **message_types,
            "too_large": "invalid_request_error",
            "overloaded": "server_error",
            "timeout": "server_error",
            "upstream": "server_error",
        }
        detail: dict[str, object] = {"type": (message_types if target == "messages" else openai_types)[self.kind], "message": self.message, "code": self.code}
        if self.phase is not None:
            detail["phase"] = self.phase
        if self.feature is not None:
            detail["feature"] = self.feature
        if self.path is not None:
            detail["path"] = self.path
        return {"type": "error", "error": detail} if target == "messages" else {"error": detail}

    def stream_payload(self, target: str, *, sequence_number: int = 0) -> dict[str, object]:
        if target == "responses":
            # ``param`` names a request parameter in the Responses contract. The
            # gateway path is an HTTP route, so it is reported separately and
            # never presented as if a request field were invalid.
            event: dict[str, object] = {"type": "error", "code": self.code, "message": self.message, "param": None, "sequence_number": sequence_number}
            if self.phase is not None:
                event["phase"] = self.phase
            if self.feature is not None:
                event["feature"] = self.feature
            if self.path is not None:
                event["path"] = self.path
            return event
        return self.payload(target)


def gateway_failure(
    status: int,
    message: str,
    *,
    code: str | None = None,
    phase: FailurePhase | None = None,
    feature: str | None = None,
    path: str | None = None,
    secrets: tuple[str, ...] = (),
) -> GatewayFailure:
    """Trust only numeric status and recognized structured codes for semantics."""
    status = status if type(status) is int and 400 <= status < 600 else 502
    status_kinds: dict[int, FailureKind] = {
        400: "invalid_request",
        401: "authentication",
        403: "permission",
        404: "not_found",
        408: "invalid_request",
        411: "invalid_request",
        413: "too_large",
        422: "invalid_request",
        429: "rate_limit",
        503: "overloaded",
        504: "timeout",
        529: "overloaded",
    }
    kind = status_kinds.get(status, "upstream")
    valid_feature = feature if isinstance(feature, str) and feature in _RECOGNIZED_FEATURES else None
    if valid_feature in _POLICY_REFUSAL_FEATURES and code in {None, "unsupported_feature", "policy_refusal"}:
        code = "policy_refusal"
    if status in {400, 413, 422} and code in _CONTEXT_CODES:
        kind, code = "context_window", "context_length_exceeded"
    elif code not in _RECOGNIZED_CODES:
        code = kind
    valid_phase: FailurePhase | None = phase if isinstance(phase, str) and phase in _RECOGNIZED_PHASES else None
    valid_path = path if isinstance(path, str) and 1 <= len(path) <= 128 and all(32 <= ord(c) < 127 for c in path) else None
    # Known secrets are removed before bounding the message, including when a
    # provider embeds credentials in an otherwise valid JSON error response.
    for secret in sorted((value for value in secrets if value), key=len, reverse=True):
        message = message.replace(secret, "[REDACTED]")
    message = "".join(char for char in message if ord(char) >= 32 or char in "\n\t")[:2000]
    return GatewayFailure(status, kind, message, code or kind, valid_phase, valid_feature, valid_path)
