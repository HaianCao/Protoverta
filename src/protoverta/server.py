"""Local HTTP gateway for completed JSON and capability-gated SSE requests."""

from __future__ import annotations

import copy
import json
import math
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .capabilities import CapabilityRegistry
from .core import ConversionError, Protocol, convert_request, convert_response
from .failures import gateway_failure
from .namespaces import NamespaceMap
from .profiles import prepare_claude_request, prepare_codex_request
from .sse import encode_sse
from .stream_pipe import StreamTranscoder, StreamValidator
from .streaming import StreamError, UnsupportedReasoningError
from .tool_names import ToolNameMap

_PATHS = {
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

    def __post_init__(self) -> None:
        if not isinstance(self.public_reasoning, bool):
            raise TypeError("public_reasoning must be a boolean")
        if self.tool_names is None:
            object.__setattr__(self, "tool_names", "alias" if self.client_profile in {"codex", "claude"} else "reject")
        if self.tool_names not in {"reject", "alias"}:
            raise ValueError("tool_names must be reject or alias")
        object.__setattr__(self, "upstream_protocol", Protocol(self.upstream_protocol))
        parsed = urlsplit(self.upstream_base_url)
        _ = parsed.port
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
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, int | float) or not math.isfinite(self.timeout) or self.timeout <= 0 or type(self.max_body_bytes) is not int or self.max_body_bytes <= 0:
            raise ValueError("timeout and max_body_bytes must be positive")
        if type(self.max_concurrent_requests) is not int or self.max_concurrent_requests <= 0:
            raise ValueError("max_concurrent_requests must be a positive integer")
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


def make_handler(config: GatewayConfig, *, upstream_open=None) -> type[BaseHTTPRequestHandler]:
    opener = build_opener(_RedirectBlocker())
    open_upstream = upstream_open or opener.open
    admission = threading.BoundedSemaphore(config.max_concurrent_requests)

    class Handler(BaseHTTPRequestHandler):
        server_version = "Protoverta/0.3"

        def log_message(self, format: str, *args: object) -> None:
            # Avoid logging paths, request bodies, or credentials by default.
            return

        def _json(self, status: int, payload: dict[str, object], headers=None) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._raw_json(status, raw, headers)

        def _error(self, status, message, protocol, *, code=None, headers=None):
            failure = gateway_failure(status, message, code=code, secrets=(config.upstream_api_key, config.inbound_token))
            self._json(failure.status, failure.payload(protocol), headers)

        def _forward_headers(self, headers) -> None:
            for name, value in (headers.items() if headers else []):
                lower = name.lower()
                allowed = lower in {"retry-after", "x-should-retry", "request-id", "x-request-id", "x-accel-buffering"} or lower.startswith("anthropic-ratelimit-")
                if allowed and "\r" not in str(value) and "\n" not in str(value):
                    self.send_header(name, value)

        def _raw_json(self, status: int, raw: bytes, headers=None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self._forward_headers(headers)
            self.end_headers()
            self.wfile.write(raw)

        def _stream(self, response, source: Protocol, target: Protocol, response_transform=None, tool_name_transform=None) -> None:
            pipeline = StreamTranscoder(source, target, messages_usage_policy=config.messages_usage_policy, max_pending_bytes=config.max_body_bytes, response_transform=response_transform, public_reasoning=config.public_reasoning, tool_name_transform=tool_name_transform) if source != target else StreamValidator(source)
            headers_sent = False
            initial = bytearray()

            def send_headers() -> None:
                nonlocal headers_sent
                if not headers_sent:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Accel-Buffering", "no")
                    self._forward_headers(getattr(response, "headers", None))
                    self.end_headers()
                    headers_sent = True

            try:
                while True:
                    chunk = response.read1(4096) if hasattr(response, "read1") else response.read(1)
                    if not chunk:
                        break
                    output = pipeline.feed(chunk)
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
                        self.wfile.write(output)
                        self.wfile.flush()
                tail = pipeline.finish()
                if initial:
                    tail = bytes(initial) + tail
                    initial.clear()
                if tail:
                    send_headers()
                    self.wfile.write(tail)
                    self.wfile.flush()
                send_headers()
            except (StreamError, UnicodeError, OSError) as exc:
                unsupported = isinstance(exc, UnsupportedReasoningError)
                message = f"upstream stream error: {type(exc).__name__}"
                if unsupported:
                    hint = "Messages requires its own thinking/signature contract; use native Messages or a compatible Responses route." if target == Protocol.MESSAGES else "For unsigned Chat reasoning_content, enable public_reasoning explicitly on a Chat/Responses route; summaries, signed or encrypted state remain unsupported."
                    message = "Upstream reasoning cannot be preserved on this route. " + hint + " No reasoning was dropped or given a fabricated signature."
                timeout = isinstance(exc, TimeoutError)
                failure = gateway_failure(422 if unsupported else 504 if timeout else 502, message, code="unsupported_reasoning" if unsupported else "upstream_timeout" if timeout else "invalid_stream")
                payload = failure.payload(target)
                try:
                    if not headers_sent:
                        self._json(failure.status, payload, {"x-should-retry": "false"} if unsupported else None)
                    else:
                        sequence = pipeline.encoder.sequence if isinstance(pipeline, StreamTranscoder) else pipeline.decoder.sequence + 1
                        error = json.dumps(failure.stream_payload(target, sequence_number=max(0, sequence)))
                        self.wfile.write(encode_sse("error", error))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def do_POST(self) -> None:
            target_url = urlsplit(self.path)
            protocol = _PATHS.get(target_url.path)
            if protocol is None:
                self._error(404, "unsupported endpoint", Protocol.CHAT)
                return
            token = self.headers.get("Authorization", "")
            if not token.startswith("Bearer ") or not secrets.compare_digest(token[7:], config.inbound_token):
                self._error(401, "unauthorized", protocol)
                return
            if not admission.acquire(blocking=False):
                self._error(503, "Local gateway request capacity reached; retry after an active request finishes.", protocol, headers={"Retry-After": "1"})
                return
            try:
                self.connection.settimeout(config.timeout)
                self._handle_post(protocol, target_url)
            except TimeoutError:
                self._error(408, "Client request body read timed out.", protocol, headers={"x-should-retry": "false"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                admission.release()

        def _handle_post(self, protocol, target_url) -> None:
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._error(411, "Content-Length is required", protocol)
                return
            if length < 0 or length > config.max_body_bytes:
                self._error(413, "request body is too large", protocol)
                return
            try:
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ConversionError("request body must be a JSON object")
                streaming = payload.get("stream", False)
                if not isinstance(streaming, bool):
                    raise ConversionError("stream must be a boolean")
                if streaming and protocol == Protocol.MESSAGES and config.upstream_protocol != Protocol.MESSAGES and config.messages_usage_policy == "strict":
                    raise ConversionError("incremental Messages output requires input usage before the first event; this route cannot provide it")
                claude_beta = config.client_profile == "claude" and protocol == Protocol.MESSAGES and target_url.query == "beta=true"
                if target_url.query and protocol != config.upstream_protocol and not claude_beta:
                    raise ConversionError("query parameters cannot be translated between protocols")
                if protocol == config.upstream_protocol:
                    outbound = payload
                    namespace_map = None
                    tool_name_map = None
                else:
                    namespace_map = None
                    tool_name_map = None
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
                outbound = apply_upstream_reasoning(outbound, config.upstream_protocol, config.upstream_reasoning)
                if config.capabilities is not None:
                    config.capabilities.preflight(config.upstream_base_url, config.upstream_protocol, outbound, require_verified=config.require_verified_capabilities)
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
            data = json.dumps(outbound, ensure_ascii=False).encode("utf-8")
            if streaming and config.upstream_protocol == Protocol.CHAT and protocol != Protocol.CHAT:
                outbound["stream_options"] = {"include_usage": True}
                data = json.dumps(outbound, ensure_ascii=False).encode("utf-8")
            upstream_url = config.upstream_url + ("?" + target_url.query if target_url.query and protocol == config.upstream_protocol else "")
            request = Request(upstream_url, data=data, headers=headers, method="POST")

            def restore_response(result):
                if tool_name_map is not None:
                    result = tool_name_map.restore(result, protocol)
                mapped = namespace_map.restore(result) if namespace_map else copy.deepcopy(result)
                # Generated Responses snapshots must describe the requested
                # format instead of incorrectly echoing plain-text defaults.
                if protocol == Protocol.RESPONSES and "text" in payload:
                    snapshot = mapped.get("response", mapped if "output" in mapped else None)
                    if isinstance(snapshot, dict):
                        snapshot["text"] = copy.deepcopy(payload["text"])
                if protocol == Protocol.RESPONSES:
                    snapshot = mapped.get("response", mapped if "output" in mapped else None)
                    if isinstance(snapshot, dict):
                        for option in ("top_p", "temperature"):
                            if payload.get(option) is not None:
                                snapshot[option] = payload[option]
                return mapped

            try:
                with open_upstream(request, timeout=config.timeout) as response:
                    if streaming:
                        transform = restore_response if protocol == Protocol.RESPONSES and (namespace_map or "text" in payload or any(payload.get(option) is not None for option in ("top_p", "temperature"))) else None
                        self._stream(response, config.upstream_protocol, protocol, transform, tool_name_map.decode if tool_name_map is not None else None)
                        return
                    raw = response.read(config.max_body_bytes + 1)
                    if len(raw) > config.max_body_bytes:
                        raise ConversionError("upstream response is too large")
                    result = json.loads(raw)
                    if not isinstance(result, dict):
                        raise ConversionError("upstream returned a non-object JSON response")
                    if protocol != config.upstream_protocol:
                        result = convert_response(result, config.upstream_protocol, protocol, public_reasoning=config.public_reasoning)
                        result = restore_response(result)
                    self._json(200, result, getattr(response, "headers", None))
            except HTTPError as exc:
                try:
                    raw = exc.read(config.max_body_bytes + 1)
                    if len(raw) > config.max_body_bytes:
                        raise ValueError("upstream error is too large")
                    error = json.loads(raw)
                    if not isinstance(error, dict) or not isinstance(error.get("error"), dict):
                        raise TypeError("not an API error object")
                    if protocol == config.upstream_protocol and 400 <= exc.code < 600:
                        self._raw_json(exc.code, raw, exc.headers)
                    else:
                        detail = error["error"]
                        message = detail.get("message")
                        self._error(exc.code, message if isinstance(message, str) else f"upstream HTTP {exc.code}", protocol, code=detail.get("code") if isinstance(detail.get("code"), str) else None, headers=exc.headers)
                except (ValueError, TypeError, UnicodeError, OSError):
                    self._error(exc.code, f"upstream HTTP {exc.code}", protocol, headers=exc.headers)
                finally:
                    exc.close()
            except (URLError, TimeoutError, OSError) as exc:
                timeout = isinstance(exc, TimeoutError) or isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError)
                self._error(504 if timeout else 502, f"upstream transport error: {type(exc).__name__}", protocol, code="upstream_timeout" if timeout else "upstream_transport")
            except (json.JSONDecodeError, UnicodeError, ConversionError, StreamError) as exc:
                self._error(502, str(exc), protocol)

    return Handler


def serve(config: GatewayConfig, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("this version only binds to 127.0.0.1")
    server = ThreadingHTTPServer((host, port), make_handler(config))
    server.daemon_threads = True
    return server


@contextmanager
def gateway_session(config: GatewayConfig) -> Iterator[str]:
    """Serve one CLI session on loopback and always release its listener."""
    server = serve(config)
    thread = threading.Thread(target=server.serve_forever, name="protoverta-gateway", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
