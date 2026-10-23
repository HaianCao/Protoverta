"""Byte-in/byte-out incremental SSE translation."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from .core import Protocol
from .sse import SSEParser, encode_sse
from .stream_encoder import StreamEncoder
from .streaming import Delta, StreamDecoder, StreamError


@dataclass
class _PendingBlock:
    deltas: deque[Delta] = field(default_factory=deque)
    closed: bool = False


class _MessagesOrder:
    """Stream the first block and bound buffering for overlapping later blocks.

    Block order follows first appearance, not tool completion order. This never
    executes tools or buffers a whole text response to invent a stream.
    """

    def __init__(self, limit: int):
        self.limit = limit
        self.size = 0
        self.blocks: deque[_PendingBlock] = deque()
        self.tools: dict[int, _PendingBlock] = {}

    @staticmethod
    def _size(delta: Delta) -> int:
        return 64 + sum(len(value.encode("utf-8")) for value in (delta.text, delta.name, delta.call_id, delta.item_id) if value)

    def feed(self, delta: Delta) -> list[Delta]:
        if delta.kind == "start":
            return [replace(delta, input_tokens=0, output_tokens=0)]
        if delta.kind == "finish":
            if self.blocks:
                raise StreamError("Messages conversion finished with queued blocks")
            return [delta]
        if delta.kind == "tool_start":
            block = _PendingBlock()
            self.tools[delta.index] = block
            self.blocks.append(block)
        elif delta.kind in {"tool_delta", "tool_end"}:
            block = self.tools.get(delta.index)
            if block is None or block.closed:
                raise StreamError("Messages conversion received an invalid tool lifecycle")
            block.closed = delta.kind == "tool_end"
        elif delta.kind == "text":
            block = _PendingBlock(closed=True)
            self.blocks.append(block)
        else:
            raise StreamError("unsupported Messages ordering delta")
        block.deltas.append(delta)
        self.size += self._size(delta)
        if self.size > self.limit:
            raise StreamError("Messages pending block buffer exceeds size limit")
        ready = []
        while self.blocks:
            front = self.blocks[0]
            while front.deltas:
                item = front.deltas.popleft()
                self.size -= self._size(item)
                ready.append(item)
            if not front.closed:
                break
            self.blocks.popleft()
        return ready


class StreamTranscoder:
    """Translate complete SSE events as transport bytes arrive.

    ``finish`` is mandatory at upstream EOF and rejects interrupted streams.
    Calling code must not emit a successful terminal event after an exception.
    """

    def __init__(self, source: Protocol | str, target: Protocol | str, *, messages_usage_policy: str = "strict", max_pending_bytes: int = 8 * 1024 * 1024, response_transform: Callable[[dict], dict] | None = None) -> None:
        self.source = Protocol(source)
        self.target = Protocol(target)
        if messages_usage_policy not in {"strict", "deferred"}:
            raise ValueError("messages_usage_policy must be strict or deferred")
        if max_pending_bytes <= 0:
            raise ValueError("max_pending_bytes must be positive")
        cross_messages = self.target == Protocol.MESSAGES and self.source != Protocol.MESSAGES
        if cross_messages and messages_usage_policy == "strict":
            raise StreamError("incremental Messages output needs input_tokens at stream start; this source reports usage later")
        self.order = _MessagesOrder(max_pending_bytes) if cross_messages else None
        self.parser = SSEParser()
        self.decoder = StreamDecoder(self.source)
        self.encoder = StreamEncoder(self.target, response_transform=response_transform)
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
            for decoded in self.decoder.feed(event):
                for delta in self.order.feed(decoded) if self.order else [decoded]:
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


class _LifecycleValidator:
    """Check native stream lifecycle without interpreting feature payloads."""

    def __init__(self, protocol: Protocol | str) -> None:
        self.protocol = Protocol(protocol)
        self.started = False
        self.ended = False
        self.sequence = -1
        self.open_items: set[int] = set()
        self.finished_choices: set[int] = set()
        self.finish_reasons: dict[int, str] = {}
        self.usage_postludes: set[int] = set()
        self.choices: set[int] = set()
        self.stop_reason = False
        self.saw_trailing_done = False

    def _index(self, body: dict, field: str) -> int:
        value = body.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise StreamError(f"invalid native stream {field}")
        return value

    def feed(self, event) -> None:
        if self.ended:
            if self.protocol == Protocol.RESPONSES and event.event == "message" and event.data == "[DONE]" and not self.saw_trailing_done:
                self.saw_trailing_done = True
                return
            raise StreamError("event after stream completion")
        if self.protocol == Protocol.CHAT and event.data == "[DONE]":
            if not self.started or not self.choices or self.choices != self.finished_choices:
                raise StreamError("Chat stream ended before all choices finished")
            self.ended = True
            return
        try:
            body = json.loads(event.data)
        except (ValueError, TypeError) as exc:
            raise StreamError("native stream event is not valid JSON") from exc
        if not isinstance(body, dict):
            raise StreamError("native stream event must be an object")
        if event.event == "error" or body.get("type") == "error" or body.get("error"):
            raise StreamError("upstream stream reported an error")
        if self.protocol == Protocol.CHAT:
            choices = body.get("choices")
            if not isinstance(choices, list):
                raise StreamError("native Chat event requires choices")
            for choice in choices:
                if not isinstance(choice, dict):
                    raise StreamError("invalid native Chat choice")
                index = self._index(choice, "index")
                if index in self.finished_choices:
                    delta = choice.get("delta")
                    if (isinstance(delta, dict) and set(delta) <= {"role", "content"}
                            and delta.get("role") in (None, "assistant") and delta.get("content") in (None, "")
                            and choice.get("finish_reason") == self.finish_reasons[index]
                            and isinstance(body.get("usage"), dict) and index not in self.usage_postludes):
                        self.usage_postludes.add(index)
                        continue
                    raise StreamError("Chat delta after choice completion")
                self.choices.add(index)
                if choice.get("finish_reason") is not None:
                    self.finished_choices.add(index)
                    self.finish_reasons[index] = choice["finish_reason"]
            self.started = True
            return
        kind = body.get("type")
        if not isinstance(kind, str) or event.event not in {"message", kind}:
            raise StreamError("native event/data type mismatch")
        if self.protocol == Protocol.MESSAGES:
            if kind == "ping":
                return
            if kind == "message_start":
                if self.started:
                    raise StreamError("duplicate message_start")
                self.started = True
                return
            if not self.started:
                raise StreamError("Messages event before message_start")
            if kind in {"content_block_start", "content_block_delta", "content_block_stop"}:
                index = self._index(body, "index")
                if kind == "content_block_start":
                    if index in self.open_items or self.stop_reason:
                        raise StreamError("duplicate or late content block")
                    self.open_items.add(index)
                elif index not in self.open_items:
                    raise StreamError("Messages event without an open content block")
                elif kind == "content_block_stop":
                    self.open_items.remove(index)
            elif kind == "message_delta":
                delta = body.get("delta")
                if not isinstance(delta, dict):
                    raise StreamError("invalid Messages delta")
                if delta.get("stop_reason") is not None:
                    self.stop_reason = True
            elif kind == "message_stop":
                if self.open_items or not self.stop_reason:
                    raise StreamError("Messages stopped before content/stop reason completed")
                self.ended = True
            return
        sequence = self._index(body, "sequence_number")
        if sequence <= self.sequence:
            raise StreamError("Responses sequence_number did not increase")
        self.sequence = sequence
        if kind == "response.created":
            if self.started:
                raise StreamError("duplicate response.created")
            self.started = True
        elif not self.started:
            raise StreamError("Responses event before response.created")
        elif kind == "response.output_item.added":
            index = self._index(body, "output_index")
            if index in self.open_items:
                raise StreamError("duplicate Responses output item")
            self.open_items.add(index)
        elif kind == "response.output_item.done":
            index = self._index(body, "output_index")
            if index not in self.open_items:
                raise StreamError("Responses output item done without added")
            self.open_items.remove(index)
        elif kind in {"response.completed", "response.incomplete"}:
            response = body.get("response")
            expected = "completed" if kind == "response.completed" else "incomplete"
            if self.open_items or not isinstance(response, dict) or response.get("status") != expected:
                raise StreamError("Responses terminal event disagrees with lifecycle")
            self.ended = True
        elif kind == "response.failed":
            raise StreamError("upstream Responses stream failed")

    def finish(self) -> None:
        if not self.ended:
            raise StreamError("stream ended before its terminal event")


class StreamValidator:
    """Validate native SSE framing/lifecycle while preserving feature payloads.

    Unlike cross-protocol decoding, native forwarding can carry thinking,
    multimodal and hosted-tool events without interpreting their contents.
    """

    def __init__(self, protocol: Protocol | str) -> None:
        self.parser = SSEParser()
        self.decoder = _LifecycleValidator(protocol)
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
