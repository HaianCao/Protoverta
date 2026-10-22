"""Local HTTP gateway for completed JSON and capability-gated SSE requests."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .core import ConversionError, Protocol, convert_request, convert_response
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

    def __post_init__(self) -> None:
        parsed = urlsplit(self.upstream_base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("upstream_base_url must be an HTTPS origin or /v1 base URL")
        if parsed.path.rstrip("/") not in {"", "/v1"}:
            raise ValueError("upstream_base_url must end at the origin or /v1")
        if self.auth_style not in {"bearer", "x-api-key"}:
            raise ValueError("auth_style must be bearer or x-api-key")
        if not self.upstream_api_key or not self.inbound_token:
            raise ValueError("both upstream key and inbound token are required")
        if any(char in self.upstream_api_key + self.inbound_token for char in "\r\n"):
            raise ValueError("credentials cannot contain line breaks")
        if self.timeout <= 0 or self.max_body_bytes <= 0:
            raise ValueError("timeout and max_body_bytes must be positive")

    @property
    def upstream_url(self) -> str:
        base = self.upstream_base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        path = {Protocol.CHAT: "/chat/completions", Protocol.MESSAGES: "/messages", Protocol.RESPONSES: "/responses"}[self.upstream_protocol]
        return base + path


class _RedirectBlocker(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def make_handler(config: GatewayConfig, *, upstream_open=None) -> type[BaseHTTPRequestHandler]:
    opener = build_opener(_RedirectBlocker())
    open_upstream = upstream_open or opener.open

    class Handler(BaseHTTPRequestHandler):
        server_version = "Protoverta/0.2"

        def log_message(self, format: str, *args: object) -> None:
            # Avoid logging paths, request bodies, or credentials by default.
            return

        def _json(self, status: int, payload: dict[str, object]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _stream(self, response, source: Protocol, target: Protocol) -> None:
            pipeline = StreamTranscoder(source, target) if source != target else StreamValidator(source)
            headers_sent = False

            def send_headers() -> None:
                nonlocal headers_sent
                if not headers_sent:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Accel-Buffering", "no")
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
                        error = json.dumps({"error": {"message": f"upstream stream error: {type(exc).__name__}"}})
                        self.wfile.write(encode_sse("error", error))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass

        def do_POST(self) -> None:
            protocol = _PATHS.get(self.path)
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
                if streaming and protocol == Protocol.MESSAGES and config.upstream_protocol != Protocol.MESSAGES:
                    raise ConversionError("incremental Messages output requires input usage before the first event; this route cannot provide it")
                if protocol == config.upstream_protocol:
                    outbound = payload
                else:
                    outbound = convert_request(payload, protocol, config.upstream_protocol)
            except (json.JSONDecodeError, ConversionError) as exc:
                self._json(422, {"error": {"message": str(exc)}})
                return
            headers = {"Content-Type": "application/json", "Accept": "text/event-stream" if streaming else "application/json"}
            if config.auth_style == "bearer":
                headers["Authorization"] = f"Bearer {config.upstream_api_key}"
            else:
                headers["x-api-key"] = config.upstream_api_key
                headers["anthropic-version"] = "2023-06-01"
            data = json.dumps(outbound, ensure_ascii=False).encode("utf-8")
            if streaming and config.upstream_protocol == Protocol.CHAT and protocol != Protocol.CHAT:
                outbound["stream_options"] = {"include_usage": True}
                data = json.dumps(outbound, ensure_ascii=False).encode("utf-8")
            request = Request(config.upstream_url, data=data, headers=headers, method="POST")
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
                    self._json(200, result)
            except HTTPError as exc:
                self._json(exc.code if 400 <= exc.code < 600 else 502, {"error": {"message": f"upstream HTTP {exc.code}"}})
            except (URLError, TimeoutError, OSError) as exc:
                self._json(502, {"error": {"message": f"upstream transport error: {type(exc).__name__}"}})
            except (json.JSONDecodeError, ConversionError, StreamError) as exc:
                self._json(502, {"error": {"message": str(exc)}})

    return Handler


def serve(config: GatewayConfig, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("this version only binds to 127.0.0.1")
    return ThreadingHTTPServer((host, port), make_handler(config))
