"""Protocol conversion primitives for LLM APIs."""

from .capabilities import (
    CapabilityRecord,
    CapabilityRegistry,
    CapabilityReport,
    RouteCapabilityRecord,
    request_features,
    route_policies,
)
from .context import ContextAssessment, TokenCount, assess_context
from .core import ConversionError, Protocol, convert_request, convert_response
from .discovery import CatalogError, CatalogPolicy, ModelInfo, fetch_catalog
from .errors import UnsupportedFeatureError
from .stream_encoder import StreamEncoder
from .stream_pipe import StreamTranscoder
from .streaming import Delta, StreamDecoder, StreamError, UnsupportedStreamFeatureError
from .tool_names import ToolNameMap

__all__ = [
    "CapabilityRecord",
    "CapabilityRegistry",
    "CapabilityReport",
    "CatalogError",
    "CatalogPolicy",
    "ContextAssessment",
    "ConversionError",
    "Delta",
    "ModelInfo",
    "Protocol",
    "RouteCapabilityRecord",
    "StreamDecoder",
    "StreamEncoder",
    "StreamError",
    "StreamTranscoder",
    "TokenCount",
    "ToolNameMap",
    "UnsupportedFeatureError",
    "UnsupportedStreamFeatureError",
    "assess_context",
    "convert_request",
    "convert_response",
    "fetch_catalog",
    "request_features",
    "route_policies",
]
