"""The protocol codec registry and the gateway's stream-failure classification."""

import pytest

from protoverta.core import CODECS, Protocol
from protoverta.resources import DeadlineExceeded, StreamLimitError
from protoverta.server import _classify_stream_failure
from protoverta.streaming import StreamError, UnsupportedReasoningError, UnsupportedStreamFeatureError


def test_every_protocol_has_one_codec_with_its_own_functions():
    assert set(CODECS) == set(Protocol)
    for protocol, codec in CODECS.items():
        assert codec.protocol == protocol
        for name in ("decode_request", "encode_request", "decode_result", "encode_result"):
            assert protocol.value.split("_")[0] in getattr(codec, name).__name__
    # Only Messages lacks logprobs, custom tools and unsigned reasoning.
    assert {p for p, c in CODECS.items() if not c.result_logprobs} == {Protocol.MESSAGES}
    assert {p for p, c in CODECS.items() if c.text_after_tool_call} == {Protocol.MESSAGES}


@pytest.mark.parametrize(
    "exc,feature,expected",
    [
        (UnsupportedStreamFeatureError("content_filter"), "content_filter", ("policy_refusal", "policy_refusal")),
        (UnsupportedReasoningError(), "reasoning", ("unsupported_reasoning", "unsupported_reasoning")),
        (UnsupportedStreamFeatureError("compaction"), "compaction", ("unsupported_feature", "unsupported_feature")),
        (DeadlineExceeded("progress_timeout"), None, ("progress_timeout", "timeout")),
        (TimeoutError(), None, ("upstream_timeout", "timeout")),
        (StreamLimitError("too big"), None, ("stream_limit", "stream_limit")),
        (StreamError("bad frame"), None, ("invalid_stream", "invalid_stream")),
    ],
)
def test_stream_failures_map_to_one_code_and_outcome(exc, feature, expected):
    assert _classify_stream_failure(exc, feature) == expected
