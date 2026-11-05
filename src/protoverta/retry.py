"""Bounded pre-response retries; never replay a partially delivered stream."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from typing import Protocol, TypeVar
from urllib.error import HTTPError
from urllib.request import Request


class RetryHeaders(Protocol):
    def get(self, key: str) -> str | None: ...


class RetryBudget(Protocol):
    def remaining(self) -> float: ...
    def wait(self, seconds: float) -> None: ...


class Admission(Protocol):
    def acquire(self) -> str: ...
    def release(self, identifier: str | None) -> None: ...
    def cooldown(self, seconds: float) -> None: ...


ResponseT_co = TypeVar("ResponseT_co", covariant=True)


class UpstreamOpener(Protocol[ResponseT_co]):
    def __call__(self, request: Request, *, timeout: float) -> ResponseT_co: ...


def retry_after(headers: RetryHeaders | None, *, now: float | None = None) -> float | None:
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
