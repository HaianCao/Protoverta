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

    def feed(self, chunk: bytes) -> bytes:
        output = bytearray()
        for event in self.parser.feed(chunk):
            for delta in self.decoder.feed(event):
                for encoded in self.encoder.feed(delta):
                    output.extend(encode_sse(encoded.event, encoded.data, id=encoded.id))
        return bytes(output)

    def finish(self) -> bytes:
        if self.parser.finish():
            raise StreamError("unexpected buffered SSE events at EOF")
        self.decoder.finish()
        self.encoder.finish()
        return b""
