import base64
import itertools

import pytest

from protoverta import ConversionError, convert_request, convert_response
from protoverta.documents import PDF, decode_pdf

DATA = base64.b64encode(b"%PDF-1.4\nsynthetic fixture, not an executable or parsed document").decode()


@pytest.mark.parametrize("source,target", list(itertools.permutations(("chat", "messages", "responses"), 2)))
def test_inline_pdf_order_and_bytes_round_trip_all_six_request_routes(source, target):
    body = {"model": "m", "max_tokens": 100, "messages": [{"role": "user", "content": [{"type": "text", "text": "before"}, PDF(DATA).encode("chat"), {"type": "text", "text": "after"}]}]}
    native = convert_request(body, "chat", source)
    translated = convert_request(native, source, target)
    replay = convert_request(translated, target, "chat")
    assert replay["messages"][0]["content"] == body["messages"][0]["content"]


def test_pdf_provider_ids_titles_and_filenames_are_not_silently_lost():
    with pytest.raises(ConversionError):
        decode_pdf({"type": "input_file", "file_id": "private"}, "responses")
    with pytest.raises(ConversionError):
        decode_pdf({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": DATA}, "title": "meaningful"}, "messages")
    with pytest.raises(ConversionError):
        PDF(DATA, "report.pdf").encode("messages")
    value = PDF(DATA, "report.pdf")
    assert decode_pdf(value.encode("chat"), "chat") == value
    assert decode_pdf(value.encode("responses"), "responses") == value


def test_generated_pdf_is_rejected_instead_of_returning_empty_text():
    body = {"model": "m", "choices": [{"message": {"role": "assistant", "content": [PDF(DATA).encode("chat")]}, "finish_reason": "stop"}]}
    with pytest.raises(ConversionError):
        convert_response(body, "chat", "responses")
