"""Portable user-image references; never fetch URLs or upload local files."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import ConversionError

_MEDIA = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
_DETAIL = frozenset({"auto", "low", "high", "original"})


def _only(value, fields, label):
    if not isinstance(value, dict) or set(value) - fields:
        raise ConversionError(f"unsupported {label} fields")


def _url(value):
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ConversionError("image URL must be a nonempty HTTP(S) URL")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise ConversionError("invalid image URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ConversionError("image URL requires HTTP(S), without credentials or fragment")
    return value


def _base64(media, data):
    if not isinstance(media, str) or media not in _MEDIA or not isinstance(data, str) or not data:
        raise ConversionError("image requires PNG, JPEG, WebP or GIF base64 data")
    try:
        if not base64.b64decode(data, validate=True):
            raise ConversionError("image base64 data is empty")
    except (ValueError, binascii.Error) as exc:
        raise ConversionError("invalid image base64 data") from exc
    return data


@dataclass(frozen=True, slots=True)
class Image:
    url: str = ""
    media_type: str = ""
    data: str = ""
    detail: str | None = None

    def encode(self, target: str) -> dict:
        if target == "messages":
            if self.detail not in (None, "auto"):
                raise ConversionError("Messages has no portable image detail equivalent")
            source = {"type": "url", "url": self.url} if self.url else {"type": "base64", "media_type": self.media_type, "data": self.data}
            return {"type": "image", "source": source}
        value = self.url or f"data:{self.media_type};base64,{self.data}"
        detail = {"detail": self.detail} if self.detail is not None else {}
        if target == "chat":
            return {"type": "image_url", "image_url": {"url": value, **detail}}
        return {"type": "input_image", "image_url": value, **detail}


def decode_image(block: dict, source: str) -> Image:
    detail = None
    if source == "messages":
        _only(block, {"type", "source"}, "Messages image")
        value = block.get("source")
        _only(value, {"type", "url", "media_type", "data"}, "Messages image source")
        if value.get("type") == "url":
            _only(value, {"type", "url"}, "Messages URL image")
            return Image(url=_url(value.get("url")))
        if value.get("type") == "base64":
            _only(value, {"type", "media_type", "data"}, "Messages base64 image")
            return Image(media_type=value.get("media_type"), data=_base64(value.get("media_type"), value.get("data")))
        raise ConversionError("Messages file IDs and unknown image sources are not portable")
    if source == "chat":
        _only(block, {"type", "image_url"}, "Chat image")
        value = block.get("image_url")
        _only(value, {"url", "detail"}, "Chat image_url")
        url = value.get("url")
        detail = value.get("detail")
    else:
        _only(block, {"type", "image_url", "detail"}, "Responses image (file IDs are not portable)")
        url = block.get("image_url")
        detail = block.get("detail")
    if detail is not None and (not isinstance(detail, str) or detail not in _DETAIL):
        raise ConversionError("unsupported image detail")
    if isinstance(url, str) and url.startswith("data:"):
        header, separator, data = url.partition(",")
        if not separator or not header.endswith(";base64"):
            raise ConversionError("image data URL must use base64")
        media = header[5:-7]
        return Image(media_type=media, data=_base64(media, data), detail=detail)
    return Image(url=_url(url), detail=detail)
