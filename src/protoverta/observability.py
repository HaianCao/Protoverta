"""Opt-in, payload-free request outcomes. Never retain provider/client content."""

from __future__ import annotations

import hmac
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Literal, TextIO

WireProtocol = Literal["chat", "messages", "responses", "models", "unknown"]
Outcome = Literal[
    "success",
    "truncated_output",
    "invalid_request",
    "authentication",
    "not_found",
    "capacity",
    "shared_admission",
    "admission_unavailable",
    "upstream_http",
    "timeout",
    "upstream_transport",
    "unsupported_reasoning",
    "unsupported_feature",
    "policy_refusal",
    "invalid_stream",
    "invalid_response",
    "stream_limit",
    "client_disconnected",
    "internal_error",
]
TracePhase = Literal["request", "preflight", "admission", "upstream", "response", "stream"]


@dataclass(frozen=True, slots=True)
class RequestObservation:
    """Fixed fields only; HTTP status is distinct from post-header SSE failure."""

    request_id: str
    protocol: WireProtocol
    upstream_protocol: WireProtocol
    status: int | None
    outcome: Outcome
    duration_ms: int
    streaming: bool
    first_payload_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    requested_model_digest: str | None = None
    effective_model_digest: str | None = None
    phase: TracePhase | None = None
    feature: str | None = None
    agent_digest: str | None = None
    chain_digest: str | None = None


Observer = Callable[[RequestObservation], None]


class RequestTrace:
    """One request's mutable accumulator, never shared between handler threads."""

    def __init__(
        self, protocol: WireProtocol, upstream: WireProtocol, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        # Never trust/echo client or upstream IDs as the local correlation ID.
        self.request_id = secrets.token_hex(16)
        self.protocol = protocol
        self.upstream_protocol = upstream
        self.status: int | None = None
        self.outcome: Outcome = "internal_error"
        self.streaming = False
        self._clock = clock
        self._started = clock()
        self.first_payload_ms: int | None = None
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.cached_input_tokens: int | None = None
        self.cache_creation_input_tokens: int | None = None
        self.requested_model_digest: str | None = None
        self.effective_model_digest: str | None = None
        self.phase: TracePhase | None = None
        self.feature: str | None = None
        self.agent_digest: str | None = None
        self.chain_digest: str | None = None

    def models(self, requested: object, effective: object, salt: bytes) -> None:
        """Session-scoped identity only; never store model names or a global hash."""

        def digest(value: object) -> str | None:
            if not isinstance(value, str) or not 1 <= len(value) <= 512:
                return None
            try:
                raw = value.encode("utf-8")
            except UnicodeError:
                return None
            return hmac.digest(salt, raw, "sha256").hex()

        self.requested_model_digest = digest(requested)
        self.effective_model_digest = digest(effective)

    def correlation(self, agent: object, chain: object, salt: bytes) -> None:
        """Bounded HMAC-digested correlation only; never used for auth or routing."""

        def digest(value: object) -> str | None:
            if not isinstance(value, str) or not 1 <= len(value) <= 128:
                return None
            if any(ord(c) < 33 or ord(c) >= 127 for c in value):
                return None
            return hmac.digest(salt, value.encode("ascii"), "sha256").hex()

        self.agent_digest = digest(agent)
        self.chain_digest = digest(chain)

    def mark_first_payload(self) -> None:
        if self.first_payload_ms is None:
            self.first_payload_ms = max(0, int((self._clock() - self._started) * 1000))

    def usage(
        self, inputs: int | None, outputs: int | None, cached: int | None = None, created: int | None = None
    ) -> None:
        if type(inputs) is int and inputs >= 0:
            self.input_tokens = inputs
        if type(outputs) is int and outputs >= 0:
            self.output_tokens = outputs
        if type(cached) is int and cached >= 0:
            self.cached_input_tokens = cached
        if type(created) is int and created >= 0:
            self.cache_creation_input_tokens = created

    def snapshot(self) -> RequestObservation:
        return RequestObservation(
            self.request_id,
            self.protocol,
            self.upstream_protocol,
            self.status,
            self.outcome,
            max(0, int((self._clock() - self._started) * 1000)),
            self.streaming,
            self.first_payload_ms,
            self.input_tokens,
            self.output_tokens,
            self.cached_input_tokens,
            self.cache_creation_input_tokens,
            self.requested_model_digest,
            self.effective_model_digest,
            self.phase,
            self.feature,
            self.agent_digest,
            self.chain_digest,
        )

    def emit(self, observer: Observer | None) -> None:
        if observer is not None:
            try:
                observer(self.snapshot())
            except Exception:  # noqa: BLE001 -- untrusted optional sink must not alter a response
                # Diagnostics are non-authoritative: no exception text, retries,
                # response changes or success replacement if a sink fails.
                return


def jsonl_observer(stream: TextIO) -> Observer:
    """Write fixed-size records to a caller-owned sink; no file is opened here.

    The server invokes this synchronously. Custom sinks must be thread-safe,
    bounded and nonblocking; a slow sink can delay handler cleanup. The built-in
    sink serializes writes but provides no durable delivery or metrics service.
    """
    import threading

    lock = threading.Lock()

    def write(record: RequestObservation) -> None:
        line = json.dumps(asdict(record), separators=(",", ":")) + "\n"
        with lock:
            stream.write(line)
            stream.flush()

    return write
