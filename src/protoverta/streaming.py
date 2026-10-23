"""Strict incremental decoders for text and client-side function-call SSE.

These decoders consume already-framed :class:`SSEEvent` values. They never
interpret an incomplete stream as a completed response. Target-protocol
encoders and HTTP forwarding are implemented separately.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .core import ConversionError, Protocol, _decode_usage, _validate_response_item
from .reasoning import response_reasoning
from .sse import SSEEvent


class StreamError(ValueError):
    """Malformed, unsupported, or prematurely terminated upstream stream."""


@dataclass(frozen=True, slots=True)
class Delta:
    kind: str
    model: str | None = None
    index: int | None = None
    item_id: str | None = None
    call_id: str | None = None
    name: str | None = None
    text: str | None = None
    reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None


def _obj(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StreamError(f"{label} must be an object")
    return value


def _only(value: dict, allowed: set[str], label: str) -> None:
    if set(value) - allowed:
        raise StreamError(f"{label} contains unsupported fields")


def _response_item(item: dict) -> None:
    try:
        _validate_response_item(item, "Responses output item")
    except ConversionError as exc:
        raise StreamError(str(exc)) from exc


def _str(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise StreamError(f"{label} must be a string")
    return value


def _index(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StreamError(f"{label} must be a non-negative integer")
    return value


def _tokens(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _index(value, label)


def _valid_arguments(raw: str, label: str) -> None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StreamError(f"{label} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise StreamError(f"{label} must be a JSON object")


def _json_event(event: SSEEvent, label: str) -> dict[str, Any]:
    try:
        body = json.loads(event.data)
    except json.JSONDecodeError as exc:
        raise StreamError(f"{label} contains invalid JSON") from exc
    return _obj(body, label)


@dataclass(slots=True)
class _Tool:
    call_id: str = ""
    name: str = ""
    item_id: str = ""
    arguments: str = ""
    opened: bool = False
    closed: bool = False


class StreamDecoder:
    """Decode one upstream stream into ordered canonical deltas.

    ``feed`` may be called once per parsed SSE event. ``finish`` must be called
    at EOF; it raises unless the protocol's terminal marker was seen.
    """

    def __init__(self, protocol: Protocol | str, *, public_reasoning: bool = False) -> None:
        self.protocol = Protocol(protocol)
        if not isinstance(public_reasoning, bool):
            raise TypeError("public_reasoning must be a boolean")
        self.public_reasoning = public_reasoning
        self.reasoning_closed = False
        self.reasoning_items: dict[int, str] = {}
        self.reasoning_parts: dict[int, str] = {}
        self.started = False
        self.ended = False
        self.model: str | None = None
        self.reason: str | None = None
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.tools: dict[int, _Tool] = {}
        self.blocks: dict[int, str] = {}
        self.seen_blocks: set[int] = set()
        self.items: dict[int, tuple[str, str]] = {}
        self.seen_items: set[int] = set()
        self.completed_items: dict[int, dict[str, Any]] = {}
        self.incomplete_items = False
        self.text_items: dict[int, str] = {}
        self.text_parts: dict[int, str] = {}
        self.sequence: int | None = None
        self.saw_tool = False
        self.saw_trailing_done = False
        self.raw_usage: dict[str, Any] = {}
        self.usage_details: dict[str, int | None] = {}

    def _usage(self, usage: dict[str, Any]) -> None:
        self.raw_usage.update(usage)
        try:
            values = _decode_usage(self.raw_usage, self.protocol)
        except ConversionError as exc:
            raise StreamError(str(exc)) from exc
        self.input_tokens = values.pop("input_tokens")
        self.output_tokens = values.pop("output_tokens")
        self.usage_details = values

    def feed(self, event: SSEEvent) -> list[Delta]:
        if self.ended:
            if self.protocol == Protocol.RESPONSES and event.event == "message" and event.data == "[DONE]" and not self.saw_trailing_done:
                self.saw_trailing_done = True
                return []
            raise StreamError("event after stream completion")
        if self.protocol == Protocol.CHAT:
            return self._chat(event)
        if self.protocol == Protocol.MESSAGES:
            return self._messages(event)
        return self._responses(event)

    def finish(self) -> None:
        if not self.ended:
            raise StreamError("stream ended before its terminal event")

    def _start(self, model: Any, input_tokens: Any = None, output_tokens: Any = None) -> list[Delta]:
        if self.started:
            raise StreamError("duplicate stream start")
        self.model = _str(model, "model")
        self.input_tokens = _tokens(input_tokens, "input_tokens")
        self.output_tokens = _tokens(output_tokens, "output_tokens")
        self.started = True
        return [Delta("start", model=self.model, input_tokens=self.input_tokens, output_tokens=self.output_tokens, **self.usage_details)]

    def _check_call_id(self, call_id: str, index: int) -> None:
        if not call_id or any(other != index and tool.call_id == call_id for other, tool in self.tools.items()):
            raise StreamError("empty or duplicate stream tool call ID")

    def _complete(self, reason: str) -> list[Delta]:
        if not self.started or self.ended:
            raise StreamError("invalid stream completion")
        if self.blocks:
            raise StreamError("stream completed with open content blocks")
        for tool in self.tools.values():
            if not tool.opened or not tool.closed:
                raise StreamError("stream completed with an unfinished tool call")
            _valid_arguments(tool.arguments or "{}", "tool arguments")
        if reason == "tool_calls" and not self.saw_tool:
            raise StreamError("tool finish reason without tool calls")
        self.ended = True
        return [Delta("finish", reason=reason, input_tokens=self.input_tokens, output_tokens=self.output_tokens, **self.usage_details)]

    def _chat(self, event: SSEEvent) -> list[Delta]:
        if event.data == "[DONE]":
            if self.reason is None:
                raise StreamError("Chat [DONE] arrived before finish_reason")
            for index in sorted(self.tools):
                tool = self.tools[index]
                if not tool.opened:
                    raise StreamError("tool call ended before ID and name were known")
                tool.closed = True
            deltas = [Delta("tool_end", index=i, call_id=t.call_id, item_id=t.item_id) for i, t in sorted(self.tools.items())]
            return deltas + self._complete(self.reason)
        if event.event not in {"message", "chat.completion.chunk"}:
            raise StreamError(f"unsupported Chat SSE event: {event.event}")
        body = _json_event(event, "Chat chunk")
        if body.get("object") != "chat.completion.chunk":
            raise StreamError("invalid Chat chunk object")
        deltas = self._start(body.get("model")) if not self.started else []
        if body.get("model") != self.model:
            raise StreamError("model changed within Chat stream")
        choices = body.get("choices")
        if not isinstance(choices, list) or len(choices) > 1:
            raise StreamError("Chat stream requires zero or one choice")
        if not choices:
            if self.reason is None or self.input_tokens is not None or self.output_tokens is not None:
                raise StreamError("Chat usage chunk is out of order or duplicated")
            usage = _obj(body.get("usage"), "Chat usage")
            self._usage(usage)
            return deltas
        choice = _obj(choices[0], "Chat choice")
        if self.reason is not None:
            # OpenRouter may repeat the finished choice in its usage postlude.
            # Only an empty, unchanged completion with first-time usage qualifies.
            delta = _obj(choice.get("delta"), "Chat usage delta")
            if (choice.get("index") == 0 and not choice.get("logprobs")
                    and choice.get("finish_reason") == self.reason
                    and set(delta) <= {"role", "content"}
                    and delta.get("role") in (None, "assistant") and delta.get("content") in (None, "")
                    and isinstance(body.get("usage"), dict)
                    and self.input_tokens is None and self.output_tokens is None):
                self._usage(body["usage"])
                return deltas
            raise StreamError("Chat choice arrived after finish_reason")
        if choice.get("index") != 0 or choice.get("logprobs"):
            raise StreamError("unsupported Chat choice index or logprobs")
        delta = _obj(choice.get("delta"), "Chat delta")
        for field, value in delta.items():
            if field == "reasoning_content" and self.public_reasoning:
                if value is not None:
                    value = _str(value, "public reasoning_content")
                    if value:
                        if self.reasoning_closed:
                            raise StreamError("interleaved public reasoning cannot be preserved")
                        deltas.append(Delta("reasoning_text", text=value))
                continue
            if field not in {"role", "content", "tool_calls"} and (field not in {"refusal", "annotations", "reasoning", "reasoning_content", "reasoning_details"} or value not in (None, "", [])):
                raise StreamError(f"unsupported Chat delta field: {field}")
        if delta.get("role") not in {None, "assistant"}:
            raise StreamError("Chat delta has a non-assistant role")
        if delta.get("refusal") or delta.get("audio") or delta.get("function_call"):
            raise StreamError("unsupported Chat delta field")
        content = delta.get("content")
        if content is not None and not (self.public_reasoning and content == ""):
            if content:
                self.reasoning_closed = True
            deltas.append(Delta("text", text=_str(content, "Chat content")))
        for partial in delta.get("tool_calls") or []:
            self.reasoning_closed = True
            partial = _obj(partial, "Chat tool delta")
            _only(partial, {"index", "id", "type", "function"}, "Chat tool delta")
            index = _index(partial.get("index"), "Chat tool index")
            tool = self.tools.setdefault(index, _Tool())
            if partial.get("type") not in {None, "function"}:
                raise StreamError("unsupported Chat tool type")
            if "id" in partial:
                if tool.call_id and tool.call_id != partial["id"]:
                    raise StreamError("Chat tool ID changed")
                tool.call_id = _str(partial["id"], "Chat tool ID")
            fn = partial.get("function") or {}
            fn = _obj(fn, "Chat tool function")
            _only(fn, {"name", "arguments"}, "Chat tool function")
            if "name" in fn:
                if tool.name and tool.name != fn["name"]:
                    raise StreamError("Chat tool name changed")
                tool.name = _str(fn["name"], "Chat tool name")
            if not tool.opened and tool.call_id and tool.name:
                self._check_call_id(tool.call_id, index)
                tool.opened = True
                self.saw_tool = True
                deltas.append(Delta("tool_start", index=index, call_id=tool.call_id, name=tool.name))
            if "arguments" in fn:
                arguments = _str(fn["arguments"], "Chat tool arguments")
                tool.arguments += arguments
                if tool.opened:
                    deltas.append(Delta("tool_delta", index=index, call_id=tool.call_id, text=arguments))
                elif arguments:
                    raise StreamError("Chat tool arguments preceded its ID/name")
        reason = choice.get("finish_reason")
        if reason is not None:
            if self.reason is not None:
                raise StreamError("duplicate Chat finish_reason")
            if reason not in {"stop", "tool_calls", "length"}:
                raise StreamError(f"unsupported Chat finish_reason: {reason}")
            self.reason = reason
        return deltas

    def _messages(self, event: SSEEvent) -> list[Delta]:
        if event.event == "ping":
            return []
        body = _json_event(event, "Messages event")
        if body.get("type") != event.event:
            raise StreamError("Messages event/data type mismatch")
        kind = event.event
        if kind == "error":
            raise StreamError("upstream Messages stream reported an error")
        if kind == "message_start":
            message = _obj(body.get("message"), "message_start.message")
            if message.get("content") not in (None, []):
                raise StreamError("message_start contains nonempty content")
            usage = _obj(message.get("usage") or {}, "message_start.usage")
            self._usage(usage)
            return self._start(message.get("model"), self.input_tokens, self.output_tokens)
        if not self.started:
            raise StreamError("Messages block before message_start")
        if self.reason is not None and kind not in {"message_stop", "message_delta"}:
            raise StreamError("Messages content arrived after stop_reason")
        if kind == "content_block_start":
            index = _index(body.get("index"), "content block index")
            if index in self.seen_blocks:
                raise StreamError("duplicate Messages content block index")
            self.seen_blocks.add(index)
            block = _obj(body.get("content_block"), "content_block")
            block_type = block.get("type")
            if block_type == "text":
                _only(block, {"type", "text", "citations"}, "Messages text block")
                if block.get("citations") not in (None, []):
                    raise StreamError("Messages citations cannot be preserved across protocols")
                self.blocks[index] = "text"
                initial = _str(block.get("text", ""), "initial text")
                return [Delta("text", index=index, text=initial)] if initial else []
            if block_type == "tool_use":
                _only(block, {"type", "id", "name", "input", "caller"}, "Messages tool block")
                if "caller" in block and block["caller"] != {"type": "direct"}:
                    raise StreamError("non-direct Messages tool callers cannot be preserved across protocols")
                call_id = _str(block.get("id"), "tool_use.id")
                name = _str(block.get("name"), "tool_use.name")
                self._check_call_id(call_id, index)
                initial = _obj(block.get("input", {}), "tool_use.input")
                if initial:
                    raise StreamError("nonempty initial tool input is unsupported")
                self.blocks[index] = "tool"
                self.tools[index] = _Tool(call_id=call_id, name=name, opened=True)
                self.saw_tool = True
                return [Delta("tool_start", index=index, call_id=call_id, name=name)]
            raise StreamError(f"unsupported Messages content block: {block_type}")
        if kind == "content_block_delta":
            index = _index(body.get("index"), "content block index")
            active = self.blocks.get(index)
            delta = _obj(body.get("delta"), "content_block_delta.delta")
            if active == "text" and delta.get("type") == "text_delta":
                _only(delta, {"type", "text"}, "Messages text delta")
                return [Delta("text", index=index, text=_str(delta.get("text"), "text delta"))]
            if active == "tool" and delta.get("type") == "input_json_delta":
                _only(delta, {"type", "partial_json"}, "Messages tool delta")
                value = _str(delta.get("partial_json"), "partial_json")
                self.tools[index].arguments += value
                return [Delta("tool_delta", index=index, call_id=self.tools[index].call_id, text=value)]
            raise StreamError("Messages delta does not match its open block")
        if kind == "content_block_stop":
            index = _index(body.get("index"), "content block index")
            active = self.blocks.pop(index, None)
            if active is None:
                raise StreamError("Messages block_stop without block_start")
            if active == "tool":
                tool = self.tools[index]
                _valid_arguments(tool.arguments or "{}", "tool arguments")
                tool.closed = True
                return [Delta("tool_end", index=index, call_id=tool.call_id)]
            return []
        if kind == "message_delta":
            if self.reason is not None or self.blocks:
                raise StreamError("Messages message_delta out of order")
            delta = _obj(body.get("delta"), "message_delta.delta")
            reason = delta.get("stop_reason")
            self.reason = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length"}.get(reason)
            if self.reason is None:
                raise StreamError(f"unsupported Messages stop_reason: {reason}")
            usage = _obj(body.get("usage") or {}, "message_delta.usage")
            self._usage(usage)
            return []
        if kind == "message_stop":
            if self.reason is None:
                raise StreamError("Messages message_stop before stop_reason")
            return self._complete(self.reason)
        raise StreamError(f"unsupported Messages event: {kind}")

    def _responses(self, event: SSEEvent) -> list[Delta]:
        body = _json_event(event, "Responses event")
        kind = body.get("type")
        if not isinstance(kind, str) or event.event not in {"message", kind}:
            raise StreamError("Responses event/data type mismatch")
        if kind == "error":
            raise StreamError("upstream Responses stream reported an error")
        sequence = _index(body.get("sequence_number"), "Responses sequence_number")
        if self.sequence is not None and sequence <= self.sequence:
            raise StreamError("Responses sequence_number did not increase")
        self.sequence = sequence
        if kind in {"error", "response.failed"}:
            raise StreamError("upstream Responses stream reported an error")
        if kind == "response.created":
            response = _obj(body.get("response"), "response.created.response")
            return self._start(response.get("model"))
        if not self.started:
            raise StreamError("Responses item before response.created")
        if kind == "response.in_progress":
            return []
        if kind == "response.output_item.added":
            index = _index(body.get("output_index"), "output_index")
            if index in self.seen_items:
                raise StreamError("duplicate Responses output_index")
            self.seen_items.add(index)
            item = _obj(body.get("item"), "output item")
            _response_item(item)
            item_type = item.get("type")
            item_id = _str(item.get("id"), "output item ID")
            if item_type == "reasoning" and self.public_reasoning:
                if self.reasoning_closed or self.reasoning_items:
                    raise StreamError("only one public reasoning prefix item is portable")
                try:
                    response_reasoning(item, initial=True)
                except ConversionError as exc:
                    raise StreamError(str(exc)) from exc
                self.items[index] = ("reasoning", item_id)
                self.reasoning_items[index] = ""
                return []
            if any(kind == "reasoning" for kind, _ in self.items.values()):
                raise StreamError("public reasoning item must close before ordinary output")
            self.reasoning_closed = True
            if item_type == "message" and item.get("role") == "assistant":
                if item.get("content") != []:
                    raise StreamError("Responses message item has nonempty initial content")
                self.items[index] = ("text", item_id)
                self.text_items[index] = ""
                return []
            if item_type == "function_call":
                call_id = _str(item.get("call_id"), "function_call.call_id")
                name = _str(item.get("name"), "function_call.name")
                self._check_call_id(call_id, index)
                self.items[index] = ("tool", item_id)
                self.tools[index] = _Tool(call_id=call_id, name=name, item_id=item_id, opened=True)
                self.saw_tool = True
                return [Delta("tool_start", index=index, item_id=item_id, call_id=call_id, name=name)]
            raise StreamError(f"unsupported Responses output item: {item_type}")
        if kind in {"response.content_part.added", "response.reasoning_text.delta", "response.reasoning_text.done", "response.content_part.done"} and type(body.get("output_index")) is int and self.items.get(body["output_index"], (None,))[0] == "reasoning":
            return self._public_reasoning_event(body, kind)
        if kind == "response.content_part.added":
            index = _index(body.get("output_index"), "output_index")
            if self.items.get(index, (None,))[0] != "text" or body.get("item_id") != self.items[index][1]:
                raise StreamError("Responses content part has wrong item identity")
            if body.get("content_index") != 0 or index in self.text_parts:
                raise StreamError("unsupported Responses content part index")
            part = _obj(body.get("part"), "Responses content part")
            if part.get("type") != "output_text" or part.get("text") != "" or part.get("annotations") != [] or part.get("logprobs") != []:
                raise StreamError("unsupported Responses content part metadata")
            self.text_parts[index] = "open"
            return []
        if kind == "response.output_text.delta":
            index = _index(body.get("output_index"), "output_index")
            if self.items.get(index, (None,))[0] != "text" or self.text_parts.get(index) != "open":
                raise StreamError("Responses text delta without a message item")
            if body.get("item_id") != self.items[index][1] or body.get("content_index") != 0 or body.get("logprobs") != []:
                raise StreamError("unsupported Responses text delta metadata")
            value = _str(body.get("delta"), "text delta")
            self.text_items[index] += value
            return [Delta("text", index=index, item_id=self.items[index][1], text=value)]
        if kind == "response.function_call_arguments.delta":
            index = _index(body.get("output_index"), "output_index")
            if self.items.get(index, (None,))[0] != "tool":
                raise StreamError("Responses tool delta without a function_call item")
            if body.get("item_id") != self.items[index][1]:
                raise StreamError("Responses tool delta has wrong item ID")
            delta = _str(body.get("delta"), "function arguments delta")
            tool = self.tools[index]
            tool.arguments += delta
            return [Delta("tool_delta", index=index, item_id=tool.item_id, call_id=tool.call_id, text=delta)]
        if kind == "response.output_text.done":
            index = _index(body.get("output_index"), "output_index")
            if self.items.get(index, (None,))[0] != "text" or self.text_parts.get(index) != "open" or body.get("text") != self.text_items[index]:
                raise StreamError("Responses completed text differs from deltas")
            if body.get("item_id") != self.items[index][1] or body.get("content_index") != 0 or body.get("logprobs") != []:
                raise StreamError("unsupported Responses completed text metadata")
            self.text_parts[index] = "text_done"
            return []
        if kind == "response.function_call_arguments.done":
            index = _index(body.get("output_index"), "output_index")
            if self.items.get(index, (None,))[0] != "tool" or body.get("arguments") != self.tools[index].arguments:
                raise StreamError("Responses completed tool arguments differ from deltas")
            if body.get("item_id") != self.items[index][1]:
                raise StreamError("Responses completed tool arguments have wrong item ID")
            return []
        if kind == "response.content_part.done":
            index = _index(body.get("output_index"), "output_index")
            if self.items.get(index, (None,))[0] != "text" or self.text_parts.get(index) != "text_done":
                raise StreamError("Responses content part closed out of order")
            if body.get("item_id") != self.items[index][1] or body.get("content_index") != 0:
                raise StreamError("Responses completed content part has wrong identity")
            part = _obj(body.get("part"), "Responses completed content part")
            if part.get("type") != "output_text" or part.get("text") != self.text_items[index] or part.get("annotations") != [] or part.get("logprobs") != []:
                raise StreamError("unsupported Responses completed content metadata")
            self.text_parts[index] = "closed"
            return []
        if kind == "response.output_item.done":
            index = _index(body.get("output_index"), "output_index")
            item_type, item_id = self.items.pop(index, (None, None))
            if item_type is None:
                raise StreamError("Responses output_item.done without added")
            item = _obj(body.get("item"), "completed output item")
            _response_item(item)
            if item.get("id") != item_id or item.get("status") not in {"completed", "incomplete"}:
                raise StreamError("Responses output item identity/status changed")
            self.incomplete_items |= item.get("status") == "incomplete"
            if item_type == "reasoning":
                try:
                    text = response_reasoning(item)
                except ConversionError as exc:
                    raise StreamError(str(exc)) from exc
                if self.reasoning_parts.get(index) != "closed" or text != self.reasoning_items[index]:
                    raise StreamError("completed public reasoning differs from its deltas")
                self.completed_items[index] = item
                return []
            if item_type == "tool":
                tool = self.tools[index]
                if item.get("call_id") != tool.call_id or item.get("name") != tool.name:
                    raise StreamError("Responses tool identity changed")
                if item.get("arguments") != tool.arguments:
                    raise StreamError("Responses tool argument deltas differ from completed item")
                _valid_arguments(tool.arguments, "tool arguments")
                tool.closed = True
                self.completed_items[index] = item
                return [Delta("tool_end", index=index, item_id=item_id, call_id=tool.call_id)]
            if item.get("type") != "message" or item.get("role") != "assistant":
                raise StreamError("Responses message item changed type or role")
            if self.text_parts.get(index) != "closed":
                raise StreamError("Responses message item closed before its content part")
            content = item.get("content")
            if not isinstance(content, list) or len(content) != 1:
                raise StreamError("unsupported Responses completed message content")
            part = _obj(content[0], "Responses completed message content")
            if part.get("type") != "output_text" or part.get("text") != self.text_items[index] or part.get("annotations") != [] or part.get("logprobs") != []:
                raise StreamError("Responses completed message content differs from deltas")
            self.completed_items[index] = item
            return []
        if kind in {"response.completed", "response.incomplete"}:
            if self.items:
                raise StreamError("Responses finished with open output items")
            response = _obj(body.get("response"), "final response")
            if response.get("model") != self.model:
                raise StreamError("Responses model changed")
            output = response.get("output")
            if not isinstance(output, list) or len(output) != len(self.completed_items):
                raise StreamError("Responses final output differs from streamed items")
            for index, item in enumerate(output):
                if item != self.completed_items.get(index):
                    raise StreamError("Responses final output differs from streamed items")
            status = response.get("status")
            if kind == "response.completed" and status == "completed":
                reason = "tool_calls" if self.saw_tool else "stop"
            elif kind == "response.incomplete" and status == "incomplete" and _obj(response.get("incomplete_details"), "incomplete_details").get("reason") == "max_output_tokens":
                reason = "length"
            else:
                raise StreamError("unsupported Responses final status")
            if self.incomplete_items and reason != "length":
                raise StreamError("incomplete Responses item in completed response")
            usage = _obj(response.get("usage") or {}, "Responses usage")
            self._usage(usage)
            return self._complete(reason)
        raise StreamError(f"unsupported Responses event: {kind}")

    def _public_reasoning_event(self, body: dict, kind: str) -> list[Delta]:
        index = _index(body.get("output_index"), "output_index")
        if body.get("item_id") != self.items[index][1] or type(body.get("content_index")) is not int or body["content_index"] != 0:
            raise StreamError("public reasoning content has wrong identity")
        state = self.reasoning_parts.get(index)
        if kind == "response.content_part.added":
            if state is not None or body.get("part") != {"type": "reasoning_text", "text": ""}:
                raise StreamError("invalid initial public reasoning part")
            self.reasoning_parts[index] = "open"
            return []
        if kind == "response.reasoning_text.delta":
            if state != "open":
                raise StreamError("public reasoning delta without an open part")
            text = _str(body.get("delta"), "public reasoning delta")
            self.reasoning_items[index] += text
            return [Delta("reasoning_text", text=text)]
        if kind == "response.reasoning_text.done":
            if state != "open" or body.get("text") != self.reasoning_items[index]:
                raise StreamError("completed public reasoning text differs from deltas")
            self.reasoning_parts[index] = "text_done"
            return []
        if state != "text_done" or body.get("part") != {"type": "reasoning_text", "text": self.reasoning_items[index]}:
            raise StreamError("public reasoning part closed out of order or changed")
        self.reasoning_parts[index] = "closed"
        return []
