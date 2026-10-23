"""Protocol conversion primitives for LLM APIs."""

from .capabilities import (
    CapabilityRecord,
    CapabilityRegistry,
    CapabilityReport,
    request_features,
)
from .core import ConversionError, Protocol, convert_request, convert_response
from .stream_encoder import StreamEncoder
from .stream_pipe import StreamTranscoder
from .streaming import Delta, StreamDecoder, StreamError

__all__ = [
    "CapabilityRecord",
    "CapabilityRegistry",
    "CapabilityReport",
    "ConversionError",
    "Delta",
    "Protocol",
    "StreamDecoder",
    "StreamEncoder",
    "StreamError",
    "StreamTranscoder",
    "convert_request",
    "convert_response",
    "request_features",
]
