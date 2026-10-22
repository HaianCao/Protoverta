"""Incremental target SSE encoders for the supported text/function subset.

An encoder refuses mappings that cannot represent known facts faithfully. In
particular, Messages requires input-token usage in its first event, so a source
that reports it only at completion cannot be encoded as Messages incrementally.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .core import Protocol
from .sse import SSEEvent
from .streaming import Delta, StreamError


def _event(kind: str, body: dict[str, Any]) -> SSEEvent:
    return SSEEvent(kind, json.dumps(body, ensure_ascii=False, separators=(",", ":")))


class StreamEncoder:
    """Convert ordered canonical deltas to one target protocol's SSE events."""

    def __init__(self, protocol: Protocol | str) -> None:
        self.protocol = Protocol(protocol)
        self.started = False
        self.ended = False
        self.model = ""
        self.id = ""
        self.created = int(time.time())
        self.sequence = 0
        self.next_index = 0
        self.text_index: int | None = None
        self.text = ""
        self.tools: dict[int, dict[str, Any]] = {}
        self.open_blocks: set[int] = set()
        self.output: list[dict[str, Any]] = []
        self.saw_tool = False
        self.message_output_index: int | None = None
        self.initial_input_tokens: int | None = None

    def finish(self) -> None:
        if not self.ended:
            raise StreamError("encoder ended before a finish delta")

    def feed(self, delta: Delta) -> list[SSEEvent]:
        if self.ended:
            raise StreamError("delta after stream completion")
        if delta.kind == "start":
            if self.started or not delta.model:
                raise StreamError("invalid or duplicate stream start")
            if self.protocol == Protocol.MESSAGES and (delta.input_tokens is None or delta.output_tokens is None):
                raise StreamError("Messages streaming requires input_tokens and output_tokens at start")
            self.started = True
            self.model = delta.model
            self.initial_input_tokens = delta.input_tokens
            prefix = {Protocol.CHAT: "chatcmpl_", Protocol.MESSAGES: "msg_", Protocol.RESPONSES: "resp_"}[self.protocol]
            self.id = f"{prefix}{uuid.uuid4().hex}"
            return self._start(delta)
        if not self.started:
            raise StreamError("delta before stream start")
        if delta.kind == "finish":
            if delta.reason not in {"stop", "tool_calls", "length"}:
                raise StreamError("unsupported finish reason")
            if self.open_blocks:
                raise StreamError("finish with open tool calls")
            if (delta.reason == "tool_calls") != self.saw_tool and delta.reason != "length":
                raise StreamError("finish reason disagrees with tool calls")
            result = self._finish(delta)
            self.ended = True
            return result
        if delta.kind == "text":
            if delta.text is None:
                raise StreamError("text delta has no text")
            return self._text(delta)
        if delta.kind == "tool_start":
            if delta.index is None or not delta.call_id or not delta.name or delta.index in self.tools:
                raise StreamError("invalid or duplicate tool start")
            self.tools[delta.index] = {"call_id": delta.call_id, "name": delta.name, "arguments": "", "item_id": delta.item_id or f"fc_{uuid.uuid4().hex}"}
            self.open_blocks.add(delta.index)
            self.saw_tool = True
            return self._tool_start(delta)
        if delta.kind == "tool_delta":
            if delta.index not in self.open_blocks or delta.text is None:
                raise StreamError("tool delta without an open call")
            self.tools[delta.index]["arguments"] += delta.text
            return self._tool_delta(delta)
        if delta.kind == "tool_end":
            if delta.index not in self.open_blocks:
                raise StreamError("tool end without an open call")
            self.open_blocks.remove(delta.index)
            return self._tool_end(delta)
        raise StreamError(f"unsupported canonical delta: {delta.kind}")

    def _response_event(self, kind: str, **body: Any) -> SSEEvent:
        sequence = self.sequence
        self.sequence += 1
        return _event(kind, {"type": kind, "sequence_number": sequence, **body})

    def _response_snapshot(self, status: str, *, usage: dict[str, int] | None = None, incomplete_details: dict[str, str] | None = None) -> dict[str, Any]:
        return {
            "id": self.id, "object": "response", "access_programs": None,
            "created_at": self.created, "completed_at": int(time.time()) if status == "completed" else None,
            "status": status, "error": None, "incomplete_details": incomplete_details,
            "instructions": None, "max_output_tokens": None, "model": self.model,
            "output": self.output.copy() if status != "in_progress" else [],
            "parallel_tool_calls": True, "previous_response_id": None,
            "reasoning": {"effort": None, "summary": None}, "store": False,
            "temperature": None, "text": {"format": {"type": "text"}},
            "tool_choice": "auto", "tools": [], "top_p": None,
            "truncation": "disabled", "usage": usage, "user": None, "metadata": {},
        }

    def _chat_chunk(self, choices: list[dict[str, Any]], usage: dict[str, int] | None = None) -> SSEEvent:
        for choice in choices:
            choice["logprobs"] = None
        body: dict[str, Any] = {"id": self.id, "object": "chat.completion.chunk", "created": self.created, "model": self.model, "choices": choices}
        if usage is not None:
            body["usage"] = usage
        return _event("message", body)

    def _start(self, delta: Delta) -> list[SSEEvent]:
        if self.protocol == Protocol.CHAT:
            return [self._chat_chunk([{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}])]
        if self.protocol == Protocol.MESSAGES:
            return [_event("message_start", {"type": "message_start", "message": {
                "id": self.id, "type": "message", "role": "assistant", "content": [], "model": self.model,
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": delta.input_tokens, "output_tokens": delta.output_tokens},
            }})]
        return [self._response_event("response.created", response=self._response_snapshot("in_progress"))]

    def _text(self, delta: Delta) -> list[SSEEvent]:
        if self.protocol == Protocol.CHAT:
            return [self._chat_chunk([{"index": 0, "delta": {"content": delta.text}, "finish_reason": None}])]
        if self.protocol == Protocol.MESSAGES:
            if self.open_blocks:
                raise StreamError("Messages cannot interleave text within an open tool block")
            events = []
            if self.text_index is None:
                self.text_index = self.next_index
                self.next_index += 1
                events.append(_event("content_block_start", {"type": "content_block_start", "index": self.text_index, "content_block": {"type": "text", "text": ""}}))
            events.append(_event("content_block_delta", {"type": "content_block_delta", "index": self.text_index, "delta": {"type": "text_delta", "text": delta.text}}))
            return events
        events = []
        if self.text_index is None:
            self.text_index = self.next_index
            self.next_index += 1
            self.message_output_index = self.text_index
            item_id = f"msg_{uuid.uuid4().hex}"
            self.output.append({"id": item_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []})
            events.extend([
                self._response_event("response.output_item.added", output_index=self.text_index, item=self.output[-1].copy()),
                self._response_event("response.content_part.added", item_id=item_id, output_index=self.text_index, content_index=0, part={"type": "output_text", "text": "", "annotations": [], "logprobs": []}),
            ])
        self.text += delta.text
        events.append(self._response_event("response.output_text.delta", item_id=self.output[self.message_output_index]["id"], output_index=self.text_index, content_index=0, delta=delta.text, logprobs=[]))
        return events

    def _close_text(self, *, status: str = "completed") -> list[SSEEvent]:
        if self.text_index is None:
            return []
        index = self.text_index
        self.text_index = None
        if self.protocol == Protocol.MESSAGES:
            return [_event("content_block_stop", {"type": "content_block_stop", "index": index})]
        if self.protocol == Protocol.RESPONSES:
            item = self.output[self.message_output_index] if self.message_output_index is not None else None
            if item is None:
                raise StreamError("missing response message item")
            part = {"type": "output_text", "text": self.text, "annotations": [], "logprobs": []}
            item.update(status=status, content=[part])
            self.text = ""
            return [
                self._response_event("response.output_text.done", item_id=item["id"], output_index=index, content_index=0, text=part["text"], logprobs=[]),
                self._response_event("response.content_part.done", item_id=item["id"], output_index=index, content_index=0, part=part),
                self._response_event("response.output_item.done", output_index=index, item=item.copy()),
            ]
        return []

    def _tool_start(self, delta: Delta) -> list[SSEEvent]:
        if self.protocol == Protocol.CHAT:
            return [self._chat_chunk([{"index": 0, "delta": {"tool_calls": [{"index": delta.index, "id": delta.call_id, "type": "function", "function": {"name": delta.name, "arguments": ""}}]}, "finish_reason": None}])]
        if self.protocol == Protocol.MESSAGES:
            if len(self.open_blocks) > 1:
                raise StreamError("Messages cannot interleave multiple open tool blocks")
            events = self._close_text()
            index = self.next_index
            self.next_index += 1
            self.tools[delta.index]["target_index"] = index
            events.append(_event("content_block_start", {"type": "content_block_start", "index": index, "content_block": {"type": "tool_use", "id": delta.call_id, "name": delta.name, "input": {}}}))
            return events
        events = self._close_text()
        index = self.next_index
        self.next_index += 1
        tool = self.tools[delta.index]
        tool["target_index"] = index
        item = {"id": tool["item_id"], "type": "function_call", "status": "in_progress", "call_id": delta.call_id, "name": delta.name, "arguments": ""}
        self.output.append(item)
        events.append(self._response_event("response.output_item.added", output_index=index, item=item.copy()))
        return events

    def _tool_delta(self, delta: Delta) -> list[SSEEvent]:
        if self.protocol == Protocol.CHAT:
            return [self._chat_chunk([{"index": 0, "delta": {"tool_calls": [{"index": delta.index, "function": {"arguments": delta.text}}]}, "finish_reason": None}])]
        tool = self.tools[delta.index]
        if self.protocol == Protocol.MESSAGES:
            return [_event("content_block_delta", {"type": "content_block_delta", "index": tool["target_index"], "delta": {"type": "input_json_delta", "partial_json": delta.text}})]
        return [self._response_event("response.function_call_arguments.delta", item_id=tool["item_id"], output_index=tool["target_index"], delta=delta.text)]

    def _tool_end(self, delta: Delta) -> list[SSEEvent]:
        if self.protocol == Protocol.CHAT:
            return []
        tool = self.tools[delta.index]
        if self.protocol == Protocol.MESSAGES:
            return [_event("content_block_stop", {"type": "content_block_stop", "index": tool["target_index"]})]
        item = self.output[tool["target_index"]]
        item.update(status="completed", arguments=tool["arguments"])
        return [
            self._response_event("response.function_call_arguments.done", item_id=tool["item_id"], output_index=tool["target_index"], arguments=tool["arguments"]),
            self._response_event("response.output_item.done", output_index=tool["target_index"], item=item.copy()),
        ]

    def _finish(self, delta: Delta) -> list[SSEEvent]:
        if self.protocol == Protocol.CHAT:
            events = [self._chat_chunk([{"index": 0, "delta": {}, "finish_reason": delta.reason}])]
            if delta.input_tokens is not None and delta.output_tokens is not None:
                events.append(self._chat_chunk([], {"prompt_tokens": delta.input_tokens, "completion_tokens": delta.output_tokens, "total_tokens": delta.input_tokens + delta.output_tokens}))
            events.append(SSEEvent("message", "[DONE]"))
            return events
        if self.protocol == Protocol.MESSAGES:
            if delta.output_tokens is None:
                raise StreamError("Messages final event requires output_tokens")
            events = self._close_text()
            reason = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}[delta.reason]
            events.extend([
                _event("message_delta", {"type": "message_delta", "delta": {"stop_reason": reason, "stop_sequence": None}, "usage": {"input_tokens": delta.input_tokens, "output_tokens": delta.output_tokens}}),
                _event("message_stop", {"type": "message_stop"}),
            ])
            return events
        if delta.input_tokens is None or delta.output_tokens is None:
            raise StreamError("Responses final event requires input and output token usage")
        incomplete = delta.reason == "length"
        events = self._close_text(status="incomplete" if incomplete else "completed")
        response = self._response_snapshot(
            "incomplete" if incomplete else "completed",
            usage={
                "input_tokens": delta.input_tokens,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": delta.output_tokens,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": delta.input_tokens + delta.output_tokens,
            },
            incomplete_details={"reason": "max_output_tokens"} if incomplete else None,
        )
        return events + [self._response_event("response.incomplete" if incomplete else "response.completed", response=response)]
