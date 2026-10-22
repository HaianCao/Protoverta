"""Protocol conversion primitives for LLM APIs."""

from .core import ConversionError, Protocol, convert_request, convert_response

__all__ = ["ConversionError", "Protocol", "convert_request", "convert_response"]
