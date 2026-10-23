"""Local HTTP gateway for completed JSON and capability-gated SSE requests."""

from __future__ import annotations

import json
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .core import ConversionError, Protocol, convert_request, convert_response
from .profiles import prepare_claude_request, prepare_codex_request
from .sse import encode_sse
from .stream_pipe import StreamTranscoder, StreamValidator
from .streaming import StreamError

_PATHS = {
    "/v1/chat/completions": Protocol.CHAT,
    "/v1/messages": Protocol.MESSAGES,
    "/v1/responses": Protocol.RESPONSES,
}


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    upstream_protocol: Protocol
    upstream_base_url: str
    upstream_api_key: str
    inbound_token: str
    auth_style: str = "bearer"
    timeout: float = 120.0
    max_body_bytes: int = 8 * 1024 * 1024
    client_profile: str = "strict"
    default_max_output_tokens: int | None = None
    messages_usage_policy: str = "strict"

    def __post_init__(self) -> None:
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
        if self.timeout <= 0 or self.max_body_bytes <= 0:
            raise ValueError("timeout and max_body_bytes must be positive")
        if self.client_profile not in {"strict", "codex", "claude"}:
            raise ValueError("client_profile must be strict, codex or claude")
        if self.messages_usage_policy not in {"strict", "deferred"}:
            raise ValueError("messages_usage_policy must be strict or deferred")
        if self.default_max_output_tokens is not None and (isinstance(self.default_max_output_tokens, bool) or not isinstance(self.default_max_output_tokens, int) or self.default_max_output_tokens <= 0):
            raise ValueError("default_max_output_tokens must be a positive integer")

    @property
    def upstream_url(self) -> str:
        base = self.upstream_base_url.rstrip("/")
        path = {Protocol.CHAT: "/chat/completions", Protocol.MESSAGES: "/messages", Protocol.RESPONSES: "/responses"}[self.upstream_protocol]
        return base + path


class _RedirectBlocker(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def make_handler(config: GatewayConfig, *, upstream_open=None) -> type[BaseHTTPRequestHandler]:
    opener = build_opener(_RedirectBlocker())
    open_upstream = upstream_open or opener.open

    class Handler(BaseHTTPRequestHandler):
        server_version = "Protoverta/0.3"

        def log_message(self, format: str, *args: object) -> None:
            # Avoid logging paths, request bodies, or credentials by default.
            return

        def _json(self, status: int, payload: dict[str, object], headers=None) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._raw_json(status, raw, headers)

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

        def _stream(self, response, source: Protocol, target: Protocol) -> None:
            pipeline = StreamTranscoder(source, target, messages_usage_policy=config.messages_usage_policy, max_pending_bytes=config.max_body_bytes) if source != target else StreamValidator(source)
            headers_sent = False

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
                        send_headers()
                        self.wfile.write(output)
                        self.wfile.flush()
                tail = pipeline.finish()
                if tail:
                    send_headers()
                    self.wfile.write(tail)
                    self.wfile.flush()
                send_headers()
            except (StreamError, UnicodeError, OSError) as exc:
                if not headers_sent:
                    self._json(502, {"error": {"message": f"upstream stream error: {type(exc).__name__}"}})
                else:
                    try:
                        error = json.dumps({"type": "error", "error": {"type": "api_error", "message": f"upstream stream error: {type(exc).__name__}"}})
                        self.wfile.write(encode_sse("error", error))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass

        def do_POST(self) -> None:
            target_url = urlsplit(self.path)
            protocol = _PATHS.get(target_url.path)
            if protocol is None:
                self._json(404, {"error": {"message": "unsupported endpoint"}})
                return
            token = self.headers.get("Authorization", "")
            if not token.startswith("Bearer ") or not secrets.compare_digest(token[7:], config.inbound_token):
                self._json(401, {"error": {"message": "unauthorized"}})
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._json(411, {"error": {"message": "Content-Length is required"}})
                return
            if length < 0 or length > config.max_body_bytes:
                self._json(413, {"error": {"message": "request body is too large"}})
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
                else:
                    if config.client_profile == "codex" and protocol == Protocol.RESPONSES:
                        payload = prepare_codex_request(payload, config.upstream_protocol, default_max_output_tokens=config.default_max_output_tokens)
                    elif config.client_profile == "claude" and protocol == Protocol.MESSAGES:
                        payload = prepare_claude_request(payload)
                    outbound = convert_request(payload, protocol, config.upstream_protocol)
            except (json.JSONDecodeError, UnicodeError, ConversionError) as exc:
                self._json(422, {"error": {"message": str(exc)}})
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
            try:
                with open_upstream(request, timeout=config.timeout) as response:
                    if streaming:
                        self._stream(response, config.upstream_protocol, protocol)
                        return
                    raw = response.read(config.max_body_bytes + 1)
                    if len(raw) > config.max_body_bytes:
                        raise ConversionError("upstream response is too large")
                    result = json.loads(raw)
                    if not isinstance(result, dict):
                        raise ConversionError("upstream returned a non-object JSON response")
                    if protocol != config.upstream_protocol:
                        result = convert_response(result, config.upstream_protocol, protocol)
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
                        self._json(exc.code if 400 <= exc.code < 600 else 502, {"type": "error", "error": {"type": "api_error", "message": str(detail.get("message", f"upstream HTTP {exc.code}"))}}, exc.headers)
                except (ValueError, TypeError, UnicodeError, OSError):
                    self._json(502 if not 400 <= exc.code < 600 else exc.code, {"error": {"message": f"upstream HTTP {exc.code}"}}, exc.headers)
                finally:
                    exc.close()
            except (URLError, TimeoutError, OSError) as exc:
                self._json(502, {"error": {"message": f"upstream transport error: {type(exc).__name__}"}})
            except (json.JSONDecodeError, UnicodeError, ConversionError, StreamError) as exc:
                self._json(502, {"error": {"message": str(exc)}})

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
