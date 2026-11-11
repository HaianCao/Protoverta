"""One serialized socket writer with post-payload transport keepalives."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from .resources import ClientDisconnected, RequestBudget, positive_number


class StreamHeartbeatWriter:
    """Never read upstream, advance model progress, or manufacture SSE state.

    The handler owns framing/encoding. Both its complete writes and the timer's
    keepalive go through this object's lock, including socket timeout changes.
    The production callback must honor the supplied socket timeout. Arbitrary
    embedding callbacks are trusted code, not preemptible tasks.
    """

    def __init__(
        self,
        send: Callable[[bytes, float], object],
        budget: RequestBudget,
        keepalive: bytes | None = None,
        *,
        interval: float = 15.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        try:
            valid = positive_number(interval)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("heartbeat interval must be finite and positive")
        self.send, self.budget, self.keepalive = send, budget, keepalive
        self.interval, self.clock = interval, clock or time.monotonic
        self.last_write = self.clock()
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None
        self.failed = False

    def _send(self, raw: bytes, timeout: float) -> None:
        if self.failed:
            raise ClientDisconnected("Local HTTP client disconnected.")
        try:
            self.send(raw, timeout)
        except OSError:
            self.failed = True
            self.budget.cancel()
            raise ClientDisconnected("Local HTTP client disconnected.") from None
        self.last_write = self.clock()

    def write(self, raw: bytes, *, final_error: bool = False) -> None:
        with self.lock:
            timeout = min(1.0, self.budget.io_timeout) if final_error else self.budget.remaining()
            self._send(raw, timeout)

    def start(self) -> None:
        # Called by the handler only after headers AND real payload were sent.
        if self.keepalive is not None and self.thread is None and not self.stopped.is_set():
            self.thread = threading.Thread(target=self._run, daemon=True, name="protoverta-sse-heartbeat")
            self.thread.start()

    def _run(self) -> None:
        while not self.stopped.wait(
            min(threading.TIMEOUT_MAX, max(0, self.interval - (self.clock() - self.last_write)))
        ):
            if not self.lock.acquire(timeout=0.05):
                # A slow consumer already has a bounded real write in flight.
                if self.stopped.wait(0.05):
                    return
                continue
            try:
                if self.stopped.is_set():
                    return
                if self.clock() - self.last_write < self.interval:
                    continue
                try:
                    timeout = min(1.0, self.budget.remaining())
                except TimeoutError:
                    # Let the handler classify its deadline, never replace it
                    # with cancellation just because the monitor noticed first.
                    return
                if self.keepalive is not None:
                    self._send(self.keepalive, timeout)
            except ClientDisconnected:
                return
            finally:
                self.lock.release()

    def close(self) -> None:
        self.stopped.set()
        if self.thread is not None:
            # Heartbeat writes are capped at one second; interruptible waits
            # and bounded lock acquisition do not retain an idle monitor.
            self.thread.join(timeout=1.25)
