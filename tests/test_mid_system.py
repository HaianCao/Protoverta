import copy

import pytest

from protoverta.core import ConversionError, convert_request
from protoverta.profiles import prepare_codex_request
from protoverta.server import GatewayConfig


def request():
    return {"model": "verified-model", "max_output_tokens": 64, "input": [{"role": "system", "content": "Initial"}, {"role": "user", "content": "Question"}, {"role": "system", "content": "New instruction"}]}


def test_opt_in_preserves_system_position_without_hoisting_or_mutation():
    body = request()
    before = copy.deepcopy(body)
    with pytest.raises(ConversionError):
        convert_request(body, "responses", "messages")
    result = convert_request(body, "responses", "messages", messages_mid_system=True)
    assert result["system"] == [{"type": "text", "text": "Initial"}]
    assert [message["role"] for message in result["messages"]] == ["user", "system"]
    assert result["messages"][-1]["content"][0]["text"] == "New instruction"
    assert body == before


@pytest.mark.parametrize("roles", [["user", "assistant", "system"], ["user", "system", "user"]])
def test_capability_does_not_bypass_placement_rules(roles):
    body = request()
    body["input"] = [{"role": role, "content": "text"} for role in roles]
    with pytest.raises(ConversionError, match="history was not relocated"):
        convert_request(body, "responses", "messages", messages_mid_system=True)


def test_consecutive_system_group_has_valid_outer_neighbors():
    body = request()
    body["input"] += [{"role": "system", "content": "Second"}, {"role": "assistant", "content": "Answer"}]
    result = convert_request(body, "responses", "messages", messages_mid_system=True)
    assert [message["role"] for message in result["messages"]] == ["user", "system", "system", "assistant"]


def test_codex_developer_mapping_requires_both_profile_and_capability():
    body = request()
    body["input"][-1]["role"] = "developer"
    with pytest.raises(ConversionError):
        prepare_codex_request(body, "messages")
    prepared = prepare_codex_request(body, "messages", messages_mid_system=True)
    assert convert_request(prepared, "responses", "messages", messages_mid_system=True)["messages"][-1]["role"] == "system"


@pytest.mark.parametrize("models,protocol", [(["m"], "messages"), (("",), "messages"), (("bad model",), "messages"), (("m",), "chat")])
def test_gateway_capability_config_is_exact_and_messages_only(models, protocol):
    with pytest.raises(ValueError, match="messages_system_models"):
        GatewayConfig(protocol, "https://example.test/v1", "key", "local", messages_system_models=models)


@pytest.mark.parametrize("complete", [False, True])
def test_system_after_parallel_tools_requires_every_result(complete):
    body = {"model": "m", "max_tokens": 64, "messages": [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": identifier, "type": "function", "function": {"name": "read", "arguments": "{}"}} for identifier in ("a", "b")]},
        {"role": "tool", "tool_call_id": "a", "content": "Result a"},
    ]}
    if complete:
        body["messages"].append({"role": "tool", "tool_call_id": "b", "content": "Result b"})
    body["messages"].append({"role": "system", "content": "New instruction"})
    if complete:
        assert convert_request(body, "chat", "messages", messages_mid_system=True)["messages"][-1]["role"] == "system"
    else:
        with pytest.raises(ConversionError, match="history was not relocated"):
            convert_request(body, "chat", "messages", messages_mid_system=True)
