"""Incremental target SSE encoders for the supported text/function subset.

The strict encoder requires initial Messages usage. The byte pipeline can opt
into deferred usage and serialize concurrent blocks before feeding this encoder.
"""

from __future__ import annotations

import copy
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

from .core import (
    ConversionError,
    Protocol,
    _arguments,
    _encode_usage,
    _reported_service_tier,
)
from .json_wire import encode_json
from .reasoning import reasoning_item
from .response_context import generated_response_metadata
from .sse import SSEEvent
from .streaming import Delta, StreamError, UnsupportedReasoningError


def _event(kind: str, body: dict[str, Any]) -> SSEEvent:
    try:
        raw = encode_json(body, compact=True)
    except ConversionError as exc:
        raise StreamError("canonical event must contain valid JSON and Unicode") from exc
    return SSEEvent(kind, raw.decode("utf-8"))


class StreamEncoder:
    """Convert ordered canonical deltas to one target protocol's SSE events."""

    def __init__(
        self,
        protocol: Protocol | str,
        *,
        response_transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        public_reasoning: bool = False,
        request_context: dict[str, Any] | None = None,
        text_logprobs: bool = False,
    ) -> None:
        self.protocol = Protocol(protocol)
        if not isinstance(public_reasoning, bool):
            raise TypeError("public_reasoning must be a boolean")
        self.public_reasoning = public_reasoning
        self.reasoning_index: int | None = None
        self.reasoning_text = ""
        self.reasoning_closed = False
        if response_transform is not None and self.protocol != Protocol.RESPONSES:
            raise ValueError("response_transform is only supported for Responses output")
        self.response_transform = response_transform
        if request_context is not None:
            if self.protocol != Protocol.RESPONSES:
                raise ValueError("request_context is only supported for Responses output")
            from .response_context import bind_response_context

            context_transform = bind_response_context(request_context)
            self.response_transform = lambda event: context_transform(
                response_transform(event) if response_transform is not None else event
            )
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
        self.service_tier: str | None = None
        if type(text_logprobs) is not bool or text_logprobs and self.protocol == Protocol.MESSAGES:
            raise ValueError("text_logprobs requires a Chat/Responses stream")
        self.text_logprobs = text_logprobs
        self.probabilities: list[dict[str, Any]] = []
        self.probability_text = ""

    def finish(self) -> None:
        if not self.ended:
            raise StreamError("encoder ended before a finish delta")

    def feed(self, delta: Delta) -> list[SSEEvent]:
        if delta.logprobs and (not self.text_logprobs or delta.kind != "text"):
            raise StreamError("token logprobs require enabled text output")
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
        if delta.kind == "reasoning_text":
            return self._reasoning(delta)
        if self.public_reasoning and delta.kind == "text" and delta.text == "" and not delta.logprobs:
            return []
        if delta.kind in {"text", "tool_start", "finish"}:
            self.reasoning_closed = True
            closing = self._close_reasoning(
                status="incomplete" if delta.kind == "finish" and delta.reason == "length" else "completed"
            )
            if closing:
                return closing + self.feed(delta)
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
            if self.saw_tool and self.protocol in {Protocol.CHAT, Protocol.RESPONSES}:
                if delta.text == "" and not delta.logprobs:
                    return []  # An empty delta carries no post-tool content.
                raise StreamError("text after a tool call cannot be preserved in this target response")
            return self._text(delta)
        if delta.kind == "tool_start":
            if delta.tool_type not in ("function", "custom", "tool_search"):
                raise StreamError("unknown canonical tool type")
            if delta.tool_type == "tool_search" and self.protocol != Protocol.RESPONSES:
                raise StreamError("client search output requires Responses")
            if delta.tool_type == "custom" and self.protocol == Protocol.MESSAGES:
                raise StreamError("Messages cannot preserve native custom-tool formats")
            if delta.tool_type == "custom" and self.protocol == Protocol.CHAT:
                raise StreamError("Chat custom-tool streaming is not verified")
            if delta.index is None or not delta.call_id or not delta.name or delta.index in self.tools:
                raise StreamError("invalid or duplicate tool start")
            if any(tool["call_id"] == delta.call_id for tool in self.tools.values()):
                raise StreamError("duplicate stream tool call ID")
            self.tools[delta.position] = {
                "call_id": delta.call_id,
                "name": delta.name,
                "arguments": "",
                "item_id": delta.item_id or f"fc_{uuid.uuid4().hex}",
                "tool_type": delta.tool_type,
            }
            self.open_blocks.add(delta.index)
            self.saw_tool = True
            return self._tool_start(delta)
        if delta.kind == "tool_delta":
            if delta.index not in self.open_blocks or delta.text is None:
                raise StreamError("tool delta without an open call")
            self.tools[delta.position]["arguments"] += delta.text
            return self._tool_delta(delta)
        if delta.kind == "tool_end":
            if delta.index not in self.open_blocks:
                raise StreamError("tool end without an open call")
            self.open_blocks.remove(delta.index)
            return self._tool_end(delta)
        raise StreamError(f"unsupported canonical delta: {delta.kind}")

    def _reasoning(self, delta: Delta) -> list[SSEEvent]:
        if self.protocol == Protocol.MESSAGES:
            raise UnsupportedReasoningError()
        if not self.public_reasoning or self.reasoning_closed or not isinstance(delta.text, str):
            raise StreamError(
                "unsigned public reasoning requires an enabled Chat/Responses prefix; Messages signatures cannot be fabricated"
            )
        if self.protocol == Protocol.CHAT:
            return [self._chat_chunk([{"index": 0, "delta": {"reasoning_content": delta.text}, "finish_reason": None}])]
        events = []
        if self.reasoning_index is None:
            self.reasoning_index = self.next_index
            self.next_index += 1
            item = {
                "type": "reasoning",
                "id": "rs_" + uuid.uuid4().hex,
                "status": "in_progress",
                "summary": [],
                "content": [],
            }
            self.output.append(item)
            events.extend(
                [
                    self._response_event(
                        "response.output_item.added", output_index=self.reasoning_index, item=item.copy()
                    ),
                    self._response_event(
                        "response.content_part.added",
                        output_index=self.reasoning_index,
                        item_id=item["id"],
                        content_index=0,
                        part={"type": "reasoning_text", "text": ""},
                    ),
                ]
            )
        self.reasoning_text += delta.text
        events.append(
            self._response_event(
                "response.reasoning_text.delta",
                output_index=self.reasoning_index,
                item_id=self.output[self.reasoning_index]["id"],
                content_index=0,
                delta=delta.text,
            )
        )
        return events

    def _close_reasoning(self, *, status: str = "completed") -> list[SSEEvent]:
        if self.reasoning_index is None:
            return []
        index = self.reasoning_index
        self.reasoning_index = None
        item = self.output[index]
        item.update(reasoning_item(self.reasoning_text, identifier=item["id"], status=status))
        part = item["content"][0]
        return [
            self._response_event(
                "response.reasoning_text.done",
                output_index=index,
                item_id=item["id"],
                content_index=0,
                text=part["text"],
            ),
            self._response_event(
                "response.content_part.done", output_index=index, item_id=item["id"], content_index=0, part=part.copy()
            ),
            self._response_event("response.output_item.done", output_index=index, item=item.copy()),
        ]

    def _response_event(self, kind: str, **body: Any) -> SSEEvent:
        sequence = self.sequence
        self.sequence += 1
        payload = {"type": kind, "sequence_number": sequence, **body}
        if self.response_transform is not None:
            try:
                payload = self.response_transform(payload)
            except ConversionError as exc:
                raise StreamError(str(exc)) from exc
        return _event(kind, payload)

    def _response_snapshot(
        self, status: str, *, usage: dict[str, int] | None = None, incomplete_details: dict[str, str] | None = None
    ) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": "response",
            "access_programs": None,
            "created_at": self.created,
            "completed_at": int(time.time()) if status == "completed" else None,
            "status": status,
            "error": None,
            "incomplete_details": incomplete_details,
            "model": self.model,
            "output": self.output.copy() if status != "in_progress" else [],
            **generated_response_metadata(),
            "usage": usage,
            "user": None,
            "metadata": {},
            **({"service_tier": self.service_tier} if self.service_tier is not None else {}),
        }

    def _chat_chunk(self, choices: list[dict[str, Any]], usage: dict[str, int] | None = None) -> SSEEvent:
        for choice in choices:
            choice.setdefault("logprobs", None)
        body: dict[str, Any] = {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": choices,
        }
        if usage is not None:
            body["usage"] = usage
        if self.service_tier is not None:
            body["service_tier"] = self.service_tier
        return _event("message", body)

    def _start(self, delta: Delta) -> list[SSEEvent]:
        if self.protocol == Protocol.CHAT:
            return [
                self._chat_chunk([{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}])
            ]
        if self.protocol == Protocol.MESSAGES:
            return [
                _event(
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": self.id,
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": self.model,
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": _encode_usage(delta, Protocol.MESSAGES),
                        },
                    },
                )
            ]
        return [self._response_event("response.created", response=self._response_snapshot("in_progress"))]

    def _text(self, delta: Delta) -> list[SSEEvent]:
        from .logprobs import records

        try:
            probabilities = records(delta.logprobs) if delta.logprobs is not None else []
        except ConversionError as exc:
            raise StreamError(str(exc)) from exc
        if self.text_logprobs:
            self.probability_text += delta.content
            self.probabilities.extend(probabilities)
        if self.protocol == Protocol.CHAT:
            choice = {"index": 0, "delta": {"content": delta.text}, "finish_reason": None}
            if probabilities:
                choice["logprobs"] = {"content": probabilities, "refusal": None}
            return [self._chat_chunk([choice])]
        if self.protocol == Protocol.MESSAGES:
            if self.open_blocks:
                raise StreamError("Messages cannot interleave text within an open tool block")
            events = []
            if self.text_index is None:
                self.text_index = self.next_index
                self.next_index += 1
                events.append(
                    _event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": self.text_index,
                            "content_block": {"type": "text", "text": ""},
                        },
                    )
                )
            events.append(
                _event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": self.text_index,
                        "delta": {"type": "text_delta", "text": delta.text},
                    },
                )
            )
            return events
        events = []
        if self.text_index is None:
            self.text_index = self.next_index
            self.next_index += 1
            self.message_output_index = self.text_index
            item_id = f"msg_{uuid.uuid4().hex}"
            self.output.append(
                {"id": item_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []}
            )
            events.extend(
                [
                    self._response_event(
                        "response.output_item.added", output_index=self.text_index, item=self.output[-1].copy()
                    ),
                    self._response_event(
                        "response.content_part.added",
                        item_id=item_id,
                        output_index=self.text_index,
                        content_index=0,
                        part={"type": "output_text", "text": "", "annotations": [], "logprobs": []},
                    ),
                ]
            )
        self.text += delta.content
        message_index = self.message_output_index
        if message_index is None:
            raise StreamError("Responses text without a message item")
        events.append(
            self._response_event(
                "response.output_text.delta",
                item_id=self.output[message_index]["id"],
                output_index=self.text_index,
                content_index=0,
                delta=delta.text,
                logprobs=probabilities,
            )
        )
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
            part = {"type": "output_text", "text": self.text, "annotations": [], "logprobs": self.probabilities.copy()}
            item.update(status=status, content=[part])
            self.text = ""
            return [
                self._response_event(
                    "response.output_text.done",
                    item_id=item["id"],
                    output_index=index,
                    content_index=0,
                    text=part["text"],
                    logprobs=part["logprobs"],
                ),
                self._response_event(
                    "response.content_part.done", item_id=item["id"], output_index=index, content_index=0, part=part
                ),
                self._response_event("response.output_item.done", output_index=index, item=item.copy()),
            ]
        return []

    def _tool_start(self, delta: Delta) -> list[SSEEvent]:
        kind = delta.tool_type
        arg = "input" if kind == "custom" else "arguments"
        if self.protocol == Protocol.CHAT:
            return [
                self._chat_chunk(
                    [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": delta.index,
                                        "id": delta.call_id,
                                        "type": kind,
                                        kind: {"name": delta.name, arg: ""},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                )
            ]
        if self.protocol == Protocol.MESSAGES:
            if len(self.open_blocks) > 1:
                raise StreamError("Messages cannot interleave multiple open tool blocks")
            events = self._close_text()
            index = self.next_index
            self.next_index += 1
            self.tools[delta.position]["target_index"] = index
            events.append(
                _event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {"type": "tool_use", "id": delta.call_id, "name": delta.name, "input": {}},
                    },
                )
            )
            return events
        events = self._close_text()
        index = self.next_index
        self.next_index += 1
        tool = self.tools[delta.position]
        tool["target_index"] = index
        item = {
            "id": tool["item_id"],
            "type": "custom_tool_call" if kind == "custom" else "function_call",
            "status": "in_progress",
            "call_id": delta.call_id,
            "name": delta.name,
            arg: "",
        }
        if kind == "tool_search":
            item = {
                "id": tool["item_id"],
                "type": "tool_search_call",
                "status": "in_progress",
                "call_id": delta.call_id,
                "execution": "client",
                "arguments": {},
            }
        self.output.append(item)
        if kind == "tool_search":
            # No documented search-argument delta wire. Hold only this bounded
            # call until it is a complete object, not the entire response.
            return events
        events.append(self._response_event("response.output_item.added", output_index=index, item=item.copy()))
        return events

    def _tool_delta(self, delta: Delta) -> list[SSEEvent]:
        tool = self.tools[delta.position]
        custom = tool["tool_type"] == "custom"
        if tool["tool_type"] == "tool_search":
            return []
        if self.protocol == Protocol.CHAT:
            return [
                self._chat_chunk(
                    [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": delta.index,
                                        "custom" if custom else "function": {
                                            "input" if custom else "arguments": delta.text
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                )
            ]
        if self.protocol == Protocol.MESSAGES:
            return [
                _event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": tool["target_index"],
                        "delta": {"type": "input_json_delta", "partial_json": delta.text},
                    },
                )
            ]
        return [
            self._response_event(
                "response.custom_tool_call_input.delta" if custom else "response.function_call_arguments.delta",
                item_id=tool["item_id"],
                output_index=tool["target_index"],
                delta=delta.text,
            )
        ]

    def _tool_end(self, delta: Delta) -> list[SSEEvent]:
        tool = self.tools[delta.position]
        if tool["tool_type"] != "custom":
            try:
                _arguments(tool["arguments"], "tool arguments")
            except ConversionError as exc:
                raise StreamError(str(exc)) from exc
        if self.protocol == Protocol.CHAT:
            return []
        if self.protocol == Protocol.MESSAGES:
            return [_event("content_block_stop", {"type": "content_block_stop", "index": tool["target_index"]})]
        item = self.output[tool["target_index"]]
        if tool["tool_type"] == "tool_search":
            try:
                arguments = json.loads(tool["arguments"])
            except (ValueError, TypeError, RecursionError) as exc:
                raise StreamError("invalid client search arguments") from exc
            if not isinstance(arguments, dict):
                raise StreamError("client search arguments must be an object")
            item.update(status="completed", arguments=arguments)
            return [
                self._response_event(
                    "response.output_item.added", output_index=tool["target_index"], item=copy.deepcopy(item)
                ),
                self._response_event(
                    "response.output_item.done", output_index=tool["target_index"], item=copy.deepcopy(item)
                ),
            ]
        custom = tool["tool_type"] == "custom"
        arg = "input" if custom else "arguments"
        item.update(status="completed", **{arg: tool["arguments"]})
        return [
            self._response_event(
                "response.custom_tool_call_input.done" if custom else "response.function_call_arguments.done",
                item_id=tool["item_id"],
                output_index=tool["target_index"],
                **{arg: tool["arguments"]},
            ),
            self._response_event("response.output_item.done", output_index=tool["target_index"], item=item.copy()),
        ]

    def _finish(self, delta: Delta) -> list[SSEEvent]:
        from .logprobs import align

        try:
            align(self.probabilities, self.probability_text, required=self.text_logprobs)
            self.service_tier = _reported_service_tier(delta.service_tier)
        except ConversionError as exc:
            raise StreamError(str(exc)) from exc
        if self.protocol == Protocol.CHAT:
            events = [self._chat_chunk([{"index": 0, "delta": {}, "finish_reason": delta.reason}])]
            if delta.input_tokens is not None and delta.output_tokens is not None:
                events.append(self._chat_chunk([], _encode_usage(delta, Protocol.CHAT)))
            events.append(SSEEvent("message", "[DONE]"))
            return events
        if self.protocol == Protocol.MESSAGES:
            if delta.input_tokens is None or delta.output_tokens is None:
                raise StreamError("Messages final event requires real input_tokens and output_tokens")
            events = self._close_text()
            reason = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}[delta.reason or ""]
            events.extend(
                [
                    _event(
                        "message_delta",
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": reason, "stop_sequence": None},
                            "usage": _encode_usage(delta, Protocol.MESSAGES),
                        },
                    ),
                    _event("message_stop", {"type": "message_stop"}),
                ]
            )
            return events
        if delta.input_tokens is None or delta.output_tokens is None:
            raise StreamError("Responses final event requires input and output token usage")
        incomplete = delta.reason == "length"
        events = self._close_text(status="incomplete" if incomplete else "completed")
        response = self._response_snapshot(
            "incomplete" if incomplete else "completed",
            usage=_encode_usage(delta, Protocol.RESPONSES),
            incomplete_details={"reason": "max_output_tokens"} if incomplete else None,
        )
        return events + [
            self._response_event("response.incomplete" if incomplete else "response.completed", response=response)
        ]
