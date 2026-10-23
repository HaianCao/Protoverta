import copy
import itertools

import pytest

from protoverta import (
    ConversionError,
    convert_request,
    convert_response,
    request_features,
)

PROTOCOLS = ("chat", "messages", "responses")
PAIRS = list(itertools.permutations(PROTOCOLS, 2))
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aDrsAAAAASUVORK5CYII="
URL = "https://images.example.test/a.png?signature=synthetic"


def image(protocol, value=URL, detail=None):
    extra = {"detail": detail} if detail is not None else {}
    if protocol == "chat":
        return {"type": "image_url", "image_url": {"url": value, **extra}}
    if protocol == "responses":
        return {"type": "input_image", "image_url": value, **extra}
    if value.startswith("data:") and ";base64," in value:
        media, data = value[5:].split(";base64,", 1)
        return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
    return {"type": "image", "source": {"type": "url", "url": value}}


def content(protocol, value=URL, detail=None):
    kind = "input_text" if protocol == "responses" else "text"
    return [{"type": kind, "text": "before"}, image(protocol, value, detail), {"type": kind, "text": "after"}, image(protocol, value, detail)]


def request(protocol, value=URL, detail=None, role="user"):
    message = {"role": role, "content": content(protocol, value, detail)}
    return {"model": "vision-fixture", "max_output_tokens" if protocol == "responses" else "max_tokens": 32, "stream": True, "input" if protocol == "responses" else "messages": [message]}


@pytest.mark.parametrize("source,target", PAIRS)
@pytest.mark.parametrize("value", [URL, "data:image/png;base64," + PNG])
def test_ordered_text_images_round_trip(source, target, value):
    body = request(source, value)
    original = copy.deepcopy(body)
    converted = convert_request(body, source, target)
    assert converted["input" if target == "responses" else "messages"][0]["content"] == content(target, value)
    assert "images" in request_features(converted, target)
    replay = convert_request(converted, target, source)
    assert replay["input" if source == "responses" else "messages"][0]["content"] == content(source, value)
    assert body == original


@pytest.mark.parametrize("source,target", [("chat", "responses"), ("responses", "chat")])
@pytest.mark.parametrize("detail", ["auto", "low", "high", "original"])
def test_openai_image_detail_is_preserved(source, target, detail):
    out = convert_request(request(source, detail=detail), source, target)
    assert out["input" if target == "responses" else "messages"][0]["content"] == content(target, detail=detail)


@pytest.mark.parametrize("source", ["chat", "responses"])
@pytest.mark.parametrize("detail", ["low", "high", "original"])
def test_messages_rejects_explicit_detail_instead_of_dropping_it(source, detail):
    with pytest.raises(ConversionError, match="detail"):
        convert_request(request(source, detail=detail), source, "messages")
    assert convert_request(request(source, detail="auto"), source, "messages")["messages"][0]["content"] == content("messages")


@pytest.mark.parametrize("source,target", PAIRS)
@pytest.mark.parametrize("role", ["assistant", "system"])
def test_images_are_not_accepted_as_assistant_or_system_content(source, target, role):
    with pytest.raises(ConversionError):
        convert_request(request(source, role=role), source, target)


@pytest.mark.parametrize("source", PROTOCOLS)
@pytest.mark.parametrize("value", ["file:///tmp/a.png", "javascript:alert(1)", "https://user:password@images.test/a.png", "https://images.test/a.png#fragment", "data:image/svg+xml;base64,PHN2Zy8+", "data:image/png;base64,@@@", "data:image/png;base64,", "data:image/png,raw", "https://bad.test:bad/a"])
def test_invalid_or_unportable_image_sources_are_rejected(source, value):
    with pytest.raises(ConversionError):
        convert_request(request(source, value), source, "chat" if source != "chat" else "responses")


def test_provider_file_ids_and_unknown_fields_are_not_dropped():
    body = request("responses")
    body["input"][0]["content"] = [{"type": "input_image", "file_id": "file-provider-owned", "detail": "auto"}]
    with pytest.raises(ConversionError, match="file IDs"):
        convert_request(body, "responses", "chat")
    body = request("messages")
    body["messages"][0]["content"] = [{"type": "image", "source": {"type": "file", "file_id": "file-provider-owned"}}]
    with pytest.raises(ConversionError):
        convert_request(body, "messages", "responses")


@pytest.mark.parametrize("source", ["messages", "responses"])
def test_image_tool_results_are_rejected_not_joined_as_empty_text(source):
    if source == "responses":
        body = {"model": "m", "input": [{"type": "function_call", "call_id": "c", "name": "screenshot", "arguments": "{}"}, {"type": "function_call_output", "call_id": "c", "output": [image(source)]}]}
    else:
        body = {"model": "m", "max_tokens": 32, "messages": [{"role": "assistant", "content": [{"type": "tool_use", "id": "c", "name": "screenshot", "input": {}}]}, {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c", "content": [image(source)]}]}]}
    with pytest.raises(ConversionError, match="non-text tool results"):
        convert_request(body, source, "chat")


@pytest.mark.parametrize("source", PROTOCOLS)
def test_generated_image_responses_are_rejected(source):
    if source == "chat":
        body = {"model": "m", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": [image(source)]}}]}
    elif source == "messages":
        body = {"model": "m", "content": [image(source)], "stop_reason": "end_turn"}
    else:
        body = {"model": "m", "status": "completed", "output": [{"type": "message", "role": "assistant", "content": [image(source)]}]}
    with pytest.raises(ConversionError, match="image output"):
        convert_response(body, source, "chat" if source != "chat" else "responses")


def test_native_file_id_forwarding_is_unchanged():
    body = {"model": "m", "input": [{"role": "user", "content": [{"type": "input_image", "file_id": "file-native"}]}]}
    assert convert_request(body, "responses", "responses") == body
