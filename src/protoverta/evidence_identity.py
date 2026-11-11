"""Opt-in evidence identity for code/profile revisions, without credentials."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def core_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def route_identity(config: Any) -> str:
    body = {
        "core": core_fingerprint(),
        "provider_profiles": config.provider_profiles.fingerprint() if config.provider_profiles else None,
        "prompt_caching": config.prompt_caching,
        "messages_system_models": sorted(config.messages_system_models),
        "default_max_output_tokens": config.default_max_output_tokens,
        "openrouter_provider": config.openrouter_provider,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def evidence_options(config: Any) -> dict[str, Any]:
    return {
        "current_identity": route_identity(config) if config.require_current_evidence else None,
        "max_age_days": config.evidence_max_age_days,
    }
