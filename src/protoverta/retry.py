"""Bounded pre-response retries; never replay a partially delivered stream."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import math
import time
from collections.abc import Callable
from email.message import Message
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from typing import Any, Protocol, Self, TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .error_classes import ErrorClass, RetryPolicy, classify, describe


class RetryHeaders(Protocol):
    """Anything with a case-insensitive ``get`` for response headers."""

    def get(self, key: str) -> str | None: ...


class RetryBudget(Protocol):
    """The part of a request budget retries use: time left and an interruptible wait."""

    def remaining(self) -> float: ...
    def wait(self, seconds: float) -> None: ...


class Admission(Protocol):
    """The part of a shared admission budget retries use."""

    def acquire(self) -> str: ...
    def release(self, identifier: str | None) -> None: ...
    def cooldown(self, seconds: float) -> None: ...


ResponseT_co = TypeVar("ResponseT_co", covariant=True)


class UpstreamOpener(Protocol[ResponseT_co]):
    """A callable that opens one upstream request with a timeout."""

    def __call__(self, request: Request, *, timeout: float) -> ResponseT_co: ...


def retry_after(headers: RetryHeaders | None, *, now: float | None = None) -> float | None:
    """Seconds to wait from a ``Retry-After`` header (delta seconds or an HTTP date), or ``None``."""
    raw = headers.get("Retry-After") if headers else None
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (ValueError, TypeError):
        try:
            seconds = parsedate_to_datetime(raw).timestamp() - (time.time() if now is None else now)
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def open_with_retry(
    opener: UpstreamOpener[ResponseT_co],
    request: Request,
    budget: RetryBudget,
    *,
    attempts: int = 1,
    max_wait: float = 5.0,
    admission: Admission | None = None,
    sleep: Callable[[float], None] | None = None,
) -> tuple[ResponseT_co, str | None]:
    """Retry only explicit transient HTTP rejection before response/body delivery.

    Transport errors are not retried: whether a generation reached upstream is
    unknown. Each explicit retry consumes a new shared admission attempt.
    """
    for index in range(attempts):
        lease = admission.acquire() if admission else None
        try:
            response = opener(request, timeout=budget.remaining())
            return response, lease
        except HTTPError as exc:
            delay = retry_after(exc.headers)
            should_retry = exc.headers.get("x-should-retry", "").strip().lower() if exc.headers else ""
            if admission:
                try:
                    try:
                        if exc.code in (429, 503) and delay is not None:
                            admission.cooldown(delay)
                    finally:
                        admission.release(lease)
                except BaseException:
                    exc.close()
                    raise
            if index + 1 >= attempts or exc.code not in (429, 502, 503, 504) or should_retry == "false":
                raise
            delay = delay if delay is not None else min(max_wait, 0.25 * 2**index)
            try:
                remaining = budget.remaining()
            except BaseException:
                exc.close()
                raise
            if delay > max_wait or delay >= remaining:
                raise
            exc.close()
            (sleep or budget.wait)(delay)
        except BaseException:
            if admission:
                admission.release(lease)
            raise
    raise AssertionError("unreachable retry state")


# --- Error-class retry policy (``GatewayConfig.retry_policy``) -------------

_PENDING_EVENTS = {
    "responses": frozenset({"response.created", "response.in_progress", "response.queued"}),
    "messages": frozenset({"message_start", "ping"}),
}
_FAILED_EVENTS = {"responses": frozenset({"error", "response.failed"}), "messages": frozenset({"error"})}
_CHAT_OUTPUT_FIELDS = ("content", "tool_calls", "function_call", "refusal", "reasoning_content", "reasoning")
_ERROR_BODY_BYTES = 65536


class RetryState:
    """Per-gateway memory of the last give-up.

    A CLI may resend a request right after the gateway gave up on it (its own
    reconnect, or Claude Code resending a 400 with fewer features). For a short
    window those follow-ups are answered at once instead of running another
    full retry cycle each.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.gave_up_at: float | None = None

    def may_retry(self, window: float) -> bool:
        gave_up = self.gave_up_at
        return gave_up is None or self.clock() - gave_up > window

    def give_up(self) -> None:
        self.gave_up_at = self.clock()

    def succeeded(self) -> None:
        self.gave_up_at = None


class PrefixedResponse:
    """An upstream response whose first bytes were read to decide on a retry."""

    def __init__(self, response: Any, prefix: bytes) -> None:
        self._response = response
        self._prefix = bytearray(prefix)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)

    def read1(self, size: int = -1) -> bytes:
        if self._prefix:
            count = len(self._prefix) if size is None or size < 0 else size
            chunk = bytes(self._prefix[:count])
            del self._prefix[:count]
            return chunk
        reader = getattr(self._response, "read1", None) or self._response.read
        result: bytes = reader(size)
        return result

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            rest: bytes = self._response.read()
            data = bytes(self._prefix) + rest
            self._prefix.clear()
            return data
        if self._prefix:
            return self.read1(size)
        result: bytes = self._response.read(size)
        return result

    def close(self) -> None:
        self._response.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _stream_verdict(protocol: str, event: Any) -> str:
    """``pending``, ``ok`` (output started), ``failed`` or ``ended`` for one SSE event."""
    if event.data == "[DONE]":
        return "ended"
    try:
        payload = json.loads(event.data)
    except ValueError:
        return "ok"  # Not ours to judge; the stream validator reports it.
    if not isinstance(payload, dict):
        return "ok"
    if protocol == "chat":
        if "error" in payload:
            return "failed"
        choices = payload.get("choices")
        for choice in choices if isinstance(choices, list) else []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if choice.get("finish_reason") or (
                isinstance(delta, dict) and any(delta.get(key) for key in _CHAT_OUTPUT_FIELDS)
            ):
                return "ok"
        return "pending"
    kind = event.event if event.event and event.event != "message" else payload.get("type")
    if kind in _FAILED_EVENTS.get(protocol, frozenset()):
        return "failed"
    return "pending" if kind in _PENDING_EVENTS.get(protocol, frozenset()) else "ok"


def _prefetch(response: Any, protocol: str, budget: Any, limit: int) -> tuple[bytes, str, str | None]:
    """Read a stream until it produces output, fails or ends; keep every byte read."""
    from .sse import SSEParser

    parser = SSEParser()
    prefix = bytearray()
    while len(prefix) <= limit:
        chunk = budget.read(response, 4096, streaming=True)
        if not chunk:
            return bytes(prefix), "ended", None
        prefix.extend(chunk)
        for event in parser.feed(chunk):
            verdict = _stream_verdict(protocol, event)
            if verdict == "failed":
                return bytes(prefix), "failed", event.data
            if verdict in ("ok", "ended"):
                return bytes(prefix), verdict, None
    return bytes(prefix), "ok", None  # Large metadata-only start: the pipeline decides.


def _final_error(exc: HTTPError, body: bytes) -> HTTPError:
    """The last provider error, readable again and marked not retryable."""
    headers: Message = copy.copy(exc.headers) if exc.headers is not None else Message()
    del headers["x-should-retry"]
    headers["x-should-retry"] = "false"
    exc.close()
    return HTTPError(exc.url, exc.code, exc.msg, headers, io.BytesIO(body))


def _is(exc: BaseException, name: str) -> bool:
    return any(kind.__name__ == name for kind in type(exc).__mro__)


def open_with_policy(
    opener: UpstreamOpener[Any],
    request: Request,
    budget: Any,
    policy: RetryPolicy,
    protocol: str,
    *,
    streaming: bool,
    state: RetryState,
    admission: Admission | None = None,
    log: Callable[[str], None] | None = None,
    max_prefetch_bytes: int = 8 * 1024 * 1024,
) -> tuple[Any, str | None]:
    """Send ``request``, waiting and resending while the failure class is retryable.

    A stream is read until it produces output; a stream that fails or ends
    before that counts as a failed attempt. Output is never resent. The last
    failure reaches the caller unchanged except for ``x-should-retry: false``.
    Local deadlines and client disconnects are never retried.
    """
    attempt = 0
    waited = 0.0

    def note(message: str) -> None:
        if log is not None:
            # Diagnostics must never fail a request.
            with contextlib.suppress(Exception):
                log(message)

    def retry(error_class: ErrorClass, status: int | None, headers: Any, reason: str) -> bool:
        nonlocal attempt, waited
        if attempt >= policy.max_retries or not policy.retryable(error_class, status, headers):
            return False
        if not state.may_retry(policy.give_up_window_seconds):
            return False
        delay = policy.delay(retry_after(headers) if headers is not None else None)
        total_left = budget.total_timeout - (budget.clock() - budget.started)
        if waited + delay > policy.max_total_seconds or delay >= total_left - 1:
            return False
        attempt += 1
        note(f"{reason}; retry {attempt}/{policy.max_retries} in {delay:.0f}s")
        budget.wait(delay)
        budget.progress()  # A deliberate wait is not a stalled provider.
        waited += delay
        return True

    def give_up(reason: str) -> None:
        if not state.may_retry(policy.give_up_window_seconds):
            note(f"{reason}; passed through (retries ran out moments ago)")
            return
        note(f"gave up after {attempt} retries: {reason}" if attempt else f"not retried: {reason}")
        if attempt:
            state.give_up()

    def release(lease: str | None) -> None:
        if admission:
            admission.release(lease)

    while True:
        lease = admission.acquire() if admission else None
        try:
            response = opener(request, timeout=budget.remaining())
        except HTTPError as exc:
            release(lease)
            try:
                body = exc.read(_ERROR_BODY_BYTES) if exc.fp is not None else b""
            except (OSError, ValueError):
                body = b""
            error_class = classify(exc.code, body)
            reason = describe(error_class, exc.code, body)
            if retry(error_class, exc.code, exc.headers, reason):
                exc.close()
                continue
            give_up(reason)
            raise _final_error(exc, body) from None
        except (URLError, OSError, HTTPException) as exc:
            release(lease)
            local = _is(exc, "DeadlineExceeded") or _is(exc, "ClientDisconnected")
            if local or not retry("network", None, None, f"network ({type(exc).__name__})"):
                if not local:
                    give_up(f"network ({type(exc).__name__})")
                raise
            continue
        except BaseException:
            release(lease)
            raise
        if not streaming:
            state.succeeded()
            if attempt:
                note(f"succeeded after {attempt} retries")
            return response, lease
        try:
            prefix, verdict, data = _prefetch(response, protocol, budget, max_prefetch_bytes)
        except (OSError, HTTPException) as exc:
            if _is(exc, "DeadlineExceeded") or _is(exc, "ClientDisconnected"):
                response.close()
                release(lease)
                raise
            prefix, verdict, data = b"", "ended", None
        if verdict == "ok":
            state.succeeded()
            if attempt:
                note(f"succeeded after {attempt} retries")
            return PrefixedResponse(response, prefix), lease
        error_class = classify(None, data, stream_before_output=True) if data else "stream_interrupted"
        reason = describe(error_class, None, data) if data else "stream_interrupted (stream ended before output)"
        if retry(error_class, None, None, reason):
            response.close()
            release(lease)
            continue
        give_up(reason)
        return PrefixedResponse(response, prefix), lease
