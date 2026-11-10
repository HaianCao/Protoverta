"""Protocol conversion primitives for LLM APIs."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

try:
    __version__ = _version("protoverta")
except PackageNotFoundError:  # Imported from a source tree without installation.
    __version__ = "0+unknown"

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
from .response_context import bind_response_context
from .stream_encoder import StreamEncoder
from .stream_pipe import StreamTranscoder
from .streaming import Delta, StreamDecoder, StreamError, UnsupportedStreamFeatureError
from .token_counting import ModelTokenCounter, NativeTokenCounter, count_input
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
    "ModelTokenCounter",
    "NativeTokenCounter",
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
    "__version__",
    "assess_context",
    "bind_response_context",
    "convert_request",
    "convert_response",
    "count_input",
    "fetch_catalog",
    "request_features",
    "route_policies",
]
