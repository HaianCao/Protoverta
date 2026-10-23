"""Explicit client compatibility policies layered above strict wire conversion."""

from __future__ import annotations

import copy

from .core import ConversionError, Protocol


def _blank_assistant_message(item: dict) -> bool:
    """Recognize only validated, whitespace-only Codex text placeholders."""
    if item.get("type", "message") != "message" or item.get("role") != "assistant" or set(item) - {"type", "id", "status", "role", "content"} or item.get("status") not in (None, "completed"):
        return False
    content = item.get("content")
    if isinstance(content, str):
        return not content.strip()
    return isinstance(content, list) and all(
        isinstance(block, dict) and block.get("type") in ("input_text", "output_text")
        and not (set(block) - {"type", "text", "annotations", "logprobs"})
        and isinstance(block.get("text"), str) and not block["text"].strip()
        and block.get("annotations") in (None, []) and block.get("logprobs") in (None, [])
        for block in content
    )


def prepare_claude_request(body: dict) -> dict:
    """Opt-in text/function policy, never a silent thinking/feature downgrade.

    Cache hints and client telemetry are deliberately not forwarded across
    providers. Structural tool-result errors become explicitly labelled text.
    The launcher must disable thinking and experimental features before sending.
    """
    result = copy.deepcopy(body)
    if "metadata" in result:
        if not isinstance(result["metadata"], dict):
            raise ConversionError("Claude metadata must be an object")
        result.pop("metadata")
    if "thinking" in result and result.pop("thinking") != {"type": "disabled"}:
        raise ConversionError("Claude cross-protocol gateway requires thinking disabled")

    def strip_cache(value):
        if not isinstance(value, dict) or "cache_control" not in value:
            return
        hint = value.pop("cache_control")
        if not isinstance(hint, dict) or hint.get("type") != "ephemeral" or set(hint) - {"type", "ttl"} or hint.get("ttl", "5m") not in {"5m", "1h"}:
            raise ConversionError("unsupported Claude cache hint")

    def content(blocks):
        if not isinstance(blocks, list):
            return
        for block in blocks:
            strip_cache(block)
            if isinstance(block, dict) and block.get("type") == "tool_result":
                content(block.get("content"))
                if block.get("is_error") is True:
                    value = block.get("content", "")
                    prefix = "[Tool execution error]\n"
                    if isinstance(value, str):
                        block["content"] = prefix + value
                    elif isinstance(value, list):
                        block["content"] = [{"type": "text", "text": prefix}, *value]
                    block.pop("is_error")

    content(result.get("system"))
    for message in result.get("messages", []) if isinstance(result.get("messages"), list) else []:
        strip_cache(message)
        if isinstance(message, dict):
            content(message.get("content"))
    for tool in result.get("tools", []) if isinstance(result.get("tools"), list) else []:
        strip_cache(tool)
        if isinstance(tool, dict):
            for key in ("strict", "defer_loading"):
                if tool.get(key) is False:
                    tool.pop(key)
    return result


def prepare_codex_request(body: dict, target: Protocol, *, default_max_output_tokens: int | None = None) -> dict:
    """Adapt Codex's stateless text/function subset under an opt-in policy.

    Cache hints and client telemetry have no cross-provider equivalent. Hidden
    reasoning state is not replayed. Summary generation must be disabled by the
    launcher. Namespaced/hosted tools remain errors in the strict converter.
    """
    result = copy.deepcopy(body)
    if target == Protocol.CHAT and isinstance(result.get("input"), list):
        # Codex may replay a whitespace-only assistant placeholder between a
        # function call and its result. Chat cannot represent this ordering.
        # This explicit client profile omits only those validated placeholders;
        # meaningful text, annotations and unknown features still fail strictly.
        inputs = []
        after_call = False
        for item in result["input"]:
            if isinstance(item, dict):
                if after_call and _blank_assistant_message(item):
                    continue
                if item.get("type") == "function_call":
                    after_call = True
                elif item.get("role") != "assistant":
                    after_call = False
            inputs.append(item)
        result["input"] = inputs
    if "client_metadata" in result:
        if not isinstance(result["client_metadata"], dict):
            raise ConversionError("Codex client_metadata must be an object")
        result.pop("client_metadata")
    if "prompt_cache_key" in result:
        if not isinstance(result["prompt_cache_key"], str):
            raise ConversionError("Codex prompt_cache_key must be a string")
        result.pop("prompt_cache_key")
    include = result.pop("include", [])
    if not isinstance(include, list) or any(value != "reasoning.encrypted_content" for value in include):
        raise ConversionError("Codex profile only permits the encrypted reasoning include hint")
    if "reasoning" in result:
        reasoning = result["reasoning"]
        if not isinstance(reasoning, dict) or set(reasoning) - {"effort", "summary"}:
            raise ConversionError("unsupported Codex reasoning settings")
        if reasoning.get("summary") not in (None, "none"):
            raise ConversionError("Codex gateway requires model_reasoning_summary='none'")
        reasoning.pop("summary", None)
        if not reasoning:
            result.pop("reasoning")
    if "text" in result:
        if result["text"] != {"format": {"type": "text"}}:
            raise ConversionError("Codex gateway only supports plain text output")
        result.pop("text")
    for tool in result.get("tools", []) if isinstance(result.get("tools"), list) else []:
        if isinstance(tool, dict) and (tool.get("type") == "namespace" or "tools" in tool):
            raise ConversionError("Codex gateway cannot translate namespaced tools in this session; start a fresh gateway session or use a native Responses provider")
    if target == Protocol.MESSAGES:
        if "max_output_tokens" not in result and default_max_output_tokens is not None:
            result["max_output_tokens"] = default_max_output_tokens
        inputs = result.get("input")
        if isinstance(inputs, str):
            inputs = [{"role": "user", "content": inputs}]
            result["input"] = inputs
        if isinstance(inputs, list):
            if "instructions" in result:
                inputs.insert(0, {"role": "system", "content": result.pop("instructions")})
            conversation_started = False
            for item in inputs:
                if not isinstance(item, dict):
                    continue  # The strict decoder reports invalid items.
                if item.get("role") in {"system", "developer"}:
                    if conversation_started:
                        raise ConversionError("Codex gateway cannot relocate mid-conversation instructions into Messages system text; start a fresh session or use a Chat/Responses provider")
                    item["role"] = "system"
                else:
                    conversation_started = True
    return result
