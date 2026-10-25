"""Strict JSON/UTF-8 boundaries; parser failures never expose input values."""

import json
from typing import Any

from .errors import ConversionError

_INVALID = "Value must be valid JSON with finite numbers and valid Unicode within supported parser limits."


def encode_json(value: Any, *, compact: bool = False) -> bytes:
    """Reject nonfinite numbers, lone surrogates, cycles and excessive nesting."""
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":") if compact else None).encode("utf-8")
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise ConversionError(_INVALID) from exc


def decode_json(raw: str | bytes | bytearray) -> Any:
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise ConversionError(_INVALID) from exc
    # Also catches exponent overflow and escaped lone Unicode surrogates.
    encode_json(value)
    return value
