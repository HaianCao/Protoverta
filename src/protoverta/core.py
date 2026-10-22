"""Strict text/function-tool conversion between three LLM wire formats.

This module deliberately rejects fields whose semantics cannot be preserved.
It does not fabricate reasoning state, token counts, or server-side history.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Protocol(StrEnum):
    CHAT = "chat"
    MESSAGES = "messages"
    RESPONSES = "responses"


class ConversionError(ValueError):
    """A request cannot be converted without changing its meaning."""


@dataclass(slots=True)
class Part:
    kind: str
    text: str = ""
    call_id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass(slots=True)
class Turn:
    role: str
    parts: list[Part] = field(default_factory=list)


@dataclass(slots=True)
class Request:
    model: str
    turns: list[Turn]
    tools: list[dict[str, Any]] = field(default_factory=list)
    max_tokens: int | None = None
    temperature: float | None = None
    stream: bool = False
    tool_choice: Any = None


@dataclass(slots=True)
class Result:
    model: str
    parts: list[Part]
    finish_reason: str
    input_tokens: int | None = None
    output_tokens: int | None = None


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConversionError(f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ConversionError(f"{label} must be a string")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConversionError(f"{label} must be an array")
    return value


def _optional_positive_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConversionError(f"{label} must be a positive integer")
    return value


def _optional_nonnegative_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConversionError(f"{label} must be a non-negative integer")
    return value


def _optional_temperature(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 2:
        raise ConversionError("temperature must be a number between 0 and 2")
    return float(value)


def _stream_flag(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ConversionError("stream must be a boolean")
    return value


def _only(obj: dict[str, Any], allowed: set[str], label: str) -> None:
    extras = set(obj) - allowed
    if extras:
        raise ConversionError(f"{label}: unsupported fields: {', '.join(sorted(extras))}")


def _arguments(value: Any, label: str) -> str:
    raw = _string(value, label)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConversionError(f"{label} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ConversionError(f"{label} must contain a JSON object")
    return raw


def _chat_content(value: Any, label: str) -> list[Part]:
    if value is None:
        return []
    if isinstance(value, str):
        return [Part("text", text=value)]
    result: list[Part] = []
    for block in _list(value, label):
        block = _object(block, label)
        if block.get("type") != "text":
            raise ConversionError(f"{label}: only text content is supported")
        _only(block, {"type", "text"}, label)
        result.append(Part("text", text=_string(block.get("text"), label)))
    return result


def _decode_chat(body: dict[str, Any]) -> Request:
    _only(body, {"model", "messages", "tools", "tool_choice", "max_completion_tokens", "max_tokens", "temperature", "stream"}, "Chat request")
    if "max_completion_tokens" in body and "max_tokens" in body:
        raise ConversionError("choose one Chat token limit field")
    turns: list[Turn] = []
    for item in _list(body.get("messages"), "messages"):
        item = _object(item, "message")
        role = _string(item.get("role"), "message.role")
        if role in {"system", "developer", "user", "assistant"}:
            _only(item, {"role", "content", "tool_calls"}, "message")
            parts = _chat_content(item.get("content"), "message.content")
            if role == "assistant":
                for call in _list(item.get("tool_calls") or [], "tool_calls"):
                    call = _object(call, "tool_call")
                    _only(call, {"id", "type", "function"}, "tool_call")
                    if call.get("type") != "function":
                        raise ConversionError("only client-side function calls are supported")
                    fn = _object(call.get("function"), "tool_call.function")
                    _only(fn, {"name", "arguments"}, "tool_call.function")
                    parts.append(Part("tool_call", call_id=_string(call.get("id"), "tool_call.id"), name=_string(fn.get("name"), "tool_call.name"), arguments=_arguments(fn.get("arguments"), "tool_call.arguments")))
            turns.append(Turn(role, parts))
        elif role == "tool":
            _only(item, {"role", "tool_call_id", "content"}, "tool message")
            turns.append(Turn("user", [Part("tool_result", call_id=_string(item.get("tool_call_id"), "tool_call_id"), text=_string(item.get("content"), "tool content"))]))
        else:
            raise ConversionError(f"unsupported Chat role: {role}")
    tools: list[dict[str, Any]] = []
    for item in _list(body.get("tools") or [], "tools"):
        item = _object(item, "tool")
        _only(item, {"type", "function"}, "tool")
        if item.get("type") != "function":
            raise ConversionError("only function tools are supported")
        fn = _object(item.get("function"), "tool.function")
        _only(fn, {"name", "description", "parameters", "strict"}, "tool.function")
        if fn.get("strict"):
            raise ConversionError("strict tool schemas have no universal equivalent")
        tools.append({"name": _string(fn.get("name"), "tool name"), "description": fn.get("description", ""), "parameters": fn.get("parameters", {"type": "object", "properties": {}})})
    return Request(_string(body.get("model"), "model"), turns, tools, _optional_positive_int(body.get("max_completion_tokens", body.get("max_tokens")), "max_completion_tokens"), _optional_temperature(body.get("temperature")), _stream_flag(body.get("stream", False)), body.get("tool_choice"))


def _message_parts(content: Any, label: str) -> list[Part]:
    if isinstance(content, str):
        return [Part("text", text=content)]
    parts: list[Part] = []
    for block in _list(content, label):
        block = _object(block, label)
        kind = block.get("type")
        if kind == "text":
            _only(block, {"type", "text"}, label)
            parts.append(Part("text", text=_string(block.get("text"), "text")))
        elif kind == "tool_use":
            _only(block, {"type", "id", "name", "input"}, label)
            inputs = _object(block.get("input"), "tool_use.input")
            parts.append(Part("tool_call", call_id=_string(block.get("id"), "tool_use.id"), name=_string(block.get("name"), "tool_use.name"), arguments=json.dumps(inputs, ensure_ascii=False)))
        elif kind == "tool_result":
            _only(block, {"type", "tool_use_id", "content", "is_error"}, label)
            if block.get("is_error"):
                raise ConversionError("tool_result.is_error cannot be preserved in other protocols")
            result = block.get("content", "")
            if not isinstance(result, str):
                text_blocks = _message_parts(result, "tool_result.content")
                if any(part.kind != "text" for part in text_blocks):
                    raise ConversionError("non-text tool results are unsupported")
                result = "".join(part.text for part in text_blocks)
            parts.append(Part("tool_result", call_id=_string(block.get("tool_use_id"), "tool_use_id"), text=result))
        else:
            raise ConversionError(f"unsupported Messages block: {kind}")
    return parts


def _decode_messages(body: dict[str, Any]) -> Request:
    _only(body, {"model", "system", "messages", "tools", "tool_choice", "max_tokens", "temperature", "stream"}, "Messages request")
    turns: list[Turn] = []
    if "system" in body:
        system = body["system"]
        parts = _message_parts(system, "system")
        if any(part.kind != "text" for part in parts):
            raise ConversionError("system must contain text only")
        turns.append(Turn("system", parts))
    for item in _list(body.get("messages"), "messages"):
        item = _object(item, "message")
        _only(item, {"role", "content"}, "message")
        role = item.get("role")
        if role not in {"user", "assistant"}:
            raise ConversionError("Messages role must be user or assistant")
        turns.append(Turn(role, _message_parts(item.get("content"), "message.content")))
    tools: list[dict[str, Any]] = []
    for item in _list(body.get("tools") or [], "tools"):
        item = _object(item, "tool")
        _only(item, {"name", "description", "input_schema"}, "tool")
        tools.append({"name": _string(item.get("name"), "tool name"), "description": item.get("description", ""), "parameters": _object(item.get("input_schema"), "input_schema")})
    return Request(_string(body.get("model"), "model"), turns, tools, _optional_positive_int(body.get("max_tokens"), "max_tokens"), _optional_temperature(body.get("temperature")), _stream_flag(body.get("stream", False)), body.get("tool_choice"))


def _response_content(content: Any, label: str) -> list[Part]:
    if isinstance(content, str):
        return [Part("text", text=content)]
    parts: list[Part] = []
    for block in _list(content, label):
        block = _object(block, label)
        if block.get("type") not in {"input_text", "output_text"}:
            raise ConversionError(f"unsupported Responses content type: {block.get('type')}")
        _only(block, {"type", "text", "annotations", "logprobs"}, label)
        if block.get("annotations") or block.get("logprobs"):
            raise ConversionError("Responses annotations and logprobs cannot be preserved")
        parts.append(Part("text", text=_string(block.get("text"), "text")))
    return parts


def _decode_responses(body: dict[str, Any]) -> Request:
    _only(body, {"model", "input", "instructions", "tools", "tool_choice", "max_output_tokens", "temperature", "stream", "store"}, "Responses request")
    if body.get("store") is True:
        raise ConversionError("store=true requires a stateful gateway")
    turns: list[Turn] = []
    if "instructions" in body:
        turns.append(Turn("developer", [Part("text", text=_string(body["instructions"], "instructions"))]))
    inputs = body.get("input")
    if isinstance(inputs, str):
        turns.append(Turn("user", [Part("text", text=inputs)]))
    else:
        for item in _list(inputs, "input"):
            item = _object(item, "input item")
            kind = item.get("type", "message")
            if kind == "message":
                _only(item, {"type", "id", "status", "role", "content"}, "input message")
                role = _string(item.get("role"), "input.role")
                if role not in {"system", "developer", "user", "assistant"}:
                    raise ConversionError(f"unsupported Responses role: {role}")
                if item.get("status") not in {None, "completed"}:
                    raise ConversionError("incomplete Responses history message")
                turns.append(Turn(role, _response_content(item.get("content"), "input.content")))
            elif kind == "function_call":
                _only(item, {"type", "id", "status", "call_id", "name", "arguments"}, "function_call")
                turns.append(Turn("assistant", [Part("tool_call", call_id=_string(item.get("call_id"), "call_id"), name=_string(item.get("name"), "function name"), arguments=_arguments(item.get("arguments"), "arguments"))]))
            elif kind == "function_call_output":
                _only(item, {"type", "call_id", "output"}, "function_call_output")
                turns.append(Turn("user", [Part("tool_result", call_id=_string(item.get("call_id"), "call_id"), text=_string(item.get("output"), "output"))]))
            else:
                raise ConversionError(f"unsupported Responses item: {kind}")
    tools: list[dict[str, Any]] = []
    for item in _list(body.get("tools") or [], "tools"):
        item = _object(item, "tool")
        _only(item, {"type", "name", "description", "parameters", "strict"}, "tool")
        if item.get("type") != "function" or item.get("strict"):
            raise ConversionError("only non-strict function tools are supported")
        tools.append({"name": _string(item.get("name"), "tool name"), "description": item.get("description", ""), "parameters": item.get("parameters", {"type": "object", "properties": {}})})
    return Request(_string(body.get("model"), "model"), turns, tools, _optional_positive_int(body.get("max_output_tokens"), "max_output_tokens"), _optional_temperature(body.get("temperature")), _stream_flag(body.get("stream", False)), body.get("tool_choice"))


_DECODERS = {Protocol.CHAT: _decode_chat, Protocol.MESSAGES: _decode_messages, Protocol.RESPONSES: _decode_responses}


def _check_turns(request: Request, target: Protocol) -> None:
    if target == Protocol.MESSAGES:
        seen_conversation = False
        for turn in request.turns:
            if turn.role in {"system", "developer"}:
                if seen_conversation or turn.role == "developer":
                    raise ConversionError("Messages cannot preserve developer or mid-conversation instruction roles")
            else:
                seen_conversation = True
    calls: set[str] = set()
    for turn in request.turns:
        if target in {Protocol.CHAT, Protocol.RESPONSES}:
            seen_tool_call = False
            for part in turn.parts:
                if part.kind == "tool_call":
                    seen_tool_call = True
                elif part.kind == "text" and seen_tool_call:
                    raise ConversionError("text after a tool call cannot be preserved in this target protocol")
        for part in turn.parts:
            if part.kind == "tool_call":
                if part.call_id in calls:
                    raise ConversionError("duplicate tool call ID")
                calls.add(part.call_id)
            elif part.kind == "tool_result" and part.call_id not in calls:
                raise ConversionError(f"tool result has no matching call: {part.call_id}")


def _encode_chat(req: Request) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    for turn in req.turns:
        text = "".join(p.text for p in turn.parts if p.kind == "text")
        calls = [p for p in turn.parts if p.kind == "tool_call"]
        results = [p for p in turn.parts if p.kind == "tool_result"]
        if results:
            if text or calls:
                raise ConversionError("mixed user text and tool results cannot be preserved in Chat")
            messages.extend({"role": "tool", "tool_call_id": p.call_id, "content": p.text} for p in results)
        else:
            msg: dict[str, Any] = {"role": turn.role, "content": text if text or not calls else None}
            if calls:
                msg["tool_calls"] = [{"id": p.call_id, "type": "function", "function": {"name": p.name, "arguments": p.arguments}} for p in calls]
            messages.append(msg)
    out: dict[str, Any] = {"model": req.model, "messages": messages, "stream": req.stream}
    if req.tools:
        out["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in req.tools]
    if req.max_tokens is not None:
        out["max_completion_tokens"] = req.max_tokens
    if req.temperature is not None:
        out["temperature"] = req.temperature
    if req.tool_choice is not None:
        if isinstance(req.tool_choice, str) and req.tool_choice in {"auto", "none", "required"}:
            out["tool_choice"] = req.tool_choice
        else:
            raise ConversionError("tool_choice is not portable")
    return out


def _encode_messages(req: Request) -> dict[str, Any]:
    system: list[dict[str, str]] = []
    messages: list[dict[str, Any]] = []
    for turn in req.turns:
        if turn.role == "system":
            system.extend({"type": "text", "text": p.text} for p in turn.parts)
            continue
        blocks: list[dict[str, Any]] = []
        for p in turn.parts:
            if p.kind == "text":
                blocks.append({"type": "text", "text": p.text})
            elif p.kind == "tool_call":
                blocks.append({"type": "tool_use", "id": p.call_id, "name": p.name, "input": json.loads(p.arguments)})
            elif p.kind == "tool_result":
                blocks.append({"type": "tool_result", "tool_use_id": p.call_id, "content": p.text})
        messages.append({"role": turn.role, "content": blocks})
    if req.max_tokens is None:
        raise ConversionError("Messages requires max_tokens; configure an explicit limit")
    out: dict[str, Any] = {"model": req.model, "messages": messages, "max_tokens": req.max_tokens, "stream": req.stream}
    if system:
        out["system"] = system
    if req.tools:
        out["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in req.tools]
    if req.temperature is not None:
        out["temperature"] = req.temperature
    if req.tool_choice is not None:
        choice = req.tool_choice
        if choice == "auto":
            out["tool_choice"] = {"type": "auto"}
        elif choice == "required":
            out["tool_choice"] = {"type": "any"}
        elif isinstance(choice, dict) and choice.get("type") in {"auto", "any", "none", "tool"}:
            out["tool_choice"] = choice
        else:
            raise ConversionError("tool_choice is not portable")
    return out


def _encode_responses(req: Request) -> dict[str, Any]:
    inputs: list[dict[str, Any]] = []
    for turn in req.turns:
        text_parts: list[Part] = []

        def flush_text() -> None:
            if text_parts:
                item: dict[str, Any] = {"role": turn.role, "content": [{"type": "output_text" if turn.role == "assistant" else "input_text", "text": p.text} for p in text_parts]}
                if turn.role == "assistant":
                    item.update({"type": "message", "id": "msg_" + secrets.token_hex(12), "status": "completed"})
                inputs.append(item)
                text_parts.clear()

        for p in turn.parts:
            if p.kind == "text":
                text_parts.append(p)
            elif p.kind == "tool_call":
                flush_text()
                inputs.append({"type": "function_call", "call_id": p.call_id, "name": p.name, "arguments": p.arguments})
            elif p.kind == "tool_result":
                flush_text()
                inputs.append({"type": "function_call_output", "call_id": p.call_id, "output": p.text})
        flush_text()
    out: dict[str, Any] = {"model": req.model, "input": inputs, "stream": req.stream, "store": False}
    if req.tools:
        out["tools"] = [{"type": "function", "name": t["name"], "description": t["description"], "parameters": t["parameters"]} for t in req.tools]
    if req.max_tokens is not None:
        out["max_output_tokens"] = req.max_tokens
    if req.temperature is not None:
        out["temperature"] = req.temperature
    if req.tool_choice is not None:
        if isinstance(req.tool_choice, str) and req.tool_choice in {"auto", "none", "required"}:
            out["tool_choice"] = req.tool_choice
        else:
            raise ConversionError("tool_choice is not portable")
    return out


_ENCODERS = {Protocol.CHAT: _encode_chat, Protocol.MESSAGES: _encode_messages, Protocol.RESPONSES: _encode_responses}


def convert_request(body: dict[str, Any], source: Protocol | str, target: Protocol | str) -> dict[str, Any]:
    """Convert a non-streaming text/function request; reject unsupported semantics."""
    source, target = Protocol(source), Protocol(target)
    if source == target:
        return body.copy()
    request = _DECODERS[source](_object(body, "request"))
    _check_turns(request, target)
    return _ENCODERS[target](request)


def _decode_result(body: dict[str, Any], source: Protocol) -> Result:
    if source == Protocol.CHAT:
        choices = _list(body.get("choices"), "choices")
        if len(choices) != 1:
            raise ConversionError("only one choice is supported")
        choice = _object(choices[0], "choice")
        if choice.get("logprobs"):
            raise ConversionError("Chat logprobs cannot be preserved")
        msg = _object(choice.get("message"), "message")
        _only(msg, {"role", "content", "tool_calls", "refusal", "annotations"}, "Chat response message")
        if msg.get("role") != "assistant":
            raise ConversionError("Chat response message must have assistant role")
        if msg.get("annotations"):
            raise ConversionError("Chat annotations cannot be preserved")
        if msg.get("refusal"):
            raise ConversionError("refusal cannot be preserved across protocols")
        parts = _chat_content(msg.get("content"), "message.content")
        for call in _list(msg.get("tool_calls") or [], "tool_calls"):
            fn = _object(_object(call, "tool_call").get("function"), "function")
            parts.append(Part("tool_call", call_id=_string(call.get("id"), "call.id"), name=_string(fn.get("name"), "function.name"), arguments=_arguments(fn.get("arguments"), "function.arguments")))
        usage = body.get("usage") or {}
        reason = choice.get("finish_reason")
        if reason not in {"stop", "tool_calls", "length"}:
            raise ConversionError(f"unsupported Chat finish_reason: {reason}")
        if (reason == "tool_calls") != any(p.kind == "tool_call" for p in parts):
            raise ConversionError("Chat tool_calls finish reason does not match output")
        return Result(_string(body.get("model"), "model"), parts, "length" if reason == "length" else ("tool_calls" if reason == "tool_calls" else "stop"), _optional_nonnegative_int(usage.get("prompt_tokens"), "prompt_tokens"), _optional_nonnegative_int(usage.get("completion_tokens"), "completion_tokens"))
    if source == Protocol.MESSAGES:
        if body.get("role") not in {None, "assistant"}:
            raise ConversionError("Messages response must have assistant role")
        parts = _message_parts(body.get("content"), "content")
        if any(p.kind == "tool_result" for p in parts):
            raise ConversionError("tool result is not a valid Messages response")
        usage = body.get("usage") or {}
        reason = body.get("stop_reason")
        if reason not in {"end_turn", "tool_use", "max_tokens"}:
            raise ConversionError(f"unsupported Messages stop_reason: {reason}")
        if (reason == "tool_use") != any(p.kind == "tool_call" for p in parts):
            raise ConversionError("Messages tool_use stop reason does not match output")
        return Result(_string(body.get("model"), "model"), parts, "length" if reason == "max_tokens" else ("tool_calls" if reason == "tool_use" else "stop"), _optional_nonnegative_int(usage.get("input_tokens"), "input_tokens"), _optional_nonnegative_int(usage.get("output_tokens"), "output_tokens"))
    parts = []
    for item in _list(body.get("output"), "output"):
        item = _object(item, "output item")
        if item.get("type") == "message":
            if item.get("role") not in {None, "assistant"} or item.get("status") not in {None, "completed"}:
                raise ConversionError("Responses output message must be a completed assistant message")
            parts.extend(_response_content(item.get("content"), "message.content"))
        elif item.get("type") == "function_call":
            if item.get("status") not in {None, "completed"}:
                raise ConversionError("Responses function call is incomplete")
            parts.append(Part("tool_call", call_id=_string(item.get("call_id"), "call_id"), name=_string(item.get("name"), "name"), arguments=_arguments(item.get("arguments"), "arguments")))
        else:
            raise ConversionError(f"unsupported Responses output item: {item.get('type')}")
    usage = body.get("usage") or {}
    status = body.get("status")
    if status not in {"completed", "incomplete"}:
        raise ConversionError(f"unsupported Responses status: {status}")
    if status == "incomplete":
        details = _object(body.get("incomplete_details"), "incomplete_details")
        if details.get("reason") != "max_output_tokens":
            raise ConversionError("unsupported Responses incomplete reason")
        reason = "length"
    else:
        reason = "tool_calls" if any(p.kind == "tool_call" for p in parts) else "stop"
    return Result(_string(body.get("model"), "model"), parts, reason, _optional_nonnegative_int(usage.get("input_tokens"), "input_tokens"), _optional_nonnegative_int(usage.get("output_tokens"), "output_tokens"))


def _encode_result(result: Result, target: Protocol) -> dict[str, Any]:
    if target in {Protocol.CHAT, Protocol.RESPONSES}:
        seen_tool_call = False
        for part in result.parts:
            if part.kind == "tool_call":
                seen_tool_call = True
            elif part.kind == "text" and seen_tool_call:
                raise ConversionError("text after a tool call cannot be preserved in this target response")
    text = "".join(p.text for p in result.parts if p.kind == "text")
    calls = [p for p in result.parts if p.kind == "tool_call"]
    if target == Protocol.CHAT:
        message: dict[str, Any] = {"role": "assistant", "content": text or None}
        if calls:
            message["tool_calls"] = [{"id": p.call_id, "type": "function", "function": {"name": p.name, "arguments": p.arguments}} for p in calls]
        out: dict[str, Any] = {"id": "chatcmpl-" + secrets.token_hex(12), "created": int(time.time()), "object": "chat.completion", "model": result.model, "choices": [{"index": 0, "message": message, "finish_reason": result.finish_reason}]}
        if result.input_tokens is not None and result.output_tokens is not None:
            out["usage"] = {"prompt_tokens": result.input_tokens, "completion_tokens": result.output_tokens, "total_tokens": result.input_tokens + result.output_tokens}
        return out
    if target == Protocol.MESSAGES:
        if result.input_tokens is None or result.output_tokens is None:
            raise ConversionError("Messages response requires real upstream token usage")
        blocks: list[dict[str, Any]] = []
        for p in result.parts:
            if p.kind == "text":
                blocks.append({"type": "text", "text": p.text})
            elif p.kind == "tool_call":
                blocks.append({"type": "tool_use", "id": p.call_id, "name": p.name, "input": json.loads(p.arguments)})
        out = {"id": "msg_" + secrets.token_hex(12), "type": "message", "role": "assistant", "model": result.model, "content": blocks, "stop_reason": {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}[result.finish_reason], "stop_sequence": None, "usage": {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens}}
        return out
    output: list[dict[str, Any]] = []
    if text:
        output.append({"id": "msg_" + secrets.token_hex(12), "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": text, "annotations": []}]})
    output.extend({"id": "fc_" + secrets.token_hex(12), "type": "function_call", "call_id": p.call_id, "name": p.name, "arguments": p.arguments, "status": "completed"} for p in calls)
    out = {"id": "resp_" + secrets.token_hex(12), "created_at": int(time.time()), "object": "response", "model": result.model, "status": "incomplete" if result.finish_reason == "length" else "completed", "output": output, "parallel_tool_calls": len(calls) > 1, "error": None, "incomplete_details": {"reason": "max_output_tokens"} if result.finish_reason == "length" else None}
    if result.input_tokens is not None and result.output_tokens is not None:
        out["usage"] = {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens, "total_tokens": result.input_tokens + result.output_tokens}
    return out


def convert_response(body: dict[str, Any], source: Protocol | str, target: Protocol | str) -> dict[str, Any]:
    """Convert a completed text/function response without inventing usage."""
    source, target = Protocol(source), Protocol(target)
    if source == target:
        return body.copy()
    return _encode_result(_decode_result(_object(body, "response"), source), target)
