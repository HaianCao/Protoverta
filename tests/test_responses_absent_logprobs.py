"""Responses text events without a ``logprobs`` field (seen from TokenHarbor)."""

import json

import pytest

from protoverta.sse import SSEParser, encode_sse
from protoverta.streaming import StreamDecoder, StreamError

ITEM = "msg_fixture"
TEXT = "NOTE_TEXT_123"


def stream(*, delta_logprobs=True):
    part = {"type": "output_text", "text": "", "annotations": []}
    done_part = {"type": "output_text", "text": TEXT, "annotations": []}
    message = {"id": ITEM, "type": "message", "status": "completed", "role": "assistant", "content": [done_part]}
    response = {"id": "resp_fixture", "object": "response", "status": "in_progress", "model": "m", "output": []}
    delta = {"type": "response.output_text.delta", "item_id": ITEM, "output_index": 0, "content_index": 0, "delta": TEXT}
    if delta_logprobs:
        delta["logprobs"] = []
    events = [
        {"type": "response.created", "response": response},
        {"type": "response.in_progress", "response": response},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"id": ITEM, "type": "message", "status": "in_progress", "role": "assistant", "content": []},
        },
        {"type": "response.content_part.added", "item_id": ITEM, "output_index": 0, "content_index": 0, "part": part},
        delta,
        {"type": "response.output_text.done", "item_id": ITEM, "output_index": 0, "content_index": 0, "text": TEXT},
        {"type": "response.content_part.done", "item_id": ITEM, "output_index": 0, "content_index": 0, "part": done_part},
        {"type": "response.output_item.done", "output_index": 0, "item": message},
        {
            "type": "response.completed",
            "response": {
                **response,
                "status": "completed",
                "output": [message],
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            },
        },
    ]
    for number, event in enumerate(events):
        event["sequence_number"] = number
    return b"".join(encode_sse(event["type"], json.dumps(event)) for event in events)


def decode(raw, **options):
    decoder = StreamDecoder("responses", **options)
    deltas = [delta for event in SSEParser().feed(raw) for delta in decoder.feed(event)]
    decoder.finish()
    return deltas


@pytest.mark.parametrize("delta_logprobs", [True, False])
def test_absent_text_logprobs_read_as_empty_when_not_requested(delta_logprobs):
    deltas = decode(stream(delta_logprobs=delta_logprobs))
    assert "".join(delta.text or "" for delta in deltas if delta.kind == "text") == TEXT


def test_absent_text_logprobs_still_fail_when_requested():
    with pytest.raises(StreamError, match="omitted the requested logprobs"):
        decode(stream(), text_logprobs=True)
