"""urllib transport retaining public connection sockets for read deadlines."""

from functools import partial
from http.client import HTTPConnection, HTTPResponse, HTTPSConnection
from urllib.request import HTTPHandler, HTTPSHandler


class _TimedResponse(HTTPResponse):
    def __init__(self, sock, *args, budget=None, **kwargs):
        self._deadline_socket = sock
        self._budget = budget
        super().__init__(sock, *args, **kwargs)

    def set_read_timeout(self, seconds):
        if self.fp is not None:
            self._deadline_socket.settimeout(seconds)

    def begin(self):
        if self._budget is None or self.headers is not None:
            return super().begin()
        original = self.fp
        self.fp = _HeaderReader(original, self._deadline_socket, self._budget)
        try:
            return super().begin()
        finally:
            if self.fp is not None:
                self.fp = original


class _HeaderReader:
    """Check budgets between header bytes; a slow drip cannot reset deadlines."""

    def __init__(self, stream, sock, budget):
        self.stream, self.sock, self.budget = stream, sock, budget

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def readline(self, limit=-1):
        result = bytearray()
        while limit < 0 or len(result) < limit:
            self.sock.settimeout(self.budget.remaining())
            try:
                char = self.stream.read(1)
            except TimeoutError:
                self.budget.remaining()
                raise
            self.budget.remaining()
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
