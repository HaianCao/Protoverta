"""Unsigned public reasoning text only; never summaries or opaque state."""

from .errors import ConversionError


def response_reasoning(item: dict, *, initial: bool = False) -> str:
    if set(item) - {"type", "id", "status", "summary", "content", "encrypted_content"} or item.get("encrypted_content") is not None or item.get("type") != "reasoning" or item.get("summary") != []:
        raise ConversionError("only unsigned public reasoning text with an empty summary is portable")
    content = item.get("content")
    if initial:
        if content != [] or item.get("status") not in (None, "in_progress"):
            raise ConversionError("public reasoning must start with empty content")
        return ""
    if item.get("status") not in (None, "completed", "incomplete") or not isinstance(content, list) or len(content) != 1:
        raise ConversionError("public reasoning requires one completed reasoning_text part")
    part = content[0]
    if not isinstance(part, dict) or set(part) != {"type", "text"} or part.get("type") != "reasoning_text" or not isinstance(part.get("text"), str):
        raise ConversionError("unsupported public reasoning content")
    return part["text"]


def reasoning_item(text: str, *, identifier: str | None = None, status: str = "completed") -> dict:
    result = {"type": "reasoning", "summary": [], "content": [{"type": "reasoning_text", "text": text}], "status": status}
    if identifier is not None:
        result["id"] = identifier
    return result
