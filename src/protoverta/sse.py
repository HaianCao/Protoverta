"""Incremental UTF-8 Server-Sent Events framing primitives.

Protocol-specific event translation belongs in later codec modules. This
parser does not assume that a network read aligns with an SSE line or event.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SSEEvent:
    event: str
    data: str
    id: str | None = None


class SSEParser:
    def __init__(self, *, max_event_chars: int = 1_000_000) -> None:
        if max_event_chars <= 0:
            raise ValueError("max_event_chars must be positive")
        self._max_event_chars = max_event_chars
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._line = ""
        self._pending_cr = False
        self._event_type = ""
        self._data: list[str] = []
        self._id: str | None = None
        self._event_chars = 0

    def _dispatch(self) -> list[SSEEvent]:
        if not self._data:
            self._event_type = ""
            self._event_chars = 0
            return []
        event = SSEEvent(self._event_type or "message", "\n".join(self._data), self._id)
        self._event_type = ""
        self._data = []
        self._event_chars = 0
        return [event]

    def _process_line(self) -> list[SSEEvent]:
        line, self._line = self._line, ""
        if not line:
            return self._dispatch()
        if line.startswith(":"):
            return []
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            self._event_type = value
        elif field == "data":
            self._data.append(value)
        elif field == "id" and "\x00" not in value:
            self._id = value
        return []

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        events: list[SSEEvent] = []
        for char in self._decoder.decode(chunk):
            if self._pending_cr:
                self._pending_cr = False
                if char == "\n":
                    continue
            if char == "\r":
                events.extend(self._process_line())
                self._pending_cr = True
            elif char == "\n":
                events.extend(self._process_line())
            else:
                self._line += char
                self._event_chars += 1
                if self._event_chars > self._max_event_chars:
                    raise ValueError("SSE event exceeds size limit")
        return events

    def finish(self, *, strict: bool = False) -> list[SSEEvent]:
        remaining = self._decoder.decode(b"", final=True)
        if remaining:
            self._line += remaining
        incomplete = bool(self._line or self._data or self._event_type)
        # An EOF without a blank line is an incomplete event, not completion.
        self._line = ""
        self._data = []
        self._event_type = ""
        self._event_chars = 0
        if strict and incomplete:
            raise ValueError("SSE stream ended with an incomplete event")
        return []


def encode_sse(event: str, data: str, *, id: str | None = None) -> bytes:
    if not event or any(char in event for char in "\r\n\x00"):
        raise ValueError("invalid SSE event name")
    lines = [f"event: {event}"]
    if id is not None:
        if any(char in id for char in "\r\n\x00"):
            raise ValueError("invalid SSE event ID")
        lines.append(f"id: {id}")
    lines.extend("data: " + part for part in data.split("\n"))
    return ("\n".join(lines) + "\n\n").encode("utf-8")
