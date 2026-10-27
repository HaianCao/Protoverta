"""Request facts for generated Responses snapshots, not provider defaults."""

from __future__ import annotations

import copy


def restore_request_context(result: dict, request: dict) -> dict:
    """Apply only to translated output after request validation and name restore.

    A stream's created/completed snapshots must describe the same request. The
    existence of one tool call does not imply parallel_tool_calls was disabled.
    Do not infer sampling defaults, service tier, usage or reasoning state here.
    """
    mapped = copy.deepcopy(result)
    snapshot = mapped.get("response", mapped if "output" in mapped else None)
    if isinstance(snapshot, dict):
        for name in ("instructions", "max_output_tokens", "tools", "tool_choice",
                     "parallel_tool_calls", "text", "temperature", "top_p"):
            if request.get(name) is not None:
                snapshot[name] = copy.deepcopy(request[name])
        snapshot["store"] = False  # This gateway does not persist Responses.
        snapshot["background"] = False  # Request runs in this HTTP exchange.
        snapshot["previous_response_id"] = None  # Opaque references are rejected.
    return mapped
