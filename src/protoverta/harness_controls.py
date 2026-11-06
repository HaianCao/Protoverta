"""Narrow, explicit guards for verified harness control limitations."""

from typing import Any

from .errors import UnsupportedFeatureError
from .streaming import UnsupportedStreamFeatureError


def guard_codex_closed_resume(value: Any, *, streaming: bool = False) -> None:
    """Do not let Codex 0.166 restore a B child using parent A's config."""
    if not isinstance(value, dict):
        return
    items = [value.get("item")]
    for container in (value, value.get("response")):
        if isinstance(container, dict) and isinstance(container.get("output"), list):
            items.extend(container["output"])
    for item in items:
        if isinstance(item, dict) and item.get("type") == "function_call" and item.get("name") == "resume_agent" and item.get("namespace") == "multi_agent_v1":
            raise (UnsupportedStreamFeatureError if streaming else UnsupportedFeatureError)("codex_closed_resume")
