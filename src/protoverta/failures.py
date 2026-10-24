"""Protocol-neutral error classification and target-specific wire envelopes.

No retries, fallback, raw exception logging or provider-message heuristics live
here. Native upstream error bodies are deliberately handled outside this module.
"""

from __future__ import annotations

from dataclasses import dataclass

_CONTEXT_CODES = frozenset({"context_length_exceeded", "context_window_exceeded", "max_context_length_exceeded"})


@dataclass(frozen=True, slots=True)
class GatewayFailure:
    status: int
    kind: str
    message: str
    code: str

    def payload(self, target: str) -> dict:
        message_types = {"invalid_request": "invalid_request_error", "context_window": "invalid_request_error", "authentication": "authentication_error", "permission": "permission_error", "not_found": "not_found_error", "too_large": "request_too_large", "rate_limit": "rate_limit_error", "overloaded": "overloaded_error", "timeout": "api_error", "upstream": "api_error"}
        openai_types = {**message_types, "too_large": "invalid_request_error", "overloaded": "server_error", "timeout": "server_error", "upstream": "server_error"}
        detail = {"type": (message_types if target == "messages" else openai_types)[self.kind], "message": self.message, "code": self.code}
        return {"type": "error", "error": detail} if target == "messages" else {"error": detail}

    def stream_payload(self, target: str, *, sequence_number: int = 0) -> dict:
        if target == "responses":
            return {"type": "error", "code": self.code, "message": self.message, "param": None, "sequence_number": sequence_number}
        return self.payload(target)


def gateway_failure(status: int, message: str, *, code: str | None = None, secrets: tuple[str, ...] = ()) -> GatewayFailure:
    """Trust only numeric status and recognized structured codes for semantics."""
    status = status if type(status) is int and 400 <= status < 600 else 502
    kind = {400: "invalid_request", 401: "authentication", 403: "permission", 404: "not_found", 408: "invalid_request", 411: "invalid_request", 413: "too_large", 422: "invalid_request", 429: "rate_limit", 503: "overloaded", 504: "timeout", 529: "overloaded"}.get(status, "upstream")
    if status in {400, 413, 422} and code in _CONTEXT_CODES:
        kind, code = "context_window", "context_length_exceeded"
    elif code not in {"unsupported_reasoning", "invalid_stream", "upstream_transport", "upstream_timeout"}:
        code = kind
    # Known secrets are removed before bounding the message, including when a
    # provider embeds credentials in an otherwise valid JSON error response.
    for secret in sorted((value for value in secrets if value), key=len, reverse=True):
        message = message.replace(secret, "[REDACTED]")
    message = "".join(char for char in message if ord(char) >= 32 or char in "\n\t")[:2000]
    return GatewayFailure(status, kind, message, code)
