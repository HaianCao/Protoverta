"""Immutable session-local discovery; never model capability certification."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC
from typing import Any
from urllib.parse import parse_qsl

from .capabilities import _prefix
from .core import Protocol
from .json_wire import encode_json

MAX_MODELS = 10_000
MAX_BYTES = 2 * 1024 * 1024
MAX_PAGE = 1000


class ModelMetadataUnavailable(ValueError):
    """The requested vendor projection cannot honestly represent unknown data."""


def _text(value: object, *, identifier: bool = False) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        return False
    return not any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF or identifier and c.isspace() for c in value)


@dataclass(frozen=True, slots=True)
class SnapshotModel:
    id: str
    display_name: str
    created: int | None = None
    owned_by: str | None = None
    lifecycle: str | None = None

    def __post_init__(self) -> None:
        if not _text(self.id, identifier=True) or not _text(self.display_name):
            raise ValueError("invalid snapshot model ID or display name")
        if self.created is not None and (type(self.created) is not int or not 0 <= self.created <= 253402300799):
            raise ValueError("invalid reported model creation time")
        if self.owned_by is not None and not _text(self.owned_by):
            raise ValueError("invalid reported model owner")
        if self.lifecycle is not None and self.lifecycle not in {"active", "deprecated", "retired"}:
            raise ValueError("invalid reported model lifecycle")

    def wire(self, format: str = "normalized", *, messages_api_version: str = "2023-06-01") -> dict[str, Any]:
        if format == "openai":
            if self.created is None or self.owned_by is None:
                raise ModelMetadataUnavailable("OpenAI model listing requires reported creation time and owner; use normalized listing when unknown")
            return {"id": self.id, "object": "model", "created": self.created, "owned_by": self.owned_by}
        if format == "messages":
            from datetime import datetime, timedelta

            if messages_api_version not in {"2023-06-01", "2026-10-21"}:
                raise ValueError("unsupported Messages model-list API version")
            if messages_api_version == "2026-10-21" and self.lifecycle is None:
                raise ModelMetadataUnavailable("Messages API 2026-10-21 listing requires reported lifecycle; use normalized listing when unknown")
            # The Messages contract explicitly permits epoch for unknown release.
            result: dict[str, Any] = {
                "id": self.id,
                "type": "model",
                "display_name": self.display_name,
                "created_at": (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=self.created or 0)).isoformat().replace("+00:00", "Z"),
            }
            if self.lifecycle is not None:
                result["lifecycle"] = self.lifecycle
            if messages_api_version == "2026-10-21":
                result.update(capabilities=None, line=None, max_input_tokens=None, max_tokens=None, deprecated_at=None, retires_at=None)
            return result
        # No invented created/owned_by/context/effort metadata.
        return {"id": self.id, "object": "model", "display_name": self.display_name, "capability_status": "unknown"}


@dataclass(frozen=True, slots=True)
class ModelSnapshot:
    upstream_base_url: str = field(repr=False)
    upstream_protocol: Protocol
    models: tuple[SnapshotModel, ...] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "upstream_base_url", _prefix(self.upstream_base_url))
        object.__setattr__(self, "upstream_protocol", Protocol(self.upstream_protocol))
        if not isinstance(self.models, tuple) or len(self.models) > MAX_MODELS or any(type(m) is not SnapshotModel for m in self.models):
            raise ValueError("snapshot needs at most 10000 immutable model entries")
        if len({m.id for m in self.models}) != len(self.models):
            raise ValueError("snapshot model IDs must be unique")
        if len(encode_json({"data": [asdict(m) for m in self.models]})) > MAX_BYTES:
            raise ValueError("snapshot exceeds 2 MiB")

    def matches(self, base_url: str, protocol: Protocol | str) -> bool:
        return self.upstream_base_url == _prefix(base_url) and self.upstream_protocol == Protocol(protocol)

    @classmethod
    def from_ids(cls, base_url: str, protocol: Protocol | str, identifiers: tuple[str, ...]) -> ModelSnapshot:
        """Explicit session IDs only; absent display names remain exact IDs."""
        if not isinstance(identifiers, tuple) or len(identifiers) > MAX_MODELS:
            raise ValueError("snapshot IDs must be a bounded tuple")
        # Validate before deduplication so malformed/unhashable input fails safely.
        entries = tuple(SnapshotModel(value, value) for value in identifiers)
        seen: set[str] = set()
        unique: list[SnapshotModel] = []
        for entry in entries:
            if entry.id not in seen:
                seen.add(entry.id)
                unique.append(entry)
        return cls(base_url, Protocol(protocol), tuple(unique))

    def page(self, query: str, *, default_format: str = "normalized", messages_api_version: str = "2023-06-01") -> dict[str, Any]:
        if len(query) > 2048 or re.search(r"%(?![0-9a-fA-F]{2})", query):
            raise ValueError("invalid catalog query")
        try:
            pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True, encoding="utf-8", errors="strict", max_num_fields=3)
        except (ValueError, UnicodeError):
            raise ValueError("invalid catalog query") from None
        args = dict(pairs)
        if len(args) != len(pairs) or set(args) - {"limit", "after_id", "before_id", "format"}:
            raise ValueError("catalog accepts only unique limit, cursor and format parameters")
        format = args.get("format", default_format)
        if format not in {"normalized", "messages", "openai"}:
            raise ValueError("unknown model list format")
        if format == "messages" and messages_api_version not in {"2023-06-01", "2026-10-21"}:
            raise ValueError("unsupported Messages model-list API version")
        limit = args.get("limit", "20" if format == "messages" else str(MAX_PAGE))
        if not re.fullmatch(r"[1-9][0-9]{0,3}", limit) or int(limit) > MAX_PAGE:
            raise ValueError("catalog limit must be between 1 and 1000")
        start, end = 0, len(self.models)
        if "after_id" in args and "before_id" in args:
            raise ValueError("catalog accepts only one cursor direction")
        cursor = args.get("after_id", args.get("before_id"))
        if cursor is not None:
            ids = [m.id for m in self.models]
            if cursor not in ids:
                raise ValueError("catalog cursor is not in this session snapshot")
            if "after_id" in args:
                start = ids.index(cursor) + 1
            else:
                end = ids.index(cursor)
                start = max(0, end - int(limit))
        models = self.models[start : min(end, start + int(limit))]
        result: dict[str, Any] = {
            "data": [m.wire(format, messages_api_version=messages_api_version) for m in models],
            "has_more": start > 0 if "before_id" in args else start + len(models) < end,
            "first_id": models[0].id if models else None,
            "last_id": models[-1].id if models else None,
        }
        if format != "messages":
            result["object"] = "list"
        if len(encode_json(result)) > MAX_BYTES:
            raise ValueError("model list page exceeds 2 MiB; reduce limit")
        return result
