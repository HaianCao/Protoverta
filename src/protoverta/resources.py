"""Request deadlines and bounded wire-level stream accounting."""

import io
import json
import math
import socket
import threading
import time

from .streaming import StreamError


class StreamLimitError(StreamError):
    """A configured stream retention/size budget was exceeded."""


class DeadlineExceeded(TimeoutError):
    def __init__(self, kind):
        self.kind = kind
        super().__init__("Upstream request deadline exceeded." if kind == "request_deadline" else "Upstream made no content progress before the deadline.")


class ClientDisconnected(ConnectionAbortedError):
    pass


class DeadlineInput(io.RawIOBase):
    """Enforce an absolute request budget on every raw client socket read.

    A socket timeout alone restarts on each recv, allowing a trickling body or
    header to live forever. BufferedReader must wrap this, not the other way round.
    """

    def __init__(self, raw, connection, budget):
        self.raw, self.connection, self.budget = raw, connection, budget

    def readable(self):
        return True

    def readinto(self, buffer):
        limit = self.budget.remaining()
        self.connection.settimeout(limit)
        try:
            count = self.raw.readinto(buffer)
        except TimeoutError:
            self.budget.on_timeout(limit)
            raise
        self.budget.remaining()
        if count:
            self.budget.progress()
        return count

    def close(self):
        try:
            self.raw.close()
        finally:
            super().close()


class RequestBudget:
    def __init__(self, io_timeout, total_timeout, progress_timeout, *, clock=None):
        if not all(positive_number(value) for value in (io_timeout, total_timeout, progress_timeout)):
            raise ValueError("request timeouts must be finite positive numbers")
        self.clock = clock or time.monotonic
        self.io_timeout, self.total_timeout, self.progress_timeout = io_timeout, total_timeout, progress_timeout
        self.started = self.last_progress = self.clock()
        self._cancelled = threading.Event()
        self._sockets = []
        self._lock = threading.Lock()

    def register_socket(self, sock):
        with self._lock:
            self._sockets.append(sock)
            if self._cancelled.is_set():
                self._shutdown(sock)

    @staticmethod
    def _shutdown(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def cancel(self):
        self._cancelled.set()
        with self._lock:
            for sock in self._sockets:
                self._shutdown(sock)

    def wait(self, seconds):
        if self._cancelled.wait(seconds):
            raise ClientDisconnected("Local HTTP client disconnected.")
        self.remaining()

    def remaining(self):
        if self._cancelled.is_set():
            raise ClientDisconnected("Local HTTP client disconnected.")
        now = self.clock()
        total = self.total_timeout - (now - self.started)
        progress = self.progress_timeout - (now - self.last_progress)
        if total <= 0:
            raise DeadlineExceeded("request_deadline")
        if progress <= 0:
            raise DeadlineExceeded("progress_timeout")
        return min(self.io_timeout, total, progress)

    def progress(self):
        self.remaining()
        self.last_progress = self.clock()

    def on_timeout(self, limit):
        """Classify an OS-rounded timeout using the bound that armed the read."""
        self.remaining()
        if limit < self.io_timeout:
            # Windows socket timers can fire slightly before monotonic reaches
            # the exact boundary. Do not turn a deadline into a retryable I/O
            # timeout (or retry a buffered reader already marked timed out).
            total_end = self.started + self.total_timeout
            progress_end = self.last_progress + self.progress_timeout
            raise DeadlineExceeded("request_deadline" if total_end <= progress_end else "progress_timeout")

    def read(self, response, size, *, streaming=False):
        timeout = self.remaining()
        setter = getattr(response, "set_read_timeout", None)
        if setter is not None:
            setter(timeout)
        method = response.read1 if streaming and hasattr(response, "read1") else response.read
        try:
            result = method(size)
        except TimeoutError:
            self.on_timeout(timeout)
            raise
        self.remaining()
        return result


def positive_number(value):
    return not isinstance(value, bool) and isinstance(value, int | float) and math.isfinite(value) and value > 0


class StreamLimits:
    def __init__(self, max_bytes=32 * 1024 * 1024, max_items=1024, max_argument_bytes=8 * 1024 * 1024):
        if any(type(value) is not int or value <= 0 for value in (max_bytes, max_items, max_argument_bytes)):
            raise ValueError("stream limits must be positive integers")
        self.max_bytes, self.max_items, self.max_argument_bytes = max_bytes, max_items, max_argument_bytes
        self.bytes = self.items = self.argument_bytes = self.progress = 0
        self.chat_tools = set()

    def consume(self, chunk):
        self.bytes += len(chunk)
        if self.bytes > self.max_bytes:
            raise StreamLimitError("upstream stream exceeds total byte limit")

    def observe(self, event):
        if event.data == "[DONE]":
            return
        try:
            body = json.loads(event.data)
        except (ValueError, TypeError, RecursionError):
            return  # The protocol validator owns malformed JSON errors.
        if not isinstance(body, dict):
            return
        kind = body.get("type")
        if not isinstance(kind, str):
            kind = None
        if kind in {"response.output_item.added", "content_block_start"}:
            self.items += 1
            block = body.get("item", body.get("content_block", {}))
            if isinstance(block, dict) and isinstance(block.get("type"), str) and block["type"] in {"function_call", "tool_use", "custom_tool_call", "server_tool_use", "tool_search_call"}:
                self.progress += 1
                if block["type"] == "tool_search_call" and isinstance(block.get("arguments"), dict):
                    self._arguments(json.dumps(block["arguments"], ensure_ascii=False))
                elif block["type"] in {"tool_use", "server_tool_use"} and isinstance(block.get("input"), dict) and block["input"]:
                    self._arguments(json.dumps(block["input"], ensure_ascii=False))
                else:
                    self._arguments(block.get("input", "") if block["type"] == "custom_tool_call" else block.get("arguments", ""))
            elif isinstance(block, dict) and any(isinstance(block.get(key), str) and block[key] for key in ("text", "thinking")):
                self.progress += 1
        if kind == "content_block_delta" and isinstance(body.get("delta"), dict):
            delta = body["delta"]
            if delta.get("type") == "input_json_delta":
                self._arguments(delta.get("partial_json"))
            if any(isinstance(delta.get(key), str) and delta[key] for key in ("text", "thinking", "partial_json", "signature")):
                self.progress += 1
        if isinstance(kind, str) and kind.endswith(".delta") and isinstance(body.get("delta"), str):
            self.progress += bool(body["delta"])
            if kind in {"response.function_call_arguments.delta", "response.custom_tool_call_input.delta"}:
                self._arguments(body["delta"])
        for choice in body.get("choices", []) if isinstance(body.get("choices"), list) else []:
            if not isinstance(choice, dict) or not isinstance(choice.get("delta"), dict):
                continue
            delta = choice["delta"]
            self.progress += any(isinstance(delta.get(key), str) and delta[key] for key in ("content", "reasoning_content", "reasoning"))
            for tool in delta.get("tool_calls", []) if isinstance(delta.get("tool_calls"), list) else []:
                if not isinstance(tool, dict):
                    continue
                choice_index, tool_index = choice.get("index"), tool.get("index")
                key = (choice_index if type(choice_index) is int else None,
                       tool_index if type(tool_index) is int else tool.get("id") if isinstance(tool.get("id"), str) else None)
                new = key not in self.chat_tools
                if new:
                    self.chat_tools.add(key)
                    self.items += 1
                fn = tool.get("function")
                if isinstance(tool.get("custom"), dict):
                    fn = {"name": tool["custom"].get("name"), "arguments": tool["custom"].get("input")}
                if isinstance(fn, dict):
                    self._arguments(fn.get("arguments"))
                    self.progress += bool(new and fn.get("name") or fn.get("arguments"))
        if self.items > self.max_items:
            raise StreamLimitError("upstream stream exceeds item limit")

    def _arguments(self, value):
        if isinstance(value, str):
            self.argument_bytes += len(value.encode("utf-8"))
            if self.argument_bytes > self.max_argument_bytes:
                raise StreamLimitError("upstream tool arguments exceed byte limit")
