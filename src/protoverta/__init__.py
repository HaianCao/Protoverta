"""Protocol conversion primitives for LLM APIs."""

from .core import ConversionError, Protocol, convert_request, convert_response
from .stream_encoder import StreamEncoder
from .stream_pipe import StreamTranscoder
from .streaming import Delta, StreamDecoder, StreamError

__all__ = [
    "ConversionError", "Delta", "Protocol", "StreamDecoder", "StreamEncoder",
    "StreamError", "StreamTranscoder", "convert_request", "convert_response",
]
