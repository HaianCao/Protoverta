"""Lossless common token probability records; no tokenizer or inferred bytes."""

from __future__ import annotations

import copy
import math
from typing import Any

from .errors import ConversionError


def requested(body: dict[str, Any], protocol: str) -> bool:
    if protocol == "chat":
        return body.get("logprobs") is True
    include = body.get("include")
    return protocol == "responses" and isinstance(include, list) and "message.output_text.logprobs" in include


def records(value: Any, limit: int | None = None) -> list[dict[str, Any]]:
    if limit is not None and (type(limit) is not int or not 0 <= limit <= 20):
        raise ConversionError("invalid requested logprob limit")
    if not isinstance(value, list):
        raise ConversionError("logprobs must be an array")
    for item in value:
        _token(item, selected=True)
        if limit is not None and len(item["top_logprobs"]) > limit:
            raise ConversionError("upstream logprobs exceed requested top-k")
    return copy.deepcopy(value)


def _token(item: Any, *, selected: bool = False) -> None:
    keys = {"token", "bytes", "logprob"} | ({"top_logprobs"} if selected else set())
    if not isinstance(item, dict) or set(item) != keys or not isinstance(item["token"], str):
        raise ConversionError("unsupported token logprob record")
    raw = item["bytes"]
    if not isinstance(raw, list) or any(type(b) is not int or not 0 <= b <= 255 for b in raw):
        raise ConversionError("token logprobs require explicit byte arrays")
    p = item["logprob"]
    if type(p) not in (int, float):
        raise ConversionError("token logprob must be finite and nonpositive")
    try:
        finite = math.isfinite(p)
    except OverflowError:
        finite = False
    if not finite or p > 0:
        raise ConversionError("token logprob must be finite and nonpositive")
    if selected:
        top = item["top_logprobs"]
        if not isinstance(top, list) or len(top) > 20:
            raise ConversionError("invalid top logprob records")
        for candidate in top:
            _token(candidate)


def align(value: list[dict[str, Any]] | None, text: str, *, required: bool = False) -> None:
    try:
        encoded = text.encode("utf-8")
    except UnicodeError as exc:
        raise ConversionError("invalid Unicode in logprob text") from exc
    if required and encoded and not value:
        raise ConversionError("upstream omitted requested text logprobs")
    if value and bytes(b for token in value for b in token["bytes"]) != encoded:
        raise ConversionError("token logprob bytes do not match output text")


def chat_records(value: Any, limit: int | None = None) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"content", "refusal"}:
        raise ConversionError("unsupported Chat logprob envelope")
    if value.get("refusal") not in (None, []):
        raise ConversionError("refusal logprobs cannot be preserved")
    return records(value["content"], limit) if value.get("content") is not None else None
