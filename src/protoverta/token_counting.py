"""Opt-in native token counts. No generation fallback or implicit remote calls."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Sequence
from http.client import HTTPException
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .context import TokenCount, _has_media, assess_context
from .core import Protocol, convert_request
from .discovery import _NoRedirect
from .errors import ConversionError
from .resources import RequestBudget, attach_budget
from .transport import DeadlineHTTPHandler, DeadlineHTTPSHandler

if TYPE_CHECKING:
    from .server import GatewayConfig


class NativeCountError(ConversionError):
    """Sanitized provider failure, distinct from a local conversion rejection."""

    def __init__(self, status: int, headers: Any = None) -> None:
        self.status = status if type(status) is int and 400 <= status <= 599 else 502
        self.headers = headers
        super().__init__(f"native token counting HTTP {self.status}")


class ModelTokenCounter:
    """Explicit local tokenizer/template plugin bound to one exact model/protocol.

    The operator supplies a complete input renderer and encoder, including tool
    templates. Nothing is downloaded/imported dynamically. Exactness is asserted
    by that operator, never inferred from a tokenizer family or model spelling.
    """

    def __init__(
        self,
        model: str,
        protocol: Protocol | str,
        *,
        render_request: Callable[[dict[str, Any]], str],
        encode: Callable[[str], Sequence[int]],
        exact: bool = False,
    ) -> None:
        if not isinstance(model, str) or not model or not callable(render_request) or not callable(encode) or type(exact) is not bool:
            raise ValueError("tokenizer plugin needs exact model, renderer, encoder and exactness")
        self.model, self.protocol = model, Protocol(protocol)
        self.render, self.encode, self.exact = render_request, encode, exact

    def __call__(self, body: dict[str, Any], protocol: Protocol | str) -> TokenCount:
        if Protocol(protocol) != self.protocol or body.get("model") != self.model or _has_media(body):
            raise ConversionError("tokenizer plugin does not certify this model/protocol/media")
        rendered = self.render(copy.deepcopy(body))
        if not isinstance(rendered, str) or len(rendered.encode()) > 8 * 1024 * 1024:
            raise ConversionError("tokenizer renderer must return bounded complete input text")
        tokens = self.encode(rendered)
        if not isinstance(tokens, (list, tuple)) or len(tokens) > 8 * 1024 * 1024 or any(type(value) is not int or value < 0 for value in tokens):
            raise ConversionError("tokenizer encoder returned invalid token IDs")
        return TokenCount(len(tokens), exact=self.exact)


class NativeTokenCounter:
    """Count input tokens with the provider's own endpoint (Messages ``count_tokens`` or Responses ``input_tokens``); never an estimate."""

    def __init__(self, config: GatewayConfig, *, upstream_open: Callable[..., Any] | None = None) -> None:
        self.config = config
        self.open = upstream_open or build_opener(_NoRedirect(), DeadlineHTTPHandler(), DeadlineHTTPSHandler()).open

    def __call__(self, body: dict[str, Any], protocol: Protocol | str, *, budget: RequestBudget | None = None) -> TokenCount:
        config = self.config
        protocol = Protocol(protocol)
        if protocol != config.upstream_protocol or protocol == Protocol.CHAT:
            raise ConversionError("no certified native counting endpoint for this upstream protocol")
        payload = copy.deepcopy(body)
        for key in ("stream", "max_tokens", "max_output_tokens", "max_completion_tokens", "store"):
            payload.pop(key, None)
        endpoint = "/messages/count_tokens" if protocol == Protocol.MESSAGES else "/responses/input_tokens"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        headers["x-api-key" if config.auth_style == "x-api-key" else "Authorization"] = (
            config.upstream_api_key if config.auth_style == "x-api-key" else "Bearer " + config.upstream_api_key
        )
        if protocol == Protocol.MESSAGES:
            headers["anthropic-version"] = "2023-06-01"
        budget = budget or RequestBudget(min(config.timeout, 10), min(config.total_timeout, 30), min(config.progress_timeout, 30))
        request = Request(config.upstream_base_url.rstrip("/") + endpoint, data=json.dumps(payload).encode(), headers=headers)
        attach_budget(request, budget)
        try:
            with self.open(request, timeout=budget.remaining()) as response:
                raw = bytearray()
                while len(raw) <= 65536:
                    chunk = budget.read(response, min(4096, 65537 - len(raw)), streaming=True)
                    if not chunk:
                        break
                    raw.extend(chunk)
                if len(raw) > 65536:
                    raise ValueError("count response too large")
                result = json.loads(raw)
            value = result.get("input_tokens") if isinstance(result, dict) else None
            if type(value) is not int or value < 0 or protocol == Protocol.RESPONSES and result.get("object") != "response.input_tokens":
                raise ValueError("invalid count response")
            return TokenCount(value, exact=True)
        except HTTPError as exc:
            exc.close()
            raise NativeCountError(exc.code, exc.headers) from None
        except (TimeoutError, URLError) as exc:
            raise NativeCountError(504 if isinstance(exc, TimeoutError) or isinstance(exc.reason, TimeoutError) else 502) from None
        except (OSError, ValueError, RecursionError, HTTPException):
            raise NativeCountError(502) from None


def count_input(
    config: GatewayConfig,
    body: dict[str, Any],
    source: Protocol | str,
    *,
    upstream_open: Callable[..., Any] | None = None,
    budget: RequestBudget | None = None,
) -> tuple[int, str]:
    """Return explicit count and provenance; unsupported media is never guessed."""
    source = Protocol(source)
    if not isinstance(body, dict) or not isinstance(body.get("model"), str) or not body["model"]:
        raise ConversionError("token counting requires an object and exact model")
    if body.get("stream") is True:
        raise ConversionError("token counting is not a streaming operation")
    if config.token_count_mode == "off":
        raise ConversionError("token counting is disabled; choose an explicit count policy")
    if config.token_count_mode == "operator":
        counter = config.token_counter
        if counter is None:
            raise ConversionError("operator token counting needs a token_counter")
        try:
            value = counter(copy.deepcopy(body), source)
        except Exception:  # noqa: BLE001 -- suppress trusted callback details
            raise ConversionError("operator token counter failed") from None
        if not isinstance(value, TokenCount):
            raise ConversionError("operator token counter must return TokenCount")
        return value.tokens, "operator-exact" if value.exact else "operator-estimate"
    if config.token_count_mode == "estimate":
        result = assess_context(body, source, None)
        if result.input_tokens is None:
            raise ConversionError("no token estimate for this media input")
        return result.input_tokens, "utf8-json-byte-estimate"
    payload = copy.deepcopy(body)
    if source != config.upstream_protocol:
        # Generation-only budget is required by Messages conversion, then removed
        # before the native counting API. Never execute a generation as a fallback.
        payload.setdefault("max_output_tokens" if source == Protocol.RESPONSES else "max_tokens", 1)
        payload = convert_request(payload, source, config.upstream_protocol)
    if config.provider_profiles is not None:
        payload = config.provider_profiles.apply(config.upstream_base_url, config.upstream_protocol, payload)
    value = NativeTokenCounter(config, upstream_open=upstream_open)(payload, config.upstream_protocol, budget=budget)
    return value.tokens, "provider-native"
