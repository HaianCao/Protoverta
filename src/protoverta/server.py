"""Local HTTP gateway for completed JSON and capability-gated SSE requests."""

from __future__ import annotations

import copy
import io
import json
import math
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .admission import (
    AdmissionDenied,
    AdmissionPolicyError,
    AdmissionStorageError,
    SharedAdmission,
)
from .capabilities import CapabilityRegistry, _prefix, request_features, route_policies
from .context import assess_context
from .core import (
    ConversionError,
    Protocol,
    _decode_usage,
    convert_request,
    convert_response,
)
from .errors import UnsupportedFeatureError
from .failures import gateway_failure
from .json_wire import decode_json, encode_json
from .lifetime import DisconnectWatch
from .namespaces import NamespaceMap
from .observability import Observer, RequestTrace
from .profiles import prepare_claude_request, prepare_codex_request
from .provider_profiles import ProviderProfiles
from .resources import (
    ClientDisconnected,
    DeadlineExceeded,
    DeadlineInput,
    RequestBudget,
    StreamLimitError,
    StreamLimits,
    positive_number,
)
from .retry import open_with_retry, retry_after
from .sse import encode_sse
from .stream_pipe import StreamTranscoder, StreamValidator
from .stream_writer import StreamHeartbeatWriter
from .streaming import (
    StreamError,
    UnsupportedReasoningError,
    UnsupportedStreamFeatureError,
)
from .tool_names import ToolNameMap
from .tool_search import ClientToolSearchMap
from .transport import DeadlineHTTPHandler, DeadlineHTTPSHandler

STREAM_HEARTBEAT_INTERVAL = 15.0

_PATHS = {
    "/v1/responses/compact": Protocol.RESPONSES,
    "/v1/messages/count_tokens": Protocol.MESSAGES,
    "/v1/responses/input_tokens": Protocol.RESPONSES,
    "/v1/chat/completions": Protocol.CHAT,
    "/v1/messages": Protocol.MESSAGES,
    "/v1/responses": Protocol.RESPONSES,
}


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    upstream_protocol: Protocol
    upstream_base_url: str
    upstream_api_key: str = field(repr=False)
    inbound_token: str = field(repr=False)
    auth_style: str = "bearer"
    timeout: float = 120.0
    max_body_bytes: int = 8 * 1024 * 1024
    client_profile: str = "strict"
    default_max_output_tokens: int | None = None
    messages_usage_policy: str = "strict"
    upstream_reasoning: str = "provider_default"
    namespace_tools: str = "reject"
    messages_system_models: tuple[str, ...] = ()
    capabilities: CapabilityRegistry | None = None
    require_verified_capabilities: bool = False
    public_reasoning: bool = False
    tool_names: str | None = None
    max_concurrent_requests: int = 16
    total_timeout: float = 600.0
    progress_timeout: float = 120.0
    max_stream_bytes: int = 32 * 1024 * 1024
    max_stream_items: int = 1024
    max_tool_argument_bytes: int = 8 * 1024 * 1024
    client_tool_search: str = "reject"
    provider_profiles: ProviderProfiles | None = None
    shared_admission_db: str | None = None
    shared_requests: int = 40
    shared_window: float = 60.0
    shared_concurrency: int = 5
    retry_attempts: int = 1
    retry_max_wait: float = 5.0
    require_verified_routes: bool = False
    context_preflight: str = "off"
    token_counter: object = field(default=None, repr=False, compare=False)
    token_count_mode: str = "off"
    require_current_evidence: bool = False
    evidence_max_age_days: int | None = None
    prompt_caching: str = "off"
    openrouter_provider: str | None = None
    shared_token_limit: int | None = None

    def __post_init__(self) -> None:
        if type(self.require_current_evidence) is not bool or self.evidence_max_age_days is not None and (type(self.evidence_max_age_days) is not int or self.evidence_max_age_days < 0):
            raise ValueError("invalid evidence identity/freshness policy")
        if self.prompt_caching not in ("off", "ephemeral") or self.prompt_caching != "off" and self.provider_profiles is None:
            raise ValueError("explicit caching requires certified provider profiles")
        if self.token_count_mode not in ("off", "estimate", "native", "operator"):
            raise ValueError("invalid token count mode")
        if self.token_count_mode == "operator" and not callable(self.token_counter):
            raise ValueError("operator counting needs a configured callback")
        if self.context_preflight not in ("off", "estimate", "reject_estimate", "exact"):
            raise ValueError("invalid context preflight policy")
        if self.token_counter is not None and not callable(self.token_counter):
            raise ValueError("token_counter must be callable")
        if self.context_preflight == "exact" and self.token_counter is None:
            raise ValueError("exact context policy needs an operator token counter")
        if type(self.retry_attempts) is not int or not 1 <= self.retry_attempts <= 4 or not positive_number(self.retry_max_wait):
            raise ValueError("retry_attempts must be 1-4 and retry_max_wait must be finite and positive")
        if self.shared_admission_db is not None:
            if not isinstance(self.shared_admission_db, str) or not self.shared_admission_db.strip():
                raise ValueError("shared_admission_db must be a nonempty path")
            SharedAdmission(self.shared_admission_db, "validation", requests=self.shared_requests, window=self.shared_window, concurrency=self.shared_concurrency)
        if self.provider_profiles is not None and not isinstance(self.provider_profiles, ProviderProfiles):
            raise ValueError("provider_profiles must be a ProviderProfiles registry")
        if self.client_tool_search not in ("reject", "function"):
            raise ValueError("client_tool_search must be reject or function")
        if not isinstance(self.public_reasoning, bool):
            raise TypeError("public_reasoning must be a boolean")
        if self.tool_names is None:
            object.__setattr__(self, "tool_names", "alias" if self.client_profile in {"codex", "claude"} else "reject")
        if self.tool_names not in {"reject", "alias"}:
            raise ValueError("tool_names must be reject or alias")
        object.__setattr__(self, "upstream_protocol", Protocol(self.upstream_protocol))
        from .routing import validate_openrouter

        validate_openrouter(self)
        if self.shared_token_limit is not None and (type(self.shared_token_limit) is not int or not 0 < self.shared_token_limit <= 2**63 - 1 or self.shared_admission_db is None or not callable(self.token_counter)):
            raise ValueError("shared_token_limit requires a positive limit, dedicated shared database and exact operator token counter")
        if not isinstance(self.upstream_base_url, str) or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in self.upstream_base_url):
            raise ValueError("upstream_base_url must be text without whitespace or control characters")
        parsed = urlsplit(self.upstream_base_url)
        port = parsed.port
        if port is not None and port < 1:
            raise ValueError("upstream_base_url port must be between 1 and 65535")
        local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if (parsed.scheme != "https" and not local_http) or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or any(char.isspace() for char in self.upstream_base_url):
            raise ValueError("upstream_base_url must be HTTPS (HTTP is allowed only for loopback), without credentials, query or fragment")
        if parsed.path.rstrip("/").endswith(("/responses", "/messages", "/chat/completions")):
            raise ValueError("upstream_base_url must omit the endpoint suffix")
        if self.auth_style not in {"bearer", "x-api-key"}:
            raise ValueError("auth_style must be bearer or x-api-key")
        if not self.upstream_api_key or not self.inbound_token:
            raise ValueError("both upstream key and inbound token are required")
        if any(char in self.upstream_api_key + self.inbound_token for char in "\r\n"):
            raise ValueError("credentials cannot contain line breaks")
        if not self.inbound_token.isascii():
            raise ValueError("inbound_token must be ASCII")
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, int | float) or not math.isfinite(self.timeout) or self.timeout <= 0 or type(self.max_body_bytes) is not int or self.max_body_bytes <= 0:
            raise ValueError("timeout and max_body_bytes must be positive")
        if type(self.max_concurrent_requests) is not int or self.max_concurrent_requests <= 0:
            raise ValueError("max_concurrent_requests must be a positive integer")
        if not positive_number(self.total_timeout) or not positive_number(self.progress_timeout):
            raise ValueError("total_timeout and progress_timeout must be finite positive numbers")
        StreamLimits(self.max_stream_bytes, self.max_stream_items, self.max_tool_argument_bytes)
        if self.client_profile not in {"strict", "codex", "claude"}:
            raise ValueError("client_profile must be strict, codex or claude")
        if self.messages_usage_policy not in {"strict", "deferred"}:
            raise ValueError("messages_usage_policy must be strict or deferred")
        if self.upstream_reasoning not in ("provider_default", "disabled", "openrouter_disabled"):
            raise ValueError("upstream_reasoning must be provider_default, disabled or openrouter_disabled")
        if self.namespace_tools not in ("reject", "flatten"):
            raise ValueError("namespace_tools must be reject or flatten")
        if not isinstance(self.messages_system_models, tuple) or any(not isinstance(model, str) or not model or any(char.isspace() for char in model) for model in self.messages_system_models):
            raise ValueError("messages_system_models must be a tuple of exact model IDs")
        if self.messages_system_models and self.upstream_protocol != Protocol.MESSAGES:
            raise ValueError("messages_system_models requires a Messages upstream")
        if self.default_max_output_tokens is not None and (isinstance(self.default_max_output_tokens, bool) or not isinstance(self.default_max_output_tokens, int) or self.default_max_output_tokens <= 0):
            raise ValueError("default_max_output_tokens must be a positive integer")
        if self.capabilities is not None and not isinstance(self.capabilities, CapabilityRegistry):
            raise ValueError("capabilities must be a CapabilityRegistry")
        if not isinstance(self.require_verified_capabilities, bool) or self.require_verified_capabilities and self.capabilities is None:
            raise ValueError("require_verified_capabilities requires a capability registry")
        if type(self.require_verified_routes) is not bool or self.require_verified_routes and self.capabilities is None:
            raise ValueError("require_verified_routes requires a capability registry")
        if (self.require_current_evidence or self.evidence_max_age_days is not None) and self.capabilities is None:
            raise ValueError("evidence identity/freshness checks require a capability registry")

    @property
    def upstream_url(self) -> str:
        base = self.upstream_base_url.rstrip("/")
        path = {Protocol.CHAT: "/chat/completions", Protocol.MESSAGES: "/messages", Protocol.RESPONSES: "/responses"}[self.upstream_protocol]
        return base + path


def apply_upstream_reasoning(payload: dict, protocol: Protocol, policy: str) -> dict:
    """Apply an explicit operator override after conversion, without hiding output.

    provider_default leaves the request untouched. Disabled policies override any
    request-level effort/budget; unsupported models must return their own error.
    OpenRouter's vendor extension is opt-in, never inferred from the hostname.
    """
    if policy == "provider_default":
        return payload
    if policy not in ("disabled", "openrouter_disabled"):
        raise ValueError("invalid upstream reasoning policy")
    outbound = dict(payload)
    for key in ("reasoning", "reasoning_effort", "thinking"):
        outbound.pop(key, None)
    if protocol == Protocol.MESSAGES:
        outbound["thinking"] = {"type": "disabled"}
        if "output_config" in outbound:
            if not isinstance(outbound["output_config"], dict):
                raise ConversionError("output_config must be an object")
            output_config = dict(outbound["output_config"])
            output_config.pop("effort", None)
            if output_config:
                outbound["output_config"] = output_config
            else:
                outbound.pop("output_config")
    elif policy == "openrouter_disabled":
        outbound["reasoning"] = {"enabled": False}
    elif protocol == Protocol.CHAT:
        outbound["reasoning_effort"] = "none"
    else:
        outbound["reasoning"] = {"effort": "none"}
    return outbound


class _RedirectBlocker(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def make_handler(config: GatewayConfig, *, upstream_open=None, observer: Observer | None = None) -> type[BaseHTTPRequestHandler]:
    opener = build_opener(_RedirectBlocker(), DeadlineHTTPHandler(), DeadlineHTTPSHandler())
    open_upstream = upstream_open or opener.open
    admission = threading.BoundedSemaphore(config.max_concurrent_requests)
    route_session_id = secrets.token_hex(16)
    shared_admission = SharedAdmission(config.shared_admission_db, _prefix(config.upstream_base_url) + "\0" + config.upstream_api_key, requests=config.shared_requests, window=config.shared_window, concurrency=config.shared_concurrency, lease=config.total_timeout + config.timeout + 5) if config.shared_admission_db is not None else None

    class Handler(BaseHTTPRequestHandler):
        server_version = "Protoverta/0.3"
        connection_limit = config.max_concurrent_requests

        def setup(self):
            super().setup()
            self._budget = RequestBudget(config.timeout, config.total_timeout, config.progress_timeout)
            self.rfile.close()
            self.rfile = io.BufferedReader(DeadlineInput(self.connection.makefile("rb", buffering=0), self.connection, self._budget))

        def handle(self):
            try:
                super().handle()
            except ConnectionError:
                # Early request-line/header disconnects have no POST trace yet.
                return

        def log_message(self, format: str, *args: object) -> None:
            # Avoid logging paths, request bodies, or credentials by default.
            return

        def _json(self, status: int, payload: dict[str, object], headers=None) -> None:
            raw = encode_json(payload)
            self._raw_json(status, raw, headers)

        def _error(self, status, message, protocol, *, code=None, headers=None):
            self._trace.outcome = {400: "invalid_request", 401: "authentication", 404: "not_found", 408: "timeout", 411: "invalid_request", 413: "invalid_request", 422: "invalid_request", 429: "upstream_http", 503: "upstream_http", 504: "timeout"}.get(status, "upstream_http")
            if code in {"unsupported_feature", "unsupported_reasoning", "invalid_stream", "stream_limit", "upstream_transport"}:
                self._trace.outcome = code
            failure = gateway_failure(status, message, code=code, secrets=(config.upstream_api_key, config.inbound_token))
            self._json(failure.status, failure.payload(protocol), headers)

        def _forward_headers(self, headers) -> None:
            for name, value in (headers.items() if headers else []):
                lower = name.lower()
                allowed = lower in {"retry-after", "x-should-retry", "request-id", "x-request-id", "x-accel-buffering"} or lower.startswith("anthropic-ratelimit-")
                if allowed and "\r" not in str(value) and "\n" not in str(value):
                    self.send_header(name, value)

        def _raw_json(self, status: int, raw: bytes, headers=None) -> None:
            self.connection.settimeout(self._budget.remaining() if status < 400 else min(config.timeout, 1))
            self._trace.status = status
            if status < 400:
                self._trace.outcome = "success"
            elif self._trace.outcome == "internal_error":
                self._trace.outcome = "upstream_http"
            self.send_response(status)
            self.send_header("X-Protoverta-Request-ID", self._trace.request_id)
            for name, value in getattr(self, "_context_headers", {}).items():
                self.send_header(name, value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self._forward_headers(headers)
            self.end_headers()
            self.wfile.write(raw)
            self._trace.mark_first_payload()

        def _stream(self, response, source: Protocol, target: Protocol, response_transform=None, tool_name_transform=None, budget=None, delta_transform=None) -> None:
            limits = {"max_stream_bytes": config.max_stream_bytes, "max_stream_items": config.max_stream_items, "max_tool_argument_bytes": config.max_tool_argument_bytes}
            pipeline = StreamTranscoder(source, target, messages_usage_policy=config.messages_usage_policy, max_pending_bytes=config.max_body_bytes, response_transform=response_transform, public_reasoning=config.public_reasoning, tool_name_transform=tool_name_transform, delta_transform=delta_transform, **limits) if source != target else StreamValidator(source, **limits)
            budget = budget or RequestBudget(config.timeout, config.total_timeout, config.progress_timeout)
            headers_sent = False
            initial = bytearray()

            def send_bytes(raw, timeout):
                self.connection.settimeout(timeout)
                self.wfile.write(raw)
                self.wfile.flush()

            keepalive = None if source == target else encode_sse("ping", '{"type":"ping"}') if target == Protocol.MESSAGES else b": protoverta keepalive\n\n"
            writer = StreamHeartbeatWriter(send_bytes, budget, keepalive, interval=STREAM_HEARTBEAT_INTERVAL)

            def write_output(raw, *, final_error=False):
                writer.write(raw, final_error=final_error)
                self._trace.mark_first_payload()
                if not final_error:
                    writer.start()

            def send_headers() -> None:
                nonlocal headers_sent
                if not headers_sent:
                    self._trace.status = 200
                    self.connection.settimeout(budget.remaining())
                    self.send_response(200)
                    self.send_header("X-Protoverta-Request-ID", self._trace.request_id)
                    for name, value in getattr(self, "_context_headers", {}).items():
                        self.send_header(name, value)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Accel-Buffering", "no")
                    self._forward_headers(getattr(response, "headers", None))
                    try:
                        self.end_headers()
                    except ConnectionError as exc:
                        raise ClientDisconnected("Local HTTP client disconnected.") from exc
                    headers_sent = True

            try:
                while True:
                    chunk = budget.read(response, 4096 if hasattr(response, "read1") else 1, streaming=True)
                    if not chunk:
                        break
                    previous_progress = pipeline.limits.progress
                    output = pipeline.feed(chunk)
                    if pipeline.limits.progress > previous_progress:
                        budget.progress()
                    if output:
                        if not headers_sent:
                            initial.extend(output)
                            if len(initial) > config.max_body_bytes:
                                raise StreamError("initial stream metadata exceeds size limit")
                            # Do not commit HTTP 200 for metadata-only starts.
                            # Reject an unsupported first content block as a
                            # non-retryable HTTP error, without buffering text.
                            if not getattr(pipeline, "has_payload", True):
                                continue
                            output = bytes(initial)
                            initial.clear()
                        send_headers()
                        write_output(output)
                writer.close()  # No keepalive may follow a terminal/error event.
                tail = pipeline.finish()
                if initial:
                    tail = bytes(initial) + tail
                    initial.clear()
                if tail:
                    send_headers()
                    write_output(tail)
                send_headers()
                self._trace.outcome = "success"
                decoder = pipeline.decoder
                details = getattr(decoder, "usage_details", {})
                self._trace.usage(getattr(decoder, "input_tokens", None), getattr(decoder, "output_tokens", None), details.get("cached_input_tokens"), details.get("cache_creation_input_tokens"))
            except (StreamError, UnicodeError, OSError, HTTPException) as exc:
                writer.close()
                if isinstance(exc, ClientDisconnected):
                    self._trace.outcome = "client_disconnected"
                    return
                unsupported = isinstance(exc, (UnsupportedReasoningError, UnsupportedStreamFeatureError))
                message = f"upstream stream error: {type(exc).__name__}"
                if isinstance(exc, UnsupportedReasoningError):
                    hint = "Messages requires its own thinking/signature contract; use native Messages or a compatible Responses route." if target == Protocol.MESSAGES else "For unsigned Chat reasoning_content, enable public_reasoning explicitly on a Chat/Responses route; summaries, signed or encrypted state remain unsupported."
                    message = "Upstream reasoning cannot be preserved on this route. " + hint + " No reasoning was dropped or given a fabricated signature."
                elif isinstance(exc, UnsupportedStreamFeatureError):
                    message = str(exc)
                timeout = isinstance(exc, TimeoutError)
                limited = isinstance(exc, StreamLimitError)
                code = "unsupported_reasoning" if isinstance(exc, UnsupportedReasoningError) else "unsupported_feature" if unsupported else exc.kind if isinstance(exc, DeadlineExceeded) else "upstream_timeout" if timeout else "stream_limit" if limited else "invalid_stream"
                self._trace.outcome = "unsupported_reasoning" if isinstance(exc, UnsupportedReasoningError) else "unsupported_feature" if unsupported else "timeout" if timeout else "stream_limit" if limited else "invalid_stream"
                failure = gateway_failure(422 if unsupported else 504 if timeout else 502, message, code=code)
                payload = failure.payload(target)
                try:
                    if not headers_sent:
                        self._json(failure.status, payload, {"x-should-retry": "false"} if unsupported or limited or isinstance(exc, DeadlineExceeded) else None)
                    else:
                        sequence = pipeline.encoder.sequence if isinstance(pipeline, StreamTranscoder) else pipeline.decoder.sequence + 1
                        error = json.dumps(failure.stream_payload(target, sequence_number=max(0, sequence)))
                        write_output(encode_sse("error", error), final_error=True)
                except (ConnectionError, TimeoutError):
                    self._trace.outcome = "client_disconnected"
            finally:
                writer.close()

        def do_POST(self) -> None:
            protocol = _PATHS.get(urlsplit(self.path).path)
            self._trace = RequestTrace(protocol.value if protocol is not None else "unknown", config.upstream_protocol.value)
            self._context_headers = {}
            try:
                self._dispatch_post()
            except ConnectionError:
                self._trace.outcome = "client_disconnected"
            except Exception:
                self._trace.outcome = "internal_error"
                raise
            finally:
                self._trace.emit(observer)

        def _dispatch_post(self) -> None:
            target_url = urlsplit(self.path)
            protocol = _PATHS.get(target_url.path)
            if protocol is None:
                self._error(404, "unsupported endpoint", Protocol.CHAT)
                return
            token = self.headers.get("Authorization", "")
            if not token.startswith("Bearer ") or not token[7:].isascii() or not secrets.compare_digest(token[7:], config.inbound_token):
                self._error(401, "unauthorized", protocol)
                return
            if not admission.acquire(blocking=False):
                self._error(503, "Local gateway request capacity reached; retry after an active request finishes.", protocol, headers={"Retry-After": "1"})
                self._trace.outcome = "capacity"
                return
            try:
                self._handle_post(protocol, target_url)
            except AdmissionDenied as exc:
                self._error(429, "Shared provider admission exhausted.", protocol, headers={"Retry-After": str(exc.retry_after)})
                self._trace.outcome = "shared_admission"
            except (AdmissionStorageError, AdmissionPolicyError):
                self._error(503, "Shared provider admission unavailable.", protocol)
                self._trace.outcome = "admission_unavailable"
            except TimeoutError:
                self._error(408, "Client request body read timed out.", protocol, headers={"x-should-retry": "false"})
            except ConnectionError:
                self._trace.outcome = "client_disconnected"
            finally:
                admission.release()

        def _handle_post(self, protocol, target_url) -> None:
            # One unambiguous framing mode; never interpret chunked input as JSON.
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get_all("Transfer-Encoding") or len(lengths) > 1:
                self._error(400, "ambiguous or unsupported request framing", protocol)
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._error(411, "Content-Length is required", protocol)
                return
            if length < 0 or length > config.max_body_bytes:
                self._error(413, "request body is too large", protocol)
                return
            try:
                payload = decode_json(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ConversionError("request body must be a JSON object")
                response_request = copy.deepcopy(payload)
                if target_url.path in ("/v1/messages/count_tokens", "/v1/responses/input_tokens"):
                    if target_url.query:
                        raise ConversionError("count endpoint query parameters are unsupported")
                    from .token_counting import NativeCountError, count_input

                    count_lease = None
                    try:
                        if shared_admission is not None and config.token_count_mode == "native":
                            count_lease = shared_admission.acquire()
                        count, provenance = count_input(config, payload, protocol, upstream_open=upstream_open, budget=self._budget)
                    except NativeCountError as exc:
                        delay = retry_after(exc.headers)
                        if shared_admission is not None and exc.status in (429, 503) and delay is not None:
                            shared_admission.cooldown(delay)
                        self._error(exc.status, str(exc), protocol, headers=exc.headers)
                        return
                    finally:
                        if shared_admission is not None and count_lease is not None:
                            try:
                                shared_admission.release(count_lease)
                            except AdmissionStorageError:
                                pass
                    self._context_headers = {"X-Protoverta-Count-Kind": provenance}
                    result = {"input_tokens": count}
                    if protocol == Protocol.RESPONSES:
                        result["object"] = "response.input_tokens"
                    self._json(200, result)
                    return
                streaming = payload.get("stream", False)
                compact = target_url.path == "/v1/responses/compact"
                if compact and (config.upstream_protocol != Protocol.RESPONSES or streaming or target_url.query):
                    raise ConversionError("compaction requires a native non-streaming Responses upstream; opaque state cannot be translated")
                if not isinstance(streaming, bool):
                    raise ConversionError("stream must be a boolean")
                self._trace.streaming = streaming
                cache_active = False
                if streaming and protocol == Protocol.MESSAGES and config.upstream_protocol != Protocol.MESSAGES and config.messages_usage_policy == "strict":
                    raise ConversionError("incremental Messages output requires input usage before the first event; this route cannot provide it")
                claude_beta = config.client_profile == "claude" and protocol == Protocol.MESSAGES and target_url.query == "beta=true"
                if target_url.query and protocol != config.upstream_protocol and not claude_beta:
                    raise ConversionError("query parameters cannot be translated between protocols")
                if protocol == config.upstream_protocol:
                    outbound = payload
                    namespace_map = None
                    tool_name_map = None
                    search_map = None
                else:
                    from .prompt_cache import CacheCheckpoint

                    cache_checkpoint = CacheCheckpoint()
                    if config.prompt_caching == "ephemeral":
                        payload = cache_checkpoint.prepare(payload, protocol, config)
                        cache_active = cache_checkpoint.hint is not None
                    namespace_map = None
                    tool_name_map = None
                    search_map = None
                    if protocol == Protocol.RESPONSES and config.client_tool_search == "function":
                        search_map = ClientToolSearchMap()
                        payload = search_map.prepare(payload)
                    mid_system = config.upstream_protocol == Protocol.MESSAGES and payload.get("model") in config.messages_system_models
                    if protocol == Protocol.RESPONSES and config.namespace_tools == "flatten":
                        namespace_map = NamespaceMap()
                        payload = namespace_map.prepare(payload)
                    if config.tool_names == "alias":
                        tool_name_map = ToolNameMap()
                        payload = tool_name_map.prepare(payload, protocol)
                    if config.client_profile == "codex" and protocol == Protocol.RESPONSES:
                        payload = prepare_codex_request(payload, config.upstream_protocol, default_max_output_tokens=config.default_max_output_tokens, messages_mid_system=mid_system)
                    elif config.client_profile == "claude" and protocol == Protocol.MESSAGES:
                        payload = prepare_claude_request(payload)
                    outbound = convert_request(payload, protocol, config.upstream_protocol, messages_mid_system=mid_system, public_reasoning=config.public_reasoning)
                    if config.prompt_caching == "ephemeral":
                        outbound = cache_checkpoint.restore(outbound)
                if not compact:
                    outbound = apply_upstream_reasoning(outbound, config.upstream_protocol, config.upstream_reasoning)
                semantic_outbound = outbound
                if config.provider_profiles is not None:
                    outbound = config.provider_profiles.apply(config.upstream_base_url, config.upstream_protocol, outbound)
                if config.capabilities is not None:
                    from .evidence_identity import evidence_options

                    config.capabilities.preflight(config.upstream_base_url, config.upstream_protocol, outbound, require_verified=config.require_verified_capabilities)
                    if semantic_outbound is not outbound:
                        config.capabilities.preflight(config.upstream_base_url, config.upstream_protocol, semantic_outbound, require_verified=config.require_verified_capabilities)
                    if cache_active:
                        config.capabilities.assess(config.upstream_base_url, config.upstream_protocol, outbound.get("model"), {"prompt_caching"}).require(verified=config.require_verified_capabilities)
                    if compact:
                        config.capabilities.assess(config.upstream_base_url, config.upstream_protocol, outbound.get("model"), {"compaction"}).require(verified=config.require_verified_capabilities)
                    config.capabilities.assess_route(config.upstream_base_url, config.upstream_protocol, outbound.get("model"), request_features(payload, protocol) | request_features(outbound, config.upstream_protocol) | request_features(semantic_outbound, config.upstream_protocol) | ({"compaction"} if compact else set()) | ({"prompt_caching"} if cache_active else set()),
                                                     source_protocol=protocol, streaming=streaming, client_profile=config.client_profile, policies=route_policies(config), **evidence_options(config)).require(verified=config.require_verified_routes or config.require_current_evidence or config.evidence_max_age_days is not None)
                if config.context_preflight != "off" and not compact:
                    record = config.capabilities.lookup(config.upstream_base_url, config.upstream_protocol, outbound.get("model")) if config.capabilities else None
                    assessment = assess_context(outbound, config.upstream_protocol, record.context_window_tokens if record else None, policy=config.context_preflight, counter=config.token_counter)
                    self._context_headers = assessment.headers()
                token_quota = None
                token_amount = None
                if config.shared_token_limit is not None:
                    from .token_quota import (
                        SharedTokenQuota,
                        input_reserve,
                        output_reserve,
                    )

                    if compact:
                        raise ConversionError("token quota cannot account for opaque compaction input")
                    if not isinstance(outbound.get("model"), str) or not outbound["model"]:
                        raise ConversionError("token quota requires an exact model ID")
                    token_amount = input_reserve(config, outbound).tokens + output_reserve(semantic_outbound, config.upstream_protocol)
                    token_quota = SharedTokenQuota(config.shared_admission_db, _prefix(config.upstream_base_url) + "\0" + config.upstream_api_key, outbound["model"], limit=config.shared_token_limit, window=config.shared_window, lease=config.total_timeout + config.timeout + 5)
                from .routing import apply_openrouter

                outbound = apply_openrouter(outbound, config.openrouter_provider, route_session_id)
                if streaming and config.upstream_protocol == Protocol.CHAT and protocol != Protocol.CHAT:
                    outbound["stream_options"] = {"include_usage": True}
                data = encode_json(outbound)
            except RecursionError:
                self._error(422, "request nesting exceeds the supported depth", protocol, headers={"x-should-retry": "false"})
                return
            except UnsupportedFeatureError as exc:
                self._error(422, str(exc), protocol, code="unsupported_feature", headers={"x-should-retry": "false"})
                return
            except (json.JSONDecodeError, UnicodeError, ConversionError) as exc:
                self._error(422, str(exc), protocol, headers={"x-should-retry": "false"})
                return
            headers = {"Content-Type": "application/json", "Accept": "text/event-stream" if streaming else "application/json"}
            if config.auth_style == "bearer":
                headers["Authorization"] = f"Bearer {config.upstream_api_key}"
            else:
                headers["x-api-key"] = config.upstream_api_key
            if config.upstream_protocol == Protocol.MESSAGES:
                headers["anthropic-version"] = self.headers.get("anthropic-version", "2023-06-01")
                if protocol == Protocol.MESSAGES and self.headers.get("anthropic-beta"):
                    headers["anthropic-beta"] = self.headers["anthropic-beta"]
            upstream_url = config.upstream_url + ("/compact" if compact else "") + ("?" + target_url.query if target_url.query and protocol == config.upstream_protocol else "")
            request = Request(upstream_url, data=data, headers=headers, method="POST")

            def restore_response(result):
                if tool_name_map is not None:
                    result = tool_name_map.restore(result, protocol)
                mapped = namespace_map.restore(result) if namespace_map else copy.deepcopy(result)
                if search_map is not None:
                    mapped = search_map.restore(mapped)
                if protocol == Protocol.RESPONSES:
                    from .response_context import restore_request_context

                    mapped = restore_request_context(mapped, response_request)
                return mapped

            lease = None
            watch = None
            token_leases = []
            successful_token_lease = None

            def open_accounted(request, *, timeout):
                identifier = token_quota.reserve(token_amount) if token_quota is not None else None
                if identifier is not None:
                    token_leases.append(identifier)
                response = open_upstream(request, timeout=timeout)
                nonlocal successful_token_lease
                successful_token_lease = identifier
                return response

            try:
                budget = self._budget
                watch = DisconnectWatch(self.connection, budget.cancel)
                watch.__enter__()
                request.protoverta_budget = budget
                response, lease = open_with_retry(open_accounted, request, budget, attempts=config.retry_attempts, max_wait=config.retry_max_wait, admission=shared_admission)
                with response:
                    budget.remaining()
                    if streaming:
                        transform = restore_response if protocol == Protocol.RESPONSES and protocol != config.upstream_protocol else None
                        self._stream(response, config.upstream_protocol, protocol, transform, tool_name_map.decode if tool_name_map is not None else None, budget, search_map.stream_delta if search_map is not None else None)
                        return
                    raw = bytearray()
                    while len(raw) <= config.max_body_bytes:
                        chunk = budget.read(response, min(4096, config.max_body_bytes + 1 - len(raw)), streaming=True)
                        if not chunk:
                            break
                        raw.extend(chunk)
                    if len(raw) > config.max_body_bytes:
                        raise ConversionError("upstream response is too large")
                    result = decode_json(raw)
                    if not isinstance(result, dict):
                        raise ConversionError("upstream returned a non-object JSON response")
                    try:
                        usage = _decode_usage(result.get("usage", {}), config.upstream_protocol)
                        self._trace.usage(usage["input_tokens"], usage["output_tokens"], usage["cached_input_tokens"], usage["cache_creation_input_tokens"])
                    except (ValueError, TypeError, AttributeError):
                        pass  # Diagnostics must never reject a native response.
                    if protocol != config.upstream_protocol:
                        result = convert_response(result, config.upstream_protocol, protocol, public_reasoning=config.public_reasoning)
                        result = restore_response(result)
                    self._json(200, result, getattr(response, "headers", None))
            except AdmissionDenied as exc:
                self._error(429, str(exc), protocol, headers={"Retry-After": str(exc.retry_after)})
                self._trace.outcome = "shared_admission"
            except ClientDisconnected:
                self._trace.outcome = "client_disconnected"
                return
            except (AdmissionStorageError, AdmissionPolicyError):
                self._error(503, "Shared provider admission is unavailable or has conflicting policy.", protocol, headers={"x-should-retry": "false"})
                self._trace.outcome = "admission_unavailable"
            except HTTPError as exc:
                try:
                    raw = bytearray()
                    while len(raw) <= config.max_body_bytes:
                        chunk = budget.read(exc, min(4096, config.max_body_bytes + 1 - len(raw)), streaming=True)
                        if not chunk:
                            break
                        raw.extend(chunk)
                    if len(raw) > config.max_body_bytes:
                        raise ValueError("upstream error is too large")
                    error = decode_json(raw)
                    if not isinstance(error, dict) or not isinstance(error.get("error"), dict):
                        raise TypeError("not an API error object")
                    if protocol == config.upstream_protocol and 400 <= exc.code < 600:
                        self._raw_json(exc.code, bytes(raw), exc.headers)
                    else:
                        detail = error["error"]
                        message = detail.get("message")
                        self._error(exc.code, message if isinstance(message, str) else f"upstream HTTP {exc.code}", protocol, code=detail.get("code") if isinstance(detail.get("code"), str) else None, headers=exc.headers)
                except (ValueError, TypeError, UnicodeError, OSError, HTTPException, RecursionError):
                    self._error(exc.code, f"upstream HTTP {exc.code}", protocol, headers=exc.headers)
                finally:
                    self._trace.outcome = "upstream_http"
                    exc.close()
            except (URLError, TimeoutError, OSError, HTTPException) as exc:
                timeout = isinstance(exc, TimeoutError) or isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError)
                cause = exc.reason if isinstance(exc, URLError) else exc
                self._error(504 if timeout else 502, f"upstream transport error: {type(exc).__name__}", protocol, code=cause.kind if isinstance(cause, DeadlineExceeded) else "upstream_timeout" if timeout else "upstream_transport", headers={"x-should-retry": "false"} if isinstance(cause, DeadlineExceeded) else None)
            except UnsupportedFeatureError as exc:
                self._error(422, str(exc), protocol, code="unsupported_feature", headers={"x-should-retry": "false"})
            except (json.JSONDecodeError, UnicodeError, ConversionError, StreamError, RecursionError) as exc:
                self._error(502, str(exc), protocol)
                self._trace.outcome = "invalid_response"
            finally:
                if token_quota is not None:
                    for identifier in token_leases:
                        try:
                            usage = (self._trace.input_tokens, self._trace.output_tokens) if identifier == successful_token_lease and self._trace.outcome == "success" else (None, None)
                            token_quota.reconcile(identifier, *usage)
                        except AdmissionStorageError:
                            pass  # Crash/storage failure retains a finite conservative lease.
                if watch is not None:
                    watch.__exit__(None, None, None)
                if shared_admission is not None and lease is not None:
                    try:
                        shared_admission.release(lease)
                    except AdmissionStorageError:
                        pass  # A crashed/busy writer cannot retain a lease beyond its TTL.

    return Handler


class GatewayHTTPServer(ThreadingHTTPServer):
    """Bound accepted handler threads, including clients not yet authenticated."""

    daemon_threads = True

    def __init__(self, address, handler):
        self._connections = threading.BoundedSemaphore(handler.connection_limit)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self._connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._connections.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connections.release()


def serve(config: GatewayConfig, host: str = "127.0.0.1", port: int = 0, *, observer: Observer | None = None) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("this version only binds to 127.0.0.1")
    server = GatewayHTTPServer((host, port), make_handler(config, observer=observer))
    server.daemon_threads = True
    return server


@contextmanager
def gateway_session(config: GatewayConfig, *, observer: Observer | None = None) -> Iterator[str]:
    """Serve one CLI session on loopback and always release its listener."""
    server = serve(config, observer=observer)
    thread = threading.Thread(target=server.serve_forever, name="protoverta-gateway", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
