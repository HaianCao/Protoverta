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

from .errors import ConversionError
from .formats import OutputFormat, decode_output_format, encode_output_format


class Protocol(StrEnum):
    CHAT = "chat"
    MESSAGES = "messages"
    RESPONSES = "responses"


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
    parallel_tool_calls: bool | None = None
    reasoning_effort: str | None = None
    output_format: OutputFormat | None = None


@dataclass(slots=True)
class Result:
    model: str
    parts: list[Part]
    finish_reason: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None


def _decode_usage(usage: dict[str, Any], source: Protocol) -> dict[str, int | None]:
    """Normalize total input tokens, not only Anthropic's uncached input."""
    if source == Protocol.CHAT:
        input_key, output_key = "prompt_tokens", "completion_tokens"
        input_details = _object(usage.get("prompt_tokens_details") or {}, "prompt_tokens_details")
        output_details = _object(usage.get("completion_tokens_details") or {}, "completion_tokens_details")
    else:
        input_key, output_key = "input_tokens", "output_tokens"
        input_details = _object(usage.get("input_tokens_details") or {}, "input_tokens_details")
        output_details = _object(usage.get("output_tokens_details") or {}, "output_tokens_details")
    read = _optional_nonnegative_int(usage.get("cache_read_input_tokens") if source == Protocol.MESSAGES else input_details.get("cached_tokens"), "cached input tokens")
    creation = _optional_nonnegative_int(usage.get("cache_creation_input_tokens"), "cache creation tokens") if source == Protocol.MESSAGES else None
    inputs = _optional_nonnegative_int(usage.get(input_key), input_key)
    outputs = _optional_nonnegative_int(usage.get(output_key), output_key)
    reasoning = _optional_nonnegative_int(output_details.get("reasoning_tokens"), "reasoning tokens")
    if source == Protocol.MESSAGES and inputs is not None:
        inputs += (read or 0) + (creation or 0)
    if inputs is not None and (read or 0) + (creation or 0) > inputs:
        raise ConversionError("cached input tokens exceed total input tokens")
    if outputs is not None and (reasoning or 0) > outputs:
        raise ConversionError("reasoning tokens exceed total output tokens")
    return {"input_tokens": inputs, "output_tokens": outputs, "cached_input_tokens": read,
            "cache_creation_input_tokens": creation, "reasoning_output_tokens": reasoning}


def _encode_usage(value: Any, target: Protocol) -> dict[str, Any]:
    inputs, outputs = value.input_tokens, value.output_tokens
    cached, creation, reasoning = value.cached_input_tokens, value.cache_creation_input_tokens, value.reasoning_output_tokens
    if target == Protocol.MESSAGES:
        usage = {"input_tokens": inputs - (cached or 0) - (creation or 0) if inputs is not None else None, "output_tokens": outputs}
        if cached is not None:
            usage["cache_read_input_tokens"] = cached
        if creation is not None:
            usage["cache_creation_input_tokens"] = creation
        return usage
    if target == Protocol.CHAT:
        usage = {"prompt_tokens": inputs, "completion_tokens": outputs, "total_tokens": inputs + outputs}
        if cached is not None:
            usage["prompt_tokens_details"] = {"cached_tokens": cached}
        if reasoning is not None:
            usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
        return usage
    return {"input_tokens": inputs, "input_tokens_details": {"cached_tokens": cached or 0},
            "output_tokens": outputs, "output_tokens_details": {"reasoning_tokens": reasoning or 0}, "total_tokens": inputs + outputs}


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
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 2:
        raise ConversionError("temperature must be a number between 0 and 2")
    return float(value)


def _stream_flag(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ConversionError("stream must be a boolean")
    return value


def _parallel_flag(value: Any) -> bool | None:
    if value is not None and not isinstance(value, bool):
        raise ConversionError("parallel_tool_calls must be a boolean")
    return value


def _effort(value: Any) -> str | None:
    if value is not None and value not in ("low", "medium", "high"):
        raise ConversionError("only low, medium, and high effort are portable")
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


def _tool_choice(value: Any, source: Protocol) -> str | dict | None:
    """Normalize forced function selection without accepting hosted tools."""
    if value is None:
        return None
    if source != Protocol.MESSAGES and isinstance(value, str) and value in {"auto", "none", "required"}:
        return value
    value = _object(value, "tool_choice")
    if source == Protocol.MESSAGES:
        _only(value, {"type", "name", "disable_parallel_tool_use"}, "tool_choice")
        kind = value.get("type")
        if isinstance(kind, str) and kind in {"auto", "any", "none"} and "name" not in value:
            return {"auto": "auto", "any": "required", "none": "none"}[kind]
        if kind == "tool":
            return {"type": "function", "name": _string(value.get("name"), "tool_choice.name")}
    elif value.get("type") == "function":
        if source == Protocol.CHAT:
            _only(value, {"type", "function"}, "tool_choice")
            function = _object(value.get("function"), "tool_choice.function")
            _only(function, {"name"}, "tool_choice.function")
            name = function.get("name")
        else:
            _only(value, {"type", "name"}, "tool_choice")
            name = value.get("name")
        return {"type": "function", "name": _string(name, "tool_choice.name")}
    raise ConversionError("tool_choice is not portable")


def _validate_chat_call(call: dict, index: int) -> None:
    _only(call, {"id", "type", "function", "index"}, "tool_call")
    # Some compatible providers include the streaming-style position in a
    # completed JSON call. Accept it only when it agrees with array ordering.
    if "index" in call and (type(call["index"]) is not int or call["index"] != index):
        raise ConversionError("Chat tool_call index must match its array position")
    if call.get("type") != "function":
        raise ConversionError("only client-side function calls are supported")


def _decode_chat(body: dict[str, Any]) -> Request:
    _only(body, {"model", "messages", "tools", "tool_choice", "max_completion_tokens", "max_tokens", "temperature", "stream", "parallel_tool_calls", "reasoning_effort", "response_format"}, "Chat request")
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
                for index, call in enumerate(_list(item.get("tool_calls") or [], "tool_calls")):
                    call = _object(call, "tool_call")
                    _validate_chat_call(call, index)
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
    return Request(_string(body.get("model"), "model"), turns, tools, _optional_positive_int(body.get("max_completion_tokens", body.get("max_tokens")), "max_completion_tokens"), _optional_temperature(body.get("temperature")), _stream_flag(body.get("stream", False)), _tool_choice(body.get("tool_choice"), Protocol.CHAT), _parallel_flag(body.get("parallel_tool_calls")), _effort(body.get("reasoning_effort")))


def _message_parts(content: Any, label: str) -> list[Part]:
    if isinstance(content, str):
        return [Part("text", text=content)]
    parts: list[Part] = []
    for block in _list(content, label):
        block = _object(block, label)
        kind = block.get("type")
        if kind == "text":
            _only(block, {"type", "text", "citations"}, label)
            if block.get("citations") not in (None, []):
                raise ConversionError("Messages citations cannot be preserved across protocols")
            parts.append(Part("text", text=_string(block.get("text"), "text")))
        elif kind == "tool_use":
            _only(block, {"type", "id", "name", "input", "caller"}, label)
            if "caller" in block and block["caller"] != {"type": "direct"}:
                raise ConversionError("non-direct Messages tool callers cannot be preserved across protocols")
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
    _only(body, {"model", "system", "messages", "tools", "tool_choice", "max_tokens", "temperature", "stream", "output_config"}, "Messages request")
    output_config = _object(body.get("output_config", {}), "output_config")
    _only(output_config, {"effort", "format"}, "output_config")
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
        if role not in {"user", "assistant", "system"}:
            raise ConversionError("Messages role must be user, assistant or system")
        turns.append(Turn(role, _message_parts(item.get("content"), "message.content")))
    tools: list[dict[str, Any]] = []
    for item in _list(body.get("tools") or [], "tools"):
        item = _object(item, "tool")
        _only(item, {"name", "description", "input_schema"}, "tool")
        tools.append({"name": _string(item.get("name"), "tool name"), "description": item.get("description", ""), "parameters": _object(item.get("input_schema"), "input_schema")})
    choice = body.get("tool_choice")
    parallel = None
    if choice is not None:
        choice = _object(choice, "tool_choice")
        if "disable_parallel_tool_use" in choice:
            flag = choice["disable_parallel_tool_use"]
            if not isinstance(flag, bool):
                raise ConversionError("disable_parallel_tool_use must be a boolean")
            parallel = not flag
        choice = _tool_choice(choice, Protocol.MESSAGES)
    return Request(_string(body.get("model"), "model"), turns, tools, _optional_positive_int(body.get("max_tokens"), "max_tokens"), _optional_temperature(body.get("temperature")), _stream_flag(body.get("stream", False)), choice, parallel, _effort(output_config.get("effort")))


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
    _only(body, {"model", "input", "instructions", "tools", "tool_choice", "max_output_tokens", "temperature", "stream", "store", "parallel_tool_calls", "reasoning", "text"}, "Responses request")
    reasoning = _object(body.get("reasoning", {}), "reasoning")
    _only(reasoning, {"effort"}, "reasoning")
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
                _only(item, {"type", "id", "call_id", "output"}, "function_call_output")
                output = item.get("output")
                if isinstance(output, list):
                    output = "".join(part.text for part in _response_content(output, "function_call_output.output"))
                turns.append(Turn("user", [Part("tool_result", call_id=_string(item.get("call_id"), "call_id"), text=_string(output, "output"))]))
            else:
                raise ConversionError(f"unsupported Responses item: {kind}")
    grouped_turns: list[Turn] = []
    for turn in turns:
        if turn.role == "assistant" and grouped_turns and grouped_turns[-1].role == "assistant":
            grouped_turns[-1].parts.extend(turn.parts)
        else:
            grouped_turns.append(turn)
    turns = grouped_turns
    tools: list[dict[str, Any]] = []
    for item in _list(body.get("tools") or [], "tools"):
        item = _object(item, "tool")
        _only(item, {"type", "name", "description", "parameters", "strict"}, "tool")
        if item.get("type") != "function" or item.get("strict"):
            raise ConversionError("only non-strict function tools are supported")
        tools.append({"name": _string(item.get("name"), "tool name"), "description": item.get("description", ""), "parameters": item.get("parameters", {"type": "object", "properties": {}})})
    return Request(_string(body.get("model"), "model"), turns, tools, _optional_positive_int(body.get("max_output_tokens"), "max_output_tokens"), _optional_temperature(body.get("temperature")), _stream_flag(body.get("stream", False)), _tool_choice(body.get("tool_choice"), Protocol.RESPONSES), _parallel_flag(body.get("parallel_tool_calls")), _effort(reasoning.get("effort")))


_DECODERS = {Protocol.CHAT: _decode_chat, Protocol.MESSAGES: _decode_messages, Protocol.RESPONSES: _decode_responses}


def _check_turns(request: Request, target: Protocol, *, messages_mid_system: bool = False) -> None:
    names = [tool["name"] for tool in request.tools]
    if len(names) != len(set(names)):
        raise ConversionError("duplicate tool definition name")
    if isinstance(request.tool_choice, dict) and request.tool_choice["name"] not in names:
        raise ConversionError("forced tool_choice must name a declared function")
    if request.tool_choice == "required" and not names:
        raise ConversionError("required tool_choice requires function tools")
    if target == Protocol.MESSAGES:
        seen_conversation = False
        for turn in request.turns:
            if turn.role in {"system", "developer"}:
                if (seen_conversation and not messages_mid_system) or turn.role == "developer":
                    raise ConversionError("Messages cannot preserve developer or mid-conversation instruction roles")
            else:
                seen_conversation = True
        if messages_mid_system:
            _check_mid_system_placement(request.turns)
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


def _check_mid_system_placement(turns: list[Turn]) -> None:
    """Validate Anthropic's text-only mid-system placement, never relocate it."""
    pending: set[str] = set()
    conversation_started = False
    following: list[str | None] = [None] * len(turns)
    next_role = None
    for index in range(len(turns) - 1, -1, -1):
        following[index] = next_role
        if turns[index].role != "system":
            next_role = turns[index].role
    previous_role = None
    for index, turn in enumerate(turns):
        if turn.role == "system" and conversation_started:
            if previous_role != "user" or following[index] not in (None, "assistant") or pending:
                raise ConversionError("mid-conversation Messages system text must follow a complete user/tool-result turn and precede assistant or end the array; history was not relocated")
        elif turn.role != "system":
            conversation_started = True
            previous_role = turn.role
        for part in turn.parts:
            if part.kind == "tool_call":
                pending.add(part.call_id)
            elif part.kind == "tool_result":
                pending.discard(part.call_id)


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
    if req.parallel_tool_calls is not None:
        out["parallel_tool_calls"] = req.parallel_tool_calls
    if req.reasoning_effort is not None:
        out["reasoning_effort"] = req.reasoning_effort
    if req.tools:
        out["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in req.tools]
    if req.max_tokens is not None:
        out["max_completion_tokens"] = req.max_tokens
    if req.temperature is not None:
        out["temperature"] = req.temperature
    if req.tool_choice is not None:
        if isinstance(req.tool_choice, dict):
            out["tool_choice"] = {"type": "function", "function": {"name": req.tool_choice["name"]}}
        elif isinstance(req.tool_choice, str) and req.tool_choice in {"auto", "none", "required"}:
            out["tool_choice"] = req.tool_choice
        else:
            raise ConversionError("tool_choice is not portable")
    return out


def _encode_messages(req: Request) -> dict[str, Any]:
    system: list[dict[str, str]] = []
    messages: list[dict[str, Any]] = []
    for turn in req.turns:
        if turn.role == "system" and not messages:
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
    if req.reasoning_effort is not None:
        out["output_config"] = {"effort": req.reasoning_effort}
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
        elif choice == "none":
            out["tool_choice"] = {"type": "none"}
        elif isinstance(choice, dict):
            out["tool_choice"] = {"type": "tool", "name": choice["name"]}
        else:
            raise ConversionError("tool_choice is not portable")
    if req.parallel_tool_calls is False and req.tools:
        choice = out.setdefault("tool_choice", {"type": "auto"})
        if choice.get("type") != "none":
            choice["disable_parallel_tool_use"] = True
    return out


def _flush_response_text(inputs: list[dict[str, Any]], role: str, parts: list[Part]) -> None:
    if parts:
        item: dict[str, Any] = {"role": role, "content": [{"type": "output_text" if role == "assistant" else "input_text", "text": p.text} for p in parts]}
        if role == "assistant":
            item.update({"type": "message", "id": "msg_" + secrets.token_hex(12), "status": "completed"})
        inputs.append(item)
        parts.clear()


def _encode_responses(req: Request) -> dict[str, Any]:
    inputs: list[dict[str, Any]] = []
    for turn in req.turns:
        text_parts: list[Part] = []
        for p in turn.parts:
            if p.kind == "text":
                text_parts.append(p)
            elif p.kind == "tool_call":
                _flush_response_text(inputs, turn.role, text_parts)
                inputs.append({"type": "function_call", "call_id": p.call_id, "name": p.name, "arguments": p.arguments})
            elif p.kind == "tool_result":
                _flush_response_text(inputs, turn.role, text_parts)
                inputs.append({"type": "function_call_output", "call_id": p.call_id, "output": p.text})
        _flush_response_text(inputs, turn.role, text_parts)
    out: dict[str, Any] = {"model": req.model, "input": inputs, "stream": req.stream, "store": False}
    if req.parallel_tool_calls is not None:
        out["parallel_tool_calls"] = req.parallel_tool_calls
    if req.reasoning_effort is not None:
        out["reasoning"] = {"effort": req.reasoning_effort}
    if req.tools:
        out["tools"] = [{"type": "function", "name": t["name"], "description": t["description"], "parameters": t["parameters"]} for t in req.tools]
    if req.max_tokens is not None:
        out["max_output_tokens"] = req.max_tokens
    if req.temperature is not None:
        out["temperature"] = req.temperature
    if req.tool_choice is not None:
        if isinstance(req.tool_choice, dict):
            out["tool_choice"] = dict(req.tool_choice)
        elif isinstance(req.tool_choice, str) and req.tool_choice in {"auto", "none", "required"}:
            out["tool_choice"] = req.tool_choice
        else:
            raise ConversionError("tool_choice is not portable")
    return out


_ENCODERS = {Protocol.CHAT: _encode_chat, Protocol.MESSAGES: _encode_messages, Protocol.RESPONSES: _encode_responses}


def convert_request(body: dict[str, Any], source: Protocol | str, target: Protocol | str, *, messages_mid_system: bool = False) -> dict[str, Any]:
    """Convert a non-streaming text/function request; reject unsupported semantics."""
    source, target = Protocol(source), Protocol(target)
    if not isinstance(messages_mid_system, bool):
        raise TypeError("messages_mid_system must be a boolean")
    if source == target:
        return body.copy()
    request = _DECODERS[source](_object(body, "request"))
    request.output_format = decode_output_format(body, source)
    _check_turns(request, target, messages_mid_system=messages_mid_system)
    output = _ENCODERS[target](request)
    formatted = encode_output_format(request.output_format, target)
    if "output_config" in formatted:
        output.setdefault("output_config", {}).update(formatted["output_config"])
    else:
        output.update(formatted)
    return output


def _validate_response_item(item: dict, label: str) -> None:
    if item.get("type") == "message":
        _only(item, {"type", "id", "status", "role", "content"}, label)
    elif item.get("type") == "function_call":
        _only(item, {"type", "id", "status", "call_id", "name", "arguments", "namespace"}, label)
        if item.get("namespace") is not None:
            raise ConversionError("upstream Responses namespace cannot be preserved in this protocol")


def _decode_result(body: dict[str, Any], source: Protocol) -> Result:
    if source == Protocol.CHAT:
        choices = _list(body.get("choices"), "choices")
        if len(choices) != 1:
            raise ConversionError("only one choice is supported")
        choice = _object(choices[0], "choice")
        if choice.get("logprobs"):
            raise ConversionError("Chat logprobs cannot be preserved")
        msg = _object(choice.get("message"), "message")
        _only(msg, {"role", "content", "tool_calls", "refusal", "annotations", "reasoning", "reasoning_content", "reasoning_details"}, "Chat response message")
        if any(msg.get(key) not in (None, "", []) for key in ("reasoning", "reasoning_content", "reasoning_details")):
            raise ConversionError("Chat reasoning cannot be preserved across protocols")
        if msg.get("role") != "assistant":
            raise ConversionError("Chat response message must have assistant role")
        if msg.get("annotations"):
            raise ConversionError("Chat annotations cannot be preserved")
        if msg.get("refusal"):
            raise ConversionError("refusal cannot be preserved across protocols")
        parts = _chat_content(msg.get("content"), "message.content")
        for index, call in enumerate(_list(msg.get("tool_calls") or [], "tool_calls")):
            call = _object(call, "tool_call")
            _validate_chat_call(call, index)
            fn = _object(call.get("function"), "function")
            _only(fn, {"name", "arguments"}, "function")
            parts.append(Part("tool_call", call_id=_string(call.get("id"), "call.id"), name=_string(fn.get("name"), "function.name"), arguments=_arguments(fn.get("arguments"), "function.arguments")))
        usage = _object(body.get("usage") or {}, "usage")
        reason = choice.get("finish_reason")
        if reason not in {"stop", "tool_calls", "length"}:
            raise ConversionError(f"unsupported Chat finish_reason: {reason}")
        if reason != "length" and (reason == "tool_calls") != any(p.kind == "tool_call" for p in parts):
            raise ConversionError("Chat tool_calls finish reason does not match output")
        return Result(_string(body.get("model"), "model"), parts, reason, **_decode_usage(usage, source))
    if source == Protocol.MESSAGES:
        if body.get("role") not in {None, "assistant"}:
            raise ConversionError("Messages response must have assistant role")
        parts = _message_parts(body.get("content"), "content")
        if any(p.kind == "tool_result" for p in parts):
            raise ConversionError("tool result is not a valid Messages response")
        usage = _object(body.get("usage") or {}, "usage")
        reason = body.get("stop_reason")
        if reason not in {"end_turn", "tool_use", "max_tokens"}:
            raise ConversionError(f"unsupported Messages stop_reason: {reason}")
        if reason != "max_tokens" and (reason == "tool_use") != any(p.kind == "tool_call" for p in parts):
            raise ConversionError("Messages tool_use stop reason does not match output")
        return Result(_string(body.get("model"), "model"), parts, "length" if reason == "max_tokens" else ("tool_calls" if reason == "tool_use" else "stop"), **_decode_usage(usage, source))
    parts = []
    for item in _list(body.get("output"), "output"):
        item = _object(item, "output item")
        _validate_response_item(item, "output item")
        if item.get("type") == "message":
            allowed_status = {None, "completed", "incomplete"} if body.get("status") == "incomplete" else {None, "completed"}
            if item.get("role") not in {None, "assistant"} or item.get("status") not in allowed_status:
                raise ConversionError("Responses output message must be a completed assistant message")
            parts.extend(_response_content(item.get("content"), "message.content"))
        elif item.get("type") == "function_call":
            if item.get("status") not in {None, "completed"}:
                raise ConversionError("Responses function call is incomplete")
            parts.append(Part("tool_call", call_id=_string(item.get("call_id"), "call_id"), name=_string(item.get("name"), "name"), arguments=_arguments(item.get("arguments"), "arguments")))
        else:
            raise ConversionError(f"unsupported Responses output item: {item.get('type')}")
    usage = _object(body.get("usage") or {}, "usage")
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
    return Result(_string(body.get("model"), "model"), parts, reason, **_decode_usage(usage, source))


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
            out["usage"] = _encode_usage(result, target)
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
        out = {"id": "msg_" + secrets.token_hex(12), "type": "message", "role": "assistant", "model": result.model, "content": blocks, "stop_reason": {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}[result.finish_reason], "stop_sequence": None, "usage": _encode_usage(result, target)}
        return out
    output: list[dict[str, Any]] = []
    if text:
        output.append({"id": "msg_" + secrets.token_hex(12), "type": "message", "role": "assistant", "status": "incomplete" if result.finish_reason == "length" else "completed", "content": [{"type": "output_text", "text": text, "annotations": [], "logprobs": []}]})
    output.extend({"id": "fc_" + secrets.token_hex(12), "type": "function_call", "call_id": p.call_id, "name": p.name, "arguments": p.arguments, "status": "completed"} for p in calls)
    out = {"id": "resp_" + secrets.token_hex(12), "created_at": int(time.time()), "object": "response", "model": result.model, "status": "incomplete" if result.finish_reason == "length" else "completed", "output": output, "parallel_tool_calls": len(calls) > 1, "error": None, "incomplete_details": {"reason": "max_output_tokens"} if result.finish_reason == "length" else None}
    out.update(tools=[], tool_choice="auto")
    if result.input_tokens is not None and result.output_tokens is not None:
        out["usage"] = _encode_usage(result, target)
    return out


def convert_response(body: dict[str, Any], source: Protocol | str, target: Protocol | str) -> dict[str, Any]:
    """Convert a completed text/function response without inventing usage."""
    source, target = Protocol(source), Protocol(target)
    if source == target:
        return body.copy()
    result = _decode_result(_object(body, "response"), source)
    call_ids = [part.call_id for part in result.parts if part.kind == "tool_call"]
    if any(not value for value in call_ids) or len(call_ids) != len(set(call_ids)):
        raise ConversionError("empty or duplicate response tool call ID")
    return _encode_result(result, target)
