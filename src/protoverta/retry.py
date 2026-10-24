"""Bounded pre-response retries; never replay a partially delivered stream."""

import math
import time
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError


def retry_after(headers, *, now=None):
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


def open_with_retry(opener, request, budget, *, attempts=1, max_wait=5.0, admission=None, sleep=None):
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
            delay = delay if delay is not None else min(max_wait, 0.25 * 2 ** index)
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
