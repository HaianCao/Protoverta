"""Portable inline PDF inputs. No URL fetch, upload or cross-provider file ID."""

import base64
import binascii
from dataclasses import dataclass

from .errors import ConversionError


@dataclass(frozen=True)
class PDF:
    data: str
    filename: str | None = None

    def encode(self, target):
        if target == "messages":
            if self.filename is not None:
                raise ConversionError("PDF filename has no equivalent Messages filename field")
            return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": self.data}}
        value = "data:application/pdf;base64," + self.data
        if target == "chat":
            return {"type": "file", "file": {"file_data": value, **({"filename": self.filename} if self.filename else {})}}
        return {"type": "input_file", "file_data": value, **({"filename": self.filename} if self.filename else {})}


def decode_pdf(block, source):
    if not isinstance(block, dict):
        raise ConversionError("PDF input must be an object")
    filename = None
    if source == "messages":
        if set(block) != {"type", "source"} or not isinstance(block.get("source"), dict):
            raise ConversionError("PDF titles, citations, context and file IDs are not portable")
        value = block["source"]
        if set(value) != {"type", "media_type", "data"} or value.get("type") != "base64" or value.get("media_type") != "application/pdf":
            raise ConversionError("portable PDF input requires inline application/pdf base64")
        data = value["data"]
    else:
        value = block.get("file") if source == "chat" else block
        if source == "chat" and set(block) != {"type", "file"} or not isinstance(value, dict) or set(value) - ({"type", "file_data", "filename"} if source == "responses" else {"file_data", "filename"}):
            raise ConversionError("PDF file IDs, URLs and unknown fields are not portable")
        data = value.get("file_data")
        prefix = "data:application/pdf;base64,"
        if not isinstance(data, str) or not data.startswith(prefix):
            raise ConversionError("portable PDF requires an explicit application/pdf data URL")
        data = data[len(prefix):]
        filename = value.get("filename")
        if filename is not None and (not isinstance(filename, str) or not filename or len(filename) > 255 or any(ord(c) < 32 or ord(c) == 127 or c in "/\\" for c in filename)):
            raise ConversionError("invalid portable PDF filename")
    if not isinstance(data, str) or len(data) > 8 * 1024 * 1024:
        raise ConversionError("PDF inline data exceeds supported bounds")
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        raise ConversionError("invalid PDF base64") from None
    if not raw.startswith(b"%PDF-"):
        raise ConversionError("inline data is not a PDF header")
    return PDF(data, filename)
