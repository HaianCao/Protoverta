"""urllib transport retaining public connection sockets for read deadlines."""

from functools import partial
from http.client import HTTPConnection, HTTPResponse, HTTPSConnection
from urllib.request import HTTPHandler, HTTPSHandler


class _TimedResponse(HTTPResponse):
    def __init__(self, sock, *args, budget=None, **kwargs):
        self._deadline_socket = sock
        self._budget = budget
        if budget is not None:
            budget.register_socket(sock)
        super().__init__(sock, *args, **kwargs)

    def set_read_timeout(self, seconds):
        if self.fp is not None:
            self._deadline_socket.settimeout(seconds)

    def begin(self):
        if self._budget is None or self.headers is not None:
            return super().begin()
        # Keep the wrapper for the body too: http.client reads chunk-size lines,
        # chunk terminators and trailers internally, outside RequestBudget.read.
        self.fp = _HeaderReader(self.fp, self._deadline_socket, self._budget)
        return super().begin()


class _HeaderReader:
    """Deadline-aware buffered reader for headers AND chunked framing/body."""

    def __init__(self, stream, sock, budget):
        self.stream, self.sock, self.budget = stream, sock, budget

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def read1(self, size=-1):
        timeout = self.budget.remaining()
        self.sock.settimeout(timeout)
        try:
            result = self.stream.read1(size)
        except TimeoutError:
            self.budget.on_timeout(timeout)
            raise
        self.budget.remaining()
        return result

    def read(self, size=-1):
        chunks = []
        remaining = size
        while remaining != 0:
            chunk = self.read1(min(remaining, 65536) if remaining > 0 else 65536)
            if not chunk:
                break
            chunks.append(chunk)
            if remaining > 0:
                remaining -= len(chunk)
        return b"".join(chunks)

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def readline(self, limit=-1):
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
    def http_open(self, request):
        return self.do_open(_connection(_HTTPConnection, request), request)


class DeadlineHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(_connection(_HTTPSConnection, request), request, context=self._context)


def _connection(kind, request):
    def create(*args, **kwargs):
        connection = kind(*args, **kwargs)
        connection.response_class = partial(_TimedResponse, budget=getattr(request, "protoverta_budget", None))
        return connection

    return create
