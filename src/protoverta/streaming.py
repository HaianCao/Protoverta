"""Strict incremental decoders for text and client-side function-call SSE.

These decoders consume already-framed :class:`SSEEvent` values. They never
interpret an incomplete stream as a completed response. Target-protocol
encoders and HTTP forwarding are implemented separately.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, TypedDict

from .core import (
    ConversionError,
    Protocol,
    _arguments,
    _decode_usage,
    _drop_null_legacy,
    _reported_service_tier,
    _validate_response_item,
)
from .errors import UnsupportedFeatureError
from .json_wire import decode_json
from .reasoning import response_reasoning
from .sse import SSEEvent


class StreamError(ValueError):
    """Malformed, unsupported, or prematurely terminated upstream stream."""


class UnsupportedStreamFeatureError(StreamError):
    """Known, unrepresentable semantics; not a malformed upstream event."""

    def __init__(self, feature: str):
        error = UnsupportedFeatureError(feature)
        self.feature = error.feature
        super().__init__(str(error))


class _UsageDetails(TypedDict, total=False):
    cached_input_tokens: int | None
    cache_creation_input_tokens: int | None
    reasoning_output_tokens: int | None


class UnsupportedReasoningError(StreamError):
    """A known reasoning feature has no enabled, lossless target mapping."""

    def __init__(self) -> None:
        # Fixed text only: upstream field values must not enter client errors.
        super().__init__(
            "unsupported reasoning output (reasoning_content/thinking/opaque state) under the current strict policy"
        )


class UpstreamReportedError(StreamError):
    """The provider itself reported an API error inside a translated stream.

    Some servers answer HTTP 200 and then send an error object as the first or
    a later SSE event. Its bounded message is surfaced so clients see the real
    cause instead of a generic malformed-stream failure.
    """

    def __init__(self, detail: Any, *, status: Any = None, kind: Any = None) -> None:
        text = detail if isinstance(detail, str) else "upstream provider reported an error"
        text = (
            "".join(char if char.isprintable() else " " for char in text).strip()[:500]
            or "upstream provider reported an error"
        )
        self.detail = text
        self.kind = kind if isinstance(kind, str) and kind.isascii() and len(kind) <= 64 else None
        self.status = status if type(status) is int and 400 <= status <= 599 else None
        super().__init__("upstream provider reported an error: " + text)


def _reported_error(body: dict[str, Any], *, status: Any = None) -> UpstreamReportedError:
    error: dict[str, Any] = body["error"] if isinstance(body.get("error"), dict) else body
    code = error.get("code")
    return UpstreamReportedError(
        error.get("message"),
        status=status if status is not None else code if type(code) is int else None,
        kind=error.get("type") or code,
    )


@dataclass(frozen=True, slots=True)
class Delta:
    """One portable stream event decoded from any protocol.

    ``kind`` names the event (for example ``start``, ``text``, ``tool_start``,
    ``tool_arguments``, ``finish``); the other fields are set as that kind needs.
    Token counts are only the provider's own numbers.
    """

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
    tool_type: str = "function"
    service_tier: str | None = None
    logprobs: list[dict[str, Any]] | None = None

    @property
    def position(self) -> int:
        """``index``, which block and tool events always carry."""
        if self.index is None:
            raise StreamError(f"{self.kind} event without an index")
        return self.index

    @property
    def call_name(self) -> str:
        """``name``, which tool-start events always carry."""
        if self.name is None:
            raise StreamError(f"{self.kind} event without a tool name")
        return self.name

    @property
    def content(self) -> str:
        """``text``, which text, reasoning and argument events always carry."""
        if self.text is None:
            raise StreamError(f"{self.kind} event without text")
        return self.text


#: Rewrites one decoded delta into one or more deltas (gateway stream hooks).
DeltaTransform = Callable[[Delta], Delta | list[Delta]]


def _obj(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StreamError(f"{label} must be an object")
    return value


def _only(value: dict[str, Any], allowed: set[str], label: str) -> None:
    if set(value) - allowed:
        raise StreamError(f"{label} contains unsupported fields")


def _response_item(item: dict[str, Any]) -> None:
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
        _arguments(raw, label)
    except ConversionError as exc:
        raise StreamError(str(exc)) from exc


def _json_event(event: SSEEvent, label: str) -> dict[str, Any]:
    try:
        body = decode_json(event.data)
    except ConversionError as exc:
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
    tool_type: str = "function"
    input_done: bool = False


class StreamDecoder:
    """Decode one upstream stream into ordered canonical deltas.

    ``feed`` may be called once per parsed SSE event. ``finish`` must be called
    at EOF; it raises unless the protocol's terminal marker was seen.
    """

    def __init__(
        self,
        protocol: Protocol | str,
        *,
        public_reasoning: bool = False,
        text_logprobs: bool = False,
        max_top_logprobs: int | None = None,
        omit_reasoning: bool = False,
    ) -> None:
        self.protocol = Protocol(protocol)
        if not isinstance(public_reasoning, bool):
            raise TypeError("public_reasoning must be a boolean")
        if type(omit_reasoning) is not bool:
            raise TypeError("omit_reasoning must be a boolean")
        self.public_reasoning = public_reasoning
        # Explicit policy: reasoning the client did not ask for (and that this
        # route cannot represent) is dropped instead of failing the request.
        # Nothing is fabricated or forwarded; ``omitted_reasoning`` records it.
        self.omit_reasoning = omit_reasoning
        self.omitted_reasoning = False
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
        self.usage_details: _UsageDetails = {}
        self.service_tier: str | None = None
        if type(text_logprobs) is not bool or text_logprobs and self.protocol == Protocol.MESSAGES:
            raise ValueError("text_logprobs requires a Chat/Responses stream")
        self.text_logprobs = text_logprobs
        self.max_top_logprobs = max_top_logprobs
        self.probability_text = ""
        self.probabilities: list[dict[str, Any]] = []
        self.item_probabilities: dict[int, list[dict[str, Any]]] = {}

    def _probabilities(self, value: Any, *, chat: bool = False) -> list[dict[str, Any]] | None:
        from .logprobs import chat_records, records

        try:
            return chat_records(value, self.max_top_logprobs) if chat else records(value, self.max_top_logprobs)
        except ConversionError as exc:
            raise StreamError(str(exc)) from exc

    def _text_logprobs(self, container: dict[str, Any]) -> list[dict[str, Any]]:
        """Responses text logprobs. Some compatible servers omit the field when
        logprobs were not requested; that carries no data, so it reads as an
        empty list. When logprobs were requested, omission is still an error."""
        if "logprobs" not in container:
            if self.text_logprobs:
                raise StreamError("Responses text omitted the requested logprobs")
            return []
        # Non-Chat records are always a list (possibly empty).
        return self._probabilities(container.get("logprobs")) or []

    def _tier(self, value: Any) -> None:
        try:
            reported = _reported_service_tier(value)
        except ConversionError as exc:
            raise StreamError(str(exc)) from exc
        if reported is not None:
            if self.service_tier is not None and reported != self.service_tier:
                raise StreamError("upstream service_tier changed within a Chat stream")
            self.service_tier = reported

    def _usage(self, usage: dict[str, Any]) -> None:
        self.raw_usage.update(usage)
        try:
            values = _decode_usage(self.raw_usage, self.protocol)
        except ConversionError as exc:
            raise StreamError(str(exc)) from exc
        self.input_tokens = values["input_tokens"]
        self.output_tokens = values["output_tokens"]
        self.usage_details = {
            "cached_input_tokens": values["cached_input_tokens"],
            "cache_creation_input_tokens": values["cache_creation_input_tokens"],
            "reasoning_output_tokens": values["reasoning_output_tokens"],
        }

    def feed(self, event: SSEEvent) -> list[Delta]:
        if self.ended:
            if (
                self.protocol == Protocol.RESPONSES
                and event.event == "message"
                and event.data == "[DONE]"
                and not self.saw_trailing_done
            ):
                self.saw_trailing_done = True
                return []
            raise StreamError("event after stream completion")
        return self._PROTOCOL_EVENTS[self.protocol](self, event)

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
        return [
            Delta(
                "start",
                model=self.model,
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                **self.usage_details,
            )
        ]

    def _check_call_id(self, call_id: str, index: int) -> None:
        if not call_id or any(other != index and tool.call_id == call_id for other, tool in self.tools.items()):
            raise StreamError("empty or duplicate stream tool call ID")

    def _complete(self, reason: str) -> list[Delta]:
        from .logprobs import align

        try:
            align(self.probabilities, self.probability_text, required=self.text_logprobs)
        except ConversionError as exc:
            raise StreamError(str(exc)) from exc
        if not self.started or self.ended:
            raise StreamError("invalid stream completion")
        if self.blocks:
            raise StreamError("stream completed with open content blocks")
        for tool in self.tools.values():
            if not tool.opened or not tool.closed:
                raise StreamError("stream completed with an unfinished tool call")
            if tool.tool_type == "function":
                _valid_arguments(tool.arguments, "tool arguments")
        if reason != "length" and (reason == "tool_calls") != self.saw_tool:
            raise StreamError("finish reason disagrees with tool calls")
        self.ended = True
        return [
            Delta(
                "finish",
                reason=reason,
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                service_tier=self.service_tier,
                **self.usage_details,
            )
        ]

    def _chat(self, event: SSEEvent) -> list[Delta]:
        if event.data == "[DONE]":
            if self.reason is None:
                raise StreamError("Chat [DONE] arrived before finish_reason")
            for index in sorted(self.tools):
                tool = self.tools[index]
                if not tool.opened:
                    raise StreamError("tool call ended before ID and name were known")
                tool.closed = True
            deltas = [
                Delta("tool_end", index=i, call_id=t.call_id, item_id=t.item_id) for i, t in sorted(self.tools.items())
            ]
            return deltas + self._complete(self.reason)
        if event.event not in {"message", "chat.completion.chunk"}:
            raise StreamError(f"unsupported Chat SSE event: {event.event}")
        body = _json_event(event, "Chat chunk")
        if "error" in body and "choices" not in body:
            raise _reported_error(body)
        if body.get("object") != "chat.completion.chunk":
            raise StreamError("invalid Chat chunk object")
        self._tier(body.get("service_tier"))
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
        probabilities = self._probabilities(choice.get("logprobs"), chat=True) if self.text_logprobs else None
        if _index(choice.get("index"), "Chat choice index") != 0:
            raise StreamError("unsupported Chat choice index")
        if self.reason is not None:
            # OpenRouter may repeat the finished choice in its usage postlude.
            # Only an empty, unchanged completion with first-time usage qualifies.
            delta = _obj(choice.get("delta"), "Chat usage delta")
            if (
                choice.get("index") == 0
                and not choice.get("logprobs")
                and choice.get("finish_reason") == self.reason
                and set(delta) <= {"role", "content"}
                and delta.get("role") in (None, "assistant")
                and delta.get("content") in (None, "")
                and isinstance(body.get("usage"), dict)
                and self.input_tokens is None
                and self.output_tokens is None
            ):
                self._usage(body["usage"])
                return deltas
            raise StreamError("Chat choice arrived after finish_reason")
        if choice.get("index") != 0 or choice.get("logprobs") and not self.text_logprobs:
            raise StreamError("unsupported Chat choice index or logprobs")
        delta = _drop_null_legacy(_obj(choice.get("delta"), "Chat delta"))
        for field, value in delta.items():
            # OpenRouter streams unsigned public reasoning as ``reasoning``;
            # other servers use ``reasoning_content``. Never accept both.
            public_field = field == "reasoning_content" or field == "reasoning" and "reasoning_content" not in delta
            if public_field and self.public_reasoning:
                if value is not None:
                    value = _str(value, "public reasoning_content")
                    if value:
                        if self.reasoning_closed:
                            raise StreamError("interleaved public reasoning cannot be preserved")
                        deltas.append(Delta("reasoning_text", text=value))
                continue
            if field in {"reasoning", "reasoning_content", "reasoning_details"} and value not in (None, "", []):
                if not self.omit_reasoning:
                    raise UnsupportedReasoningError()
                if not isinstance(value, str | list):
                    raise StreamError(f"Chat {field} must be a string or array")
                self.omitted_reasoning = True
                continue
            if field == "refusal":
                if value is not None and not isinstance(value, str):
                    raise StreamError("Chat refusal must be a string or null")
                if value:
                    raise UnsupportedStreamFeatureError("refusal")
                continue
            if field not in {"role", "content", "tool_calls"} and (
                field not in {"refusal", "annotations", "reasoning", "reasoning_content", "reasoning_details"}
                or value not in (None, "", [])
            ):
                raise StreamError(f"unsupported Chat delta field: {field}")
        if delta.get("role") not in (None, "assistant"):
            raise StreamError("Chat delta has a non-assistant role")
        if delta.get("refusal") is not None and not isinstance(delta["refusal"], str):
            raise StreamError("Chat refusal must be a string or null")
        if delta.get("refusal"):
            raise UnsupportedStreamFeatureError("refusal")
        if delta.get("audio") or delta.get("function_call"):
            raise StreamError("unsupported Chat delta field")
        content = delta.get("content")
        if content is not None and not (self.public_reasoning and content == "" and not probabilities) or probabilities:
            if content:
                self.reasoning_closed = True
            value = _str(content if content is not None else "", "Chat content")
            if self.text_logprobs:
                self.probability_text += value
                self.probabilities.extend(probabilities or [])
            deltas.append(Delta("text", text=value, logprobs=probabilities))
        calls = delta.get("tool_calls")
        if calls is not None and not isinstance(calls, list):
            raise StreamError("Chat tool_calls must be an array or null")
        for partial in calls if calls is not None else []:
            self.reasoning_closed = True
            partial = _obj(partial, "Chat tool delta")
            index = _index(partial.get("index"), "Chat tool index")
            tool = self.tools.setdefault(index, _Tool())
            kind = partial.get("type", "function")
            if kind != "function":
                raise StreamError("unsupported Chat tool type")
            _only(partial, {"index", "id", "type", kind}, "Chat tool delta")
            if (tool.opened or tool.call_id or tool.name or tool.arguments) and tool.tool_type != kind:
                raise StreamError("Chat tool type changed")
            tool.tool_type = kind
            if "id" in partial:
                if tool.call_id and tool.call_id != partial["id"]:
                    raise StreamError("Chat tool ID changed")
                tool.call_id = _str(partial["id"], "Chat tool ID")
            fn = partial.get(kind) or {}
            fn = _obj(fn, "Chat tool function")
            arg = "input" if kind == "custom" else "arguments"
            _only(fn, {"name", arg}, "Chat tool function")
            if "name" in fn:
                if tool.name and tool.name != fn["name"]:
                    raise StreamError("Chat tool name changed")
                tool.name = _str(fn["name"], "Chat tool name")
            if not tool.opened and tool.call_id and tool.name:
                self._check_call_id(tool.call_id, index)
                tool.opened = True
                self.saw_tool = True
                deltas.append(Delta("tool_start", index=index, call_id=tool.call_id, name=tool.name, tool_type=kind))
            if arg in fn:
                arguments = _str(fn[arg], "Chat tool input")
                tool.arguments += arguments
                if tool.opened:
                    deltas.append(Delta("tool_delta", index=index, call_id=tool.call_id, text=arguments))
                elif arguments:
                    raise StreamError("Chat tool arguments preceded its ID/name")
        reason = choice.get("finish_reason")
        if reason is not None:
            if reason == "content_filter":
                raise UnsupportedStreamFeatureError("content_filter")
            if self.reason is not None:
                raise StreamError("duplicate Chat finish_reason")
            if reason not in ("stop", "tool_calls", "length"):
                raise StreamError(f"unsupported Chat finish_reason: {reason}")
            self.reason = reason
        if body.get("usage") is not None:
            if reason is None:
                raise StreamError("Chat usage requires a finishing choice or a usage postlude")
            self._usage(_obj(body["usage"], "Chat usage"))
        return deltas

    def _messages(self, event: SSEEvent) -> list[Delta]:
        if event.event == "ping":
            return []
        body = _json_event(event, "Messages event")
        if body.get("type") != event.event:
            raise StreamError("Messages event/data type mismatch")
        kind = event.event
        if kind == "error":
            raise _reported_error(body)
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
            if block_type in ("thinking", "redacted_thinking"):
                if not self.omit_reasoning:
                    raise UnsupportedReasoningError()
                self.blocks[index] = "omitted"
                self.omitted_reasoning = True
                return []
            if block_type == "compaction":
                raise UnsupportedStreamFeatureError("compaction")
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
                initial_input = _obj(block.get("input"), "tool_use.input")
                if initial_input:
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
            if active == "omitted" and delta.get("type") in ("thinking_delta", "signature_delta"):
                return []
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
                normalized = []
                if tool.arguments == "":
                    # Messages explicitly began with input={}; no JSON tokens
                    # means that empty object, not an empty argument string.
                    tool.arguments = "{}"
                    normalized.append(Delta("tool_delta", index=index, call_id=tool.call_id, text="{}"))
                _valid_arguments(tool.arguments, "tool arguments")
                tool.closed = True
                return normalized + [Delta("tool_end", index=index, call_id=tool.call_id)]
            return []
        if kind == "message_delta":
            if self.reason is not None or self.blocks:
                raise StreamError("Messages message_delta out of order")
            delta = _obj(body.get("delta"), "message_delta.delta")
            reason = _str(delta.get("stop_reason"), "Messages stop_reason")
            if reason in ("refusal", "stop_sequence", "pause_turn"):
                raise UnsupportedStreamFeatureError(reason)
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
            raise _reported_error(body)
        if (
            kind == "response.failed"
            and isinstance(body.get("response"), dict)
            and isinstance(body["response"].get("error"), dict)
        ):
            raise _reported_error(body["response"]["error"])
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
        if (
            kind != "response.output_item.done"
            and kind.startswith(("response.reasoning", "response.content_part."))
            and type(body.get("output_index")) is int
            and self.items.get(body["output_index"], (None,))[0] == "omitted"
        ):
            if body.get("item_id") != self.items[body["output_index"]][1]:
                raise StreamError("omitted reasoning event has wrong item identity")
            return []
        if (
            kind
            in {
                "response.content_part.added",
                "response.reasoning_text.delta",
                "response.reasoning_text.done",
                "response.content_part.done",
            }
            and type(body.get("output_index")) is int
            and self.items.get(body["output_index"], (None,))[0] == "reasoning"
        ):
            return self._public_reasoning_event(body, kind)
        handler = self._RESPONSES_EVENTS.get(kind)
        if handler is None:
            raise StreamError(f"unsupported Responses event: {kind}")
        return handler(self, body, kind)

    def _on_output_item_added(self, body: dict[str, Any], kind: str) -> list[Delta]:
        index = _index(body.get("output_index"), "output_index")
        if index in self.seen_items:
            raise StreamError("duplicate Responses output_index")
        self.seen_items.add(index)
        item = _obj(body.get("item"), "output item")
        _response_item(item)
        item_type = item.get("type")
        if item_type == "compaction":
            raise UnsupportedStreamFeatureError("compaction")
        item_id = _str(item.get("id"), "output item ID")
        if item_type == "reasoning" and not self.public_reasoning and self.omit_reasoning:
            self.items[index] = ("omitted", item_id)
            self.omitted_reasoning = True
            return []
        if item_type == "reasoning" and not self.public_reasoning:
            raise UnsupportedReasoningError()
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
        if item_type in ("function_call", "custom_tool_call"):
            custom = item_type == "custom_tool_call"
            if item.get("input" if custom else "arguments", "") != "":
                raise StreamError("Responses tool item has nonempty initial input")
            call_id = _str(item.get("call_id"), "function_call.call_id")
            name = _str(item.get("name"), "function_call.name")
            self._check_call_id(call_id, index)
            self.items[index] = ("tool", item_id)
            self.tools[index] = _Tool(
                call_id=call_id,
                name=name,
                item_id=item_id,
                opened=True,
                tool_type="custom" if custom else "function",
            )
            self.saw_tool = True
            return [
                Delta(
                    "tool_start",
                    index=index,
                    item_id=item_id,
                    call_id=call_id,
                    name=name,
                    tool_type="custom" if custom else "function",
                )
            ]
        raise StreamError(f"unsupported Responses output item: {item_type}")

    def _on_content_part_added(self, body: dict[str, Any], kind: str) -> list[Delta]:
        index = _index(body.get("output_index"), "output_index")
        if self.items.get(index, (None,))[0] != "text" or body.get("item_id") != self.items[index][1]:
            raise StreamError("Responses content part has wrong item identity")
        if body.get("content_index") != 0 or index in self.text_parts:
            raise StreamError("unsupported Responses content part index")
        part = _obj(body.get("part"), "Responses content part")
        if part.get("type") == "refusal":
            _only(part, {"type", "refusal"}, "Responses refusal part")
            _str(part.get("refusal"), "refusal")
            raise UnsupportedStreamFeatureError("refusal")
        if (
            part.get("type") != "output_text"
            or part.get("text") != ""
            or part.get("annotations") != []
            or part.get("logprobs", []) != []
        ):
            raise StreamError("unsupported Responses content part metadata")
        self.text_parts[index] = "open"
        self.item_probabilities[index] = []
        return []

    def _on_output_text_delta(self, body: dict[str, Any], kind: str) -> list[Delta]:
        index = _index(body.get("output_index"), "output_index")
        if self.items.get(index, (None,))[0] != "text" or self.text_parts.get(index) != "open":
            raise StreamError("Responses text delta without a message item")
        if (
            body.get("item_id") != self.items[index][1]
            or body.get("content_index") != 0
            or not self.text_logprobs
            and self._text_logprobs(body) != []
        ):
            raise StreamError("unsupported Responses text delta metadata")
        value = _str(body.get("delta"), "text delta")
        probabilities = self._text_logprobs(body) if self.text_logprobs else []
        self.text_items[index] += value
        self.item_probabilities[index].extend(probabilities)
        if self.text_logprobs:
            self.probabilities.extend(probabilities)
            self.probability_text += value
        return [Delta("text", index=index, item_id=self.items[index][1], text=value, logprobs=probabilities or None)]

    def _on_tool_input_delta(self, body: dict[str, Any], kind: str) -> list[Delta]:
        index = _index(body.get("output_index"), "output_index")
        if self.items.get(index, (None,))[0] != "tool":
            raise StreamError("Responses tool delta without a function_call item")
        if body.get("item_id") != self.items[index][1]:
            raise StreamError("Responses tool delta has wrong item ID")
        delta = _str(body.get("delta"), "function arguments delta")
        tool = self.tools[index]
        if (tool.tool_type == "custom") != (kind == "response.custom_tool_call_input.delta"):
            raise StreamError("Responses tool delta type differs from item")
        if tool.closed or tool.input_done:
            raise StreamError("Responses input delta after tool completion")
        tool.arguments += delta
        return [Delta("tool_delta", index=index, item_id=tool.item_id, call_id=tool.call_id, text=delta)]

    def _on_output_text_done(self, body: dict[str, Any], kind: str) -> list[Delta]:
        index = _index(body.get("output_index"), "output_index")
        if (
            self.items.get(index, (None,))[0] != "text"
            or self.text_parts.get(index) != "open"
            or body.get("text") != self.text_items[index]
        ):
            raise StreamError("Responses completed text differs from deltas")
        if (
            body.get("item_id") != self.items[index][1]
            or body.get("content_index") != 0
            or self._text_logprobs(body) != self.item_probabilities[index]
        ):
            raise StreamError("unsupported Responses completed text metadata")
        from .logprobs import align

        try:
            align(self.item_probabilities[index], self.text_items[index], required=self.text_logprobs)
        except ConversionError as exc:
            raise StreamError(str(exc)) from exc
        self.text_parts[index] = "text_done"
        return []

    def _on_tool_input_done(self, body: dict[str, Any], kind: str) -> list[Delta]:
        index = _index(body.get("output_index"), "output_index")
        custom = kind == "response.custom_tool_call_input.done"
        if (
            self.items.get(index, (None,))[0] != "tool"
            or body.get("input" if custom else "arguments") != self.tools[index].arguments
        ):
            raise StreamError("Responses completed tool arguments differ from deltas")
        if custom != (self.tools[index].tool_type == "custom"):
            raise StreamError("Responses completed input type differs from item")
        if self.tools[index].input_done:
            raise StreamError("duplicate Responses completed tool input")
        self.tools[index].input_done = True
        if body.get("item_id") != self.items[index][1]:
            raise StreamError("Responses completed tool arguments have wrong item ID")
        return []

    def _on_content_part_done(self, body: dict[str, Any], kind: str) -> list[Delta]:
        index = _index(body.get("output_index"), "output_index")
        if self.items.get(index, (None,))[0] != "text" or self.text_parts.get(index) != "text_done":
            raise StreamError("Responses content part closed out of order")
        if body.get("item_id") != self.items[index][1] or body.get("content_index") != 0:
            raise StreamError("Responses completed content part has wrong identity")
        part = _obj(body.get("part"), "Responses completed content part")
        if (
            part.get("type") != "output_text"
            or part.get("text") != self.text_items[index]
            or part.get("annotations") != []
            or self._text_logprobs(part) != self.item_probabilities[index]
        ):
            raise StreamError("unsupported Responses completed content metadata")
        self.text_parts[index] = "closed"
        return []

    def _on_output_item_done(self, body: dict[str, Any], kind: str) -> list[Delta]:
        index = _index(body.get("output_index"), "output_index")
        added = self.items.pop(index, None)
        if added is None:
            raise StreamError("Responses output_item.done without added")
        item_type, item_id = added
        item = _obj(body.get("item"), "completed output item")
        _response_item(item)
        if item.get("id") != item_id or item.get("status") not in ("completed", "incomplete"):
            raise StreamError("Responses output item identity/status changed")
        if item_type == "omitted":
            if item.get("type") != "reasoning":
                raise StreamError("omitted reasoning item changed type")
            self.completed_items[index] = item
            return []
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
            custom = tool.tool_type == "custom"
            if item.get("type") != ("custom_tool_call" if custom else "function_call"):
                raise StreamError("Responses tool item changed type")
            if item.get("call_id") != tool.call_id or item.get("name") != tool.name:
                raise StreamError("Responses tool identity changed")
            if item.get("input" if custom else "arguments") != tool.arguments:
                raise StreamError("Responses tool argument deltas differ from completed item")
            if not custom:
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
        if (
            part.get("type") != "output_text"
            or part.get("text") != self.text_items[index]
            or part.get("annotations") != []
            or self._text_logprobs(part) != self.item_probabilities[index]
        ):
            raise StreamError("Responses completed message content differs from deltas")
        self.completed_items[index] = item
        return []

    def _on_finished(self, body: dict[str, Any], kind: str) -> list[Delta]:
        if self.items:
            raise StreamError("Responses finished with open output items")
        response = _obj(body.get("response"), "final response")
        if response.get("model") != self.model:
            raise StreamError("Responses model changed")
        output = response.get("output")
        if not isinstance(output, list) or len(output) != len(self.completed_items):
            raise StreamError("Responses final output differs from streamed items")
        for index, item in enumerate(output):
            if isinstance(item, dict) and item.get("type") == "message" and isinstance(item.get("content"), list):
                for part in item["content"]:
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        self._text_logprobs(part)
            if item != self.completed_items.get(index):
                raise StreamError("Responses final output differs from streamed items")
        status = response.get("status")
        if (
            kind == "response.incomplete"
            and status == "incomplete"
            and _obj(response.get("incomplete_details"), "incomplete_details").get("reason") == "content_filter"
        ):
            raise UnsupportedStreamFeatureError("content_filter")
        if kind == "response.completed" and status == "completed":
            reason = "tool_calls" if self.saw_tool else "stop"
        elif (
            kind == "response.incomplete"
            and status == "incomplete"
            and _obj(response.get("incomplete_details"), "incomplete_details").get("reason") == "max_output_tokens"
        ):
            reason = "length"
        else:
            raise StreamError("unsupported Responses final status")
        if self.incomplete_items and reason != "length":
            raise StreamError("incomplete Responses item in completed response")
        usage = _obj(response.get("usage") or {}, "Responses usage")
        self._usage(usage)
        # Created snapshots may still describe a requested processing mode.
        # Only the terminal response supplies the final reported tier.
        self._tier(response.get("service_tier"))
        return self._complete(reason)

    # Responses event type -> handler; the shared checks in _responses run first.
    _RESPONSES_EVENTS: ClassVar[dict[str, Callable[[StreamDecoder, dict[str, Any], str], list[Delta]]]] = {
        "response.output_item.added": _on_output_item_added,
        "response.content_part.added": _on_content_part_added,
        "response.output_text.delta": _on_output_text_delta,
        "response.function_call_arguments.delta": _on_tool_input_delta,
        "response.custom_tool_call_input.delta": _on_tool_input_delta,
        "response.output_text.done": _on_output_text_done,
        "response.function_call_arguments.done": _on_tool_input_done,
        "response.custom_tool_call_input.done": _on_tool_input_done,
        "response.content_part.done": _on_content_part_done,
        "response.output_item.done": _on_output_item_done,
        "response.completed": _on_finished,
        "response.incomplete": _on_finished,
    }

    # Upstream protocol -> per-event decoder.
    _PROTOCOL_EVENTS: ClassVar[dict[Protocol, Callable[[StreamDecoder, SSEEvent], list[Delta]]]] = {
        Protocol.CHAT: _chat,
        Protocol.MESSAGES: _messages,
        Protocol.RESPONSES: _responses,
    }

    def _public_reasoning_event(self, body: dict[str, Any], kind: str) -> list[Delta]:
        index = _index(body.get("output_index"), "output_index")
        if (
            body.get("item_id") != self.items[index][1]
            or type(body.get("content_index")) is not int
            or body["content_index"] != 0
        ):
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
