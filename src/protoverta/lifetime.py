"""Scope an upstream request to the local HTTP client's connection lifetime."""

from __future__ import annotations

import select
import socket
import threading
from collections.abc import Callable
from types import TracebackType
from typing import Self


class DisconnectWatch:
    def __init__(self, connection: socket.socket, cancel: Callable[[], None]) -> None:
        self.connection, self.cancel = connection, cancel
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True, name="protoverta-client-lifetime")

    def _watch(self) -> None:
        while not self.stop.is_set():
            try:
                readable, _, exceptional = select.select([self.connection], [], [self.connection], 0.1)
                if exceptional:
                    self.cancel()
                    return
                if readable:
                    if not self.connection.recv(1, socket.MSG_PEEK):
                        self.cancel()
                        return
                    # Do not consume pipelined bytes; the HTTP server owns them.
                    self.stop.wait(0.1)
            except (OSError, ValueError):
                if not self.stop.is_set():
                    self.cancel()
                return

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        self.stop.set()
        self.thread.join(timeout=1)
