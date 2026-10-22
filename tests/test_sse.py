import pytest

from protoverta.sse import SSEEvent, SSEParser, encode_sse


def test_all_byte_boundaries_utf8_crlf_and_multiline():
    wire = b": heartbeat\r\nevent: response.output_text.delta\r\nid: 7\r\ndata: " + "xin chào".encode() + b"\r\ndata: world\r\n\r\n"
    parser = SSEParser()
    events = []
    for byte in wire:
        events.extend(parser.feed(bytes([byte])))
    events.extend(parser.finish())
    assert events == [SSEEvent("response.output_text.delta", "xin chào\nworld", "7")]


def test_discard_unterminated_event():
    parser = SSEParser()
    assert parser.feed(b"data: hello") == []
    assert parser.finish() == []


def test_round_trip_and_validation():
    parser = SSEParser()
    assert parser.feed(encode_sse("message_delta", "one\ntwo", id="a")) == [SSEEvent("message_delta", "one\ntwo", "a")]
    with pytest.raises(ValueError):
        encode_sse("bad\nevent", "x")
