"""Explicit upstream routing extensions, never a protocol conversion fallback."""

import copy
import re

from .errors import ConversionError


def validate_openrouter(config):
    slug = config.openrouter_provider
    if slug is None:
        return
    if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}", slug):
        raise ValueError("openrouter_provider requires one bounded provider/endpoint slug")
    if config.upstream_protocol != "chat" or config.upstream_base_url.rstrip("/") != "https://openrouter.ai/api/v1":
        raise ValueError("OpenRouter routing is certified only for its exact Chat API prefix")


def apply_openrouter(body, provider, session_id):
    if provider is None:
        return body
    if "provider" in body or "session_id" in body:
        raise ConversionError("request routing conflicts with the configured session route")
    result = copy.deepcopy(body)
    result["provider"] = {"only": [provider], "order": [provider], "allow_fallbacks": False, "require_parameters": True}
    result["session_id"] = session_id
    return result
