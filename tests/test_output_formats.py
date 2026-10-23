import copy

import pytest

from protoverta import ConversionError, Protocol, convert_request
from protoverta.profiles import prepare_codex_request
from protoverta.server import apply_upstream_reasoning

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}, "values": {"type": "array", "items": {"type": "integer"}}}, "required": ["ok", "values"], "additionalProperties": False}


def request(protocol):
    return {
        Protocol.CHAT: {"model": "m", "messages": [{"role": "user", "content": "Return JSON"}], "max_completion_tokens": 64, "response_format": {"type": "json_schema", "json_schema": {"name": "result", "schema": copy.deepcopy(SCHEMA), "strict": True}}, "reasoning_effort": "low"},
        Protocol.RESPONSES: {"model": "m", "input": "Return JSON", "max_output_tokens": 64, "text": {"format": {"type": "json_schema", "name": "result", "schema": copy.deepcopy(SCHEMA), "strict": True}}, "reasoning": {"effort": "low"}},
        Protocol.MESSAGES: {"model": "m", "messages": [{"role": "user", "content": "Return JSON"}], "max_tokens": 64, "output_config": {"format": {"type": "json_schema", "schema": copy.deepcopy(SCHEMA)}, "effort": "low"}},
    }[protocol]


def format_of(body, protocol):
    if protocol == Protocol.CHAT:
        return body["response_format"]["json_schema"]
    if protocol == Protocol.RESPONSES:
        return body["text"]["format"]
    return body["output_config"]["format"]


@pytest.mark.parametrize("source,target", [(a, b) for a in Protocol for b in Protocol if a != b])
def test_structured_output_all_six_routes_preserve_schema_and_effort(source, target):
    body = request(source)
    original = copy.deepcopy(body)
    converted = convert_request(body, source, target)
    assert format_of(converted, target)["schema"] == SCHEMA
    if target != Protocol.MESSAGES:
        assert format_of(converted, target)["strict"] is True
        assert format_of(converted, target)["name"] == ("protoverta_output" if source == Protocol.MESSAGES else "result")
    else:
        assert converted["output_config"]["effort"] == "low"
        disabled = apply_upstream_reasoning(converted, target, "disabled")
        assert disabled["output_config"]["format"]["schema"] == SCHEMA
        assert "effort" not in disabled["output_config"]
    assert format_of(convert_request(converted, target, source), source)["schema"] == SCHEMA
    assert body == original


def test_openai_formats_preserve_non_strict_schema_and_description():
    body = request(Protocol.CHAT)
    details = body["response_format"]["json_schema"]
    details.update(strict=False, description="The result label")
    details["schema"]["properties"]["values"]["minItems"] = 2
    converted = convert_request(body, "chat", "responses")
    assert converted["text"]["format"] == {"type": "json_schema", **details}
    assert convert_request(converted, "responses", "chat")["response_format"] == body["response_format"]
    with pytest.raises(ConversionError, match="strict=true"):
        convert_request(body, "chat", "messages")


def test_json_object_mode_is_not_emulated_on_messages():
    body = request(Protocol.CHAT)
    body["response_format"] = {"type": "json_object"}
    converted = convert_request(body, "chat", "responses")
    assert converted["text"] == {"format": {"type": "json_object"}}
    assert convert_request(converted, "responses", "chat")["response_format"] == body["response_format"]
    with pytest.raises(ConversionError, match="no equivalent"):
        convert_request(body, "chat", "messages")


@pytest.mark.parametrize("mutation", ["constraint", "optional", "extra_keys", "recursive", "description", "strict", "format_extra"])
def test_messages_schema_boundary_rejects_without_rewriting(mutation):
    body = request(Protocol.RESPONSES)
    value = body["text"]["format"]
    if mutation == "constraint":
        value["schema"]["properties"]["values"]["minItems"] = 2
    elif mutation == "optional":
        value["schema"]["required"] = ["ok"]
    elif mutation == "extra_keys":
        value["schema"]["additionalProperties"] = True
    elif mutation == "recursive":
        value["schema"]["properties"]["values"] = {"$ref": "#"}
    elif mutation == "description":
        value["description"] = "Extra instruction"
    elif mutation == "strict":
        value.pop("strict")
    else:
        body["text"]["verbosity"] = "low"
    original = copy.deepcopy(body)
    with pytest.raises(ConversionError):
        convert_request(body, "responses", "messages")
    assert original == body


def test_codex_profile_accepts_supported_format_and_keeps_validation():
    body = request(Protocol.RESPONSES)
    prepared = prepare_codex_request(body, Protocol.MESSAGES)
    assert convert_request(prepared, "responses", "messages")["output_config"]["format"]["schema"] == SCHEMA


def test_messages_optional_properties_cannot_silently_become_required():
    body = request(Protocol.MESSAGES)
    body["output_config"]["format"]["schema"]["required"] = ["ok"]
    with pytest.raises(ConversionError, match="every property"):
        convert_request(body, "messages", "responses")
