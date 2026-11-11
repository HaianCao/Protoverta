"""Explicit end-of-system Anthropic cache breakpoint for certified Chat wire."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from .core import Protocol
from .errors import ConversionError

if TYPE_CHECKING:
    from .server import GatewayConfig


class CacheCheckpoint:
    def __init__(self) -> None:
        self.hint: dict[str, Any] | None = None

    def prepare(self, body: dict[str, Any], source: Protocol, config: GatewayConfig) -> dict[str, Any]:
        if source == config.upstream_protocol:
            return body
        value = copy.deepcopy(body)
        system = value.get("system")
        if source == Protocol.MESSAGES and isinstance(system, list):
            for index, block in enumerate(system):
                if isinstance(block, dict) and "cache_control" in block:
                    if index != len(system) - 1 or block.get("type") != "text":
                        raise ConversionError("only an end-of-system text cache checkpoint is portable under this policy")
                    hint = block.pop("cache_control")
                    if (
                        not isinstance(hint, dict)
                        or set(hint) - {"type", "ttl"}
                        or hint.get("type") != "ephemeral"
                        or hint.get("ttl", "5m") not in ("5m", "1h")
                    ):
                        raise ConversionError("unsupported cache checkpoint")
                    self.hint = hint

        def has_hint(block: Any) -> bool:
            if isinstance(block, list):
                return any(has_hint(item) for item in block)
            return isinstance(block, dict) and ("cache_control" in block or has_hint(block.get("content")))

        if has_hint(value.get("messages")) or has_hint(value.get("input")) or has_hint(value.get("tools")) or has_hint(value.get("system")):
            raise ConversionError("this cache checkpoint has no certified portable location")
        profiles = config.provider_profiles
        if self.hint is not None and (
            config.upstream_protocol != Protocol.CHAT
            or profiles is None
            or not profiles.supports_prompt_cache(config.upstream_base_url, config.upstream_protocol, body.get("model"))
        ):
            raise ConversionError("no exact provider/model evidence for the requested cache contract")
        return value

    def restore(self, outbound: dict[str, Any]) -> dict[str, Any]:
        if self.hint is None:
            return outbound
        result = copy.deepcopy(outbound)
        messages = result.get("messages", [])
        if not messages or messages[0].get("role") != "system" or not isinstance(messages[0].get("content"), str):
            raise ConversionError("cannot preserve the system cache boundary")
        messages[0]["content"] = [{"type": "text", "text": messages[0]["content"], "cache_control": self.hint}]
        return result
