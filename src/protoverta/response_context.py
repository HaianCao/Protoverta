"""Request facts for generated Responses snapshots, not provider defaults."""

from __future__ import annotations

import copy
from collections.abc import Callable

POLICY_FIELDS = ("parallel_tool_calls", "tools", "tool_choice")
CONTEXT_FIELDS = ("instructions", "max_output_tokens", "tools", "tool_choice",
                  "parallel_tool_calls", "text", "temperature", "top_p")


def generated_response_metadata() -> dict:
    """Facts for a new synchronous, stateless Responses envelope.

    JSON and SSE use the same contract. No implicit sampling, output format,
    tool policy, serving tier or token measurement is inferred from the output.
    Request context may supply validated, explicitly known settings afterward.
    """
    return {
        "instructions": None, "max_output_tokens": None, "max_tool_calls": None,
        "previous_response_id": None, "reasoning": None, "store": False,
        "background": False, "safety_identifier": None, "prompt_cache_key": None,
        "truncation": "disabled",
    }


def bind_response_context(request: dict) -> Callable[[dict], dict]:
    """Freeze portable Responses request metadata; never infer omitted policy.

    This checks metadata types, not upstream compliance or response provenance.
    Validate the full request using convert_request before sending it upstream.
    """
    from .core import (
        Protocol,
        _object,
        _optional_list,
        _optional_positive_int,
        _optional_temperature,
        _optional_top_p,
        _parallel_flag,
        _string,
        _tool_choice,
    )
    from .formats import decode_output_format
    from .json_wire import encode_json

    _object(request, "request_context")
    encode_json(request)
    _parallel_flag(request.get("parallel_tool_calls"))
    for tool in _optional_list(request.get("tools"), "request_context.tools"):
        _object(tool, "request_context.tool")
    _tool_choice(request.get("tool_choice"), Protocol.RESPONSES)
    if request.get("instructions") is not None:
        _string(request["instructions"], "request_context.instructions")
    _optional_positive_int(request.get("max_output_tokens"), "request_context.max_output_tokens")
    _optional_temperature(request.get("temperature"))
    _optional_top_p(request.get("top_p"))
    if request.get("text") is not None:
        decode_output_format(request, Protocol.RESPONSES)
    frozen = copy.deepcopy({name: request[name] for name in CONTEXT_FIELDS if name in request})
    return lambda result: restore_request_context(result, frozen)


def restore_request_context(result: dict, request: dict) -> dict:
    """Apply only to translated output after request validation and name restore.

    A stream's created/completed snapshots must describe the same request. The
    existence of one tool call does not imply parallel_tool_calls was disabled.
    Do not infer sampling defaults, service tier, usage or reasoning state here.
    """
    mapped = copy.deepcopy(result)
    snapshot = mapped.get("response", mapped if "output" in mapped else None)
    if isinstance(snapshot, dict):
        for name in POLICY_FIELDS:
            snapshot.pop(name, None)
        for name in CONTEXT_FIELDS:
            if request.get(name) is not None:
                snapshot[name] = copy.deepcopy(request[name])
        for tool in snapshot.get("tools", []):
            if isinstance(tool, dict) and tool.get("type") == "function":
                # The response metadata schema permits unknown/unset values as
                # null. Do not infer provider defaults (especially strictness).
                for name in ("description", "parameters", "strict"):
                    tool.setdefault(name, None)
        snapshot["store"] = False  # This gateway does not persist Responses.
        snapshot["background"] = False  # Request runs in this HTTP exchange.
        snapshot["previous_response_id"] = None  # Opaque references are rejected.
    return mapped
