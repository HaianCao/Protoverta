"""Native custom-tool format mapping; never emulate grammar with functions."""

from copy import deepcopy

from .errors import ConversionError


def custom_definition(value: dict, source: str) -> dict:
    allowed = {"name", "description", "format"} | ({"type"} if source == "responses" else set())
    if set(value) - allowed or not isinstance(value.get("name"), str):
        raise ConversionError("invalid custom tool definition")
    result = {"type": "custom", "name": value["name"]}
    if "description" in value:
        if not isinstance(value["description"], str):
            raise ConversionError("custom description must be a string")
        result["description"] = value["description"]
    if "format" not in value:
        return result
    form = value["format"]
    if not isinstance(form, dict):
        raise ConversionError("custom format must be an object")
    if form == {"type": "text"}:
        result["format"] = dict(form)
        return result
    if form.get("type") != "grammar":
        raise ConversionError("custom format must be text or grammar")
    if source == "chat":
        if set(form) != {"type", "grammar"} or not isinstance(form["grammar"], dict):
            raise ConversionError("invalid Chat custom grammar")
        grammar = form["grammar"]
    else:
        grammar = {key: val for key, val in form.items() if key != "type"}
    if (
        set(grammar) != {"syntax", "definition"}
        or grammar["syntax"] not in ("lark", "regex")
        or not isinstance(grammar["definition"], str)
        or not grammar["definition"]
    ):
        raise ConversionError("custom grammar requires syntax and a nonempty definition")
    result["format"] = {"type": "grammar", **grammar}
    return result


def encode_custom(value: dict, target: str) -> dict:
    result = deepcopy(value)
    if target == "responses":
        return result
    result.pop("type")
    form = result.get("format")
    if form and form["type"] == "grammar":
        result["format"] = {"type": "grammar", "grammar": {key: val for key, val in form.items() if key != "type"}}
    return {"type": "custom", "custom": result}
