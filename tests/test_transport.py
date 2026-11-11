"""Deadline transport: buffered line reads and the TLS context it forwards."""

import io
import ssl

import pytest

from protoverta.resources import RequestBudget
from protoverta.transport import DeadlineHTTPSHandler, _HeaderReader


class CountingRaw(io.RawIOBase):
    def __init__(self, data, size=7):
        self.data, self.size, self.reads = data, size, 0

    def readable(self):
        return True

    def readinto(self, buffer):
        self.reads += 1
        chunk, self.data = self.data[: min(self.size, len(buffer))], self.data[min(self.size, len(buffer)) :]
        buffer[: len(chunk)] = chunk
        return len(chunk)


class FakeSocket:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)


def reader(data, size=7):
    raw = CountingRaw(data, size)
    sock = FakeSocket()
    return _HeaderReader(io.BufferedReader(raw, 64), sock, RequestBudget(5, 5, 5)), raw, sock


def test_readline_returns_whole_lines_with_few_socket_reads():
    line = b"Content-Type: application/json\r\n"
    stream, raw, sock = reader(line + b"X-Next: 1\r\n\r\n")
    assert stream.readline() == line
    assert stream.readline() == b"X-Next: 1\r\n"
    assert stream.readline() == b"\r\n"
    assert stream.readline() == b""
    # Seven-byte socket reads, never one read per byte; every read had a deadline.
    assert raw.reads <= len(line) // 7 + 6 and len(sock.timeouts) >= raw.reads - 1


def test_readline_honours_limit_and_keeps_the_rest():
    stream, _, _ = reader(b"abcdefghij\nrest\n", size=64)
    assert stream.readline(4) == b"abcd"
    assert stream.readline() == b"efghij\n"
    assert stream.read(4) == b"rest"


def test_readline_reports_a_socket_timeout_to_the_budget():
    class Timeout(io.RawIOBase):
        def readable(self):
            return True

        def readinto(self, buffer):
            raise TimeoutError

    calls = []
    budget = RequestBudget(5, 5, 5)
    budget.on_timeout = calls.append
    stream = _HeaderReader(io.BufferedReader(Timeout()), FakeSocket(), budget)
    with pytest.raises(TimeoutError):
        stream.readline()
    assert len(calls) == 1


def test_https_handler_forwards_its_own_tls_context():
    context = ssl.create_default_context()
    seen = []

    class Handler(DeadlineHTTPSHandler):
        def do_open(self, http_class, request, **options):
            seen.append(options)

    Handler(context=context).https_open(object())
    Handler().https_open(object())
    assert seen == [{"context": context}, {"context": None}]
