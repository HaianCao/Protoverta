"""urllib transport retaining public connection sockets for read deadlines."""

from __future__ import annotations

import socket
import ssl
from collections.abc import Callable
from functools import partial
from http.client import HTTPConnection, HTTPResponse, HTTPSConnection
from typing import Any
from urllib.request import HTTPHandler, HTTPSHandler, Request

from .resources import RequestBudget, request_budget


class _TimedResponse(HTTPResponse):
    def __init__(self, sock: socket.socket, *args: Any, budget: RequestBudget | None = None, **kwargs: Any) -> None:
        self._deadline_socket = sock
        self._budget = budget
        if budget is not None:
            budget.register_socket(sock)
        super().__init__(sock, *args, **kwargs)

    def set_read_timeout(self, seconds: float) -> None:
        if self.fp is not None:
            self._deadline_socket.settimeout(seconds)

    def begin(self) -> None:
        # http.client leaves headers unset until begin() parses them.
        if self._budget is None or getattr(self, "headers", None) is not None:
            return super().begin()
        # Keep the wrapper for the body too: http.client reads chunk-size lines,
        # chunk terminators and trailers internally, outside RequestBudget.read.
        self.fp = _HeaderReader(self.fp, self._deadline_socket, self._budget)  # type: ignore[assignment]
        return super().begin()


class _HeaderReader:
    """Deadline-aware buffered reader for headers AND chunked framing/body."""

    def __init__(self, stream: Any, sock: socket.socket, budget: RequestBudget) -> None:
        self.stream, self.sock, self.budget = stream, sock, budget

    def __getattr__(self, name: str) -> Any:
        return getattr(self.stream, name)

    def read1(self, size: int = -1) -> bytes:
        timeout = self.budget.remaining()
        self.sock.settimeout(timeout)
        try:
            result: bytes = self.stream.read1(size)
        except TimeoutError:
            self.budget.on_timeout(timeout)
            raise
        self.budget.remaining()
        return result

    def read(self, size: int | None = -1) -> bytes:
        chunks = []
        remaining = -1 if size is None else size
        while remaining != 0:
            chunk = self.read1(min(remaining, 65536) if remaining > 0 else 65536)
            if not chunk:
                break
            chunks.append(chunk)
            if remaining > 0:
                remaining -= len(chunk)
        return b"".join(chunks)

    def readinto(self, buffer: Any) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def readline(self, limit: int | None = -1) -> bytes:
        limit = -1 if limit is None else limit
        if not hasattr(self.stream, "peek"):
            return self._readline_bytewise(limit)
        result = bytearray()
        while limit < 0 or len(result) < limit:
            # peek() makes at most one socket read, under a freshly armed
            # deadline; the bytes it returns are then taken from the buffer.
            timeout = self.budget.remaining()
            self.sock.settimeout(timeout)
            try:
                buffered: bytes = self.stream.peek(1)
            except TimeoutError:
                self.budget.on_timeout(timeout)
                raise
            if not buffered:
                break
            wanted = len(buffered) if limit < 0 else min(len(buffered), limit - len(result))
            newline = buffered.find(b"\n", 0, wanted)
            result.extend(self.stream.read(newline + 1 if newline >= 0 else wanted))
            if newline >= 0:
                break
        self.budget.remaining()
        return bytes(result)

    def _readline_bytewise(self, limit: int) -> bytes:
        result = bytearray()
        while limit < 0 or len(result) < limit:
            char = self.read1(1)
            result.extend(char)
            if not char or char == b"\n":
                break
        return bytes(result)


class _HTTPConnection(HTTPConnection):
    response_class = _TimedResponse


class _HTTPSConnection(HTTPSConnection):
    response_class = _TimedResponse


class DeadlineHTTPHandler(HTTPHandler):
    def http_open(self, request: Request) -> HTTPResponse:
        return self.do_open(_connection(_HTTPConnection, request), request)


class DeadlineHTTPSHandler(HTTPSHandler):
    def __init__(
        self, debuglevel: int | None = None, context: ssl.SSLContext | None = None, check_hostname: bool | None = None
    ) -> None:
        # Python 3.11 expects an integer debug level; later versions accept None.
        super().__init__(0 if debuglevel is None else debuglevel, context, check_hostname)
        # Kept here rather than read back from CPython's private attributes.
        # None lets http.client build its default verified context, as before.
        self._tls_context = context

    def https_open(self, request: Request) -> HTTPResponse:
        return self.do_open(_connection(_HTTPSConnection, request), request, context=self._tls_context)


def _connection(kind: type[HTTPConnection], request: Request) -> Callable[..., HTTPConnection]:
    def create(*args: Any, **kwargs: Any) -> HTTPConnection:
        connection = kind(*args, **kwargs)
        connection.response_class = partial(_TimedResponse, budget=request_budget(request))  # type: ignore[assignment]
        return connection

    return create
