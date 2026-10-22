"""Byte-in/byte-out incremental SSE translation."""

from __future__ import annotations

from .core import Protocol
from .sse import SSEParser, encode_sse
from .stream_encoder import StreamEncoder
from .streaming import StreamDecoder, StreamError


class StreamTranscoder:
    """Translate complete SSE events as transport bytes arrive.

    ``finish`` is mandatory at upstream EOF and rejects interrupted streams.
    Calling code must not emit a successful terminal event after an exception.
    """

    def __init__(self, source: Protocol | str, target: Protocol | str) -> None:
        self.source = Protocol(source)
        self.target = Protocol(target)
        if self.target == Protocol.MESSAGES and self.source != Protocol.MESSAGES:
            raise StreamError("incremental Messages output needs input_tokens at stream start; this source reports usage later")
        self.parser = SSEParser()
        self.decoder = StreamDecoder(self.source)
        self.encoder = StreamEncoder(self.target)
        self._terminal = b""
        self._closed = False

    def feed(self, chunk: bytes) -> bytes:
        if self._closed:
            raise StreamError("stream already finished")
        output = bytearray()
        try:
            events = self.parser.feed(chunk)
        except (UnicodeError, ValueError) as exc:
            raise StreamError(str(exc)) from exc
        for event in events:
            for delta in self.decoder.feed(event):
                for encoded in self.encoder.feed(delta):
                    wire = encode_sse(encoded.event, encoded.data, id=encoded.id)
                    if delta.kind == "finish":
                        self._terminal += wire
                    else:
                        output.extend(wire)
        return bytes(output)

    def finish(self) -> bytes:
        if self._closed:
            raise StreamError("stream already finished")
        try:
            self.parser.finish(strict=True)
        except ValueError as exc:
            raise StreamError(str(exc)) from exc
        self.decoder.finish()
        self.encoder.finish()
        self._closed = True
        return self._terminal


class StreamValidator:
    """Validate a same-protocol stream and preserve its SSE event semantics."""

    def __init__(self, protocol: Protocol | str) -> None:
        self.parser = SSEParser()
        self.decoder = StreamDecoder(protocol)
        self._terminal = b""
        self._closed = False

    def feed(self, chunk: bytes) -> bytes:
        if self._closed:
            raise StreamError("stream already finished")
        try:
            events = self.parser.feed(chunk)
        except (UnicodeError, ValueError) as exc:
            raise StreamError(str(exc)) from exc
        output = bytearray()
        for event in events:
            self.decoder.feed(event)
            wire = encode_sse(event.event, event.data, id=event.id)
            if self.decoder.ended:
                self._terminal += wire
            else:
                output.extend(wire)
        return bytes(output)

    def finish(self) -> bytes:
        if self._closed:
            raise StreamError("stream already finished")
        try:
            self.parser.finish(strict=True)
        except (UnicodeError, ValueError) as exc:
            raise StreamError(str(exc)) from exc
        self.decoder.finish()
        self._closed = True
        return self._terminal
