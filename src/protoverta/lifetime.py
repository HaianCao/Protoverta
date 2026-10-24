"""Scope an upstream request to the local HTTP client's connection lifetime."""

import select
import socket
import threading


class DisconnectWatch:
    def __init__(self, connection, cancel):
        self.connection, self.cancel = connection, cancel
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True, name="protoverta-client-lifetime")

    def _watch(self):
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
            except OSError:
                if not self.stop.is_set():
                    self.cancel()
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.stop.set()
        self.thread.join(timeout=1)
