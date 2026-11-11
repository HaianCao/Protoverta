"""Opt-in synthetic probe diagnostics; no payloads, IDs, headers or error text.

Instrumentation observes the production pipeline and re-raises its original
exceptions without rewriting requests or stream bytes. An explicit probe budget
can stop additional upstream I/O; it does not represent the provider's quota.
"""

from __future__ import annotations

import copy
import io
import json
from collections import Counter
from contextlib import ExitStack, contextmanager
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import build_opener

from protoverta import server
from protoverta.sse import SSEParser
from protoverta.stream_encoder import StreamEncoder
from protoverta.stream_pipe import (
    StreamTranscoder,
    StreamValidator,
    _LifecycleValidator,
    _MessagesOrder,
)
from protoverta.streaming import StreamDecoder, StreamError, UnsupportedReasoningError

_DELTA_FIELDS = {
    "role",
    "content",
    "tool_calls",
    "refusal",
    "annotations",
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "audio",
    "function_call",
}
_TOOL_FIELDS = {"index", "id", "type", "function"}
_FUNCTION_FIELDS = {"name", "arguments"}
_USAGE_FIELDS = {
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_tokens_details",
    "completion_tokens_details",
}
_KINDS = {"start", "text", "reasoning_text", "tool_start", "tool_delta", "tool_end", "finish"}
_REASONS = {"stop", "tool_calls", "length"}


class ProbeBudgetExceeded(HTTPError):
    """A local probe limit, distinct from an upstream failure or rate limit."""

    def __init__(self):
        message = "Isolated probe request budget exhausted; no additional upstream request was sent."
        body = json.dumps(
            {"error": {"type": "invalid_request_error", "code": "probe_request_budget", "message": message}}
        ).encode()
        super().__init__("http://127.0.0.1", 422, message, {"x-should-retry": "false"}, io.BytesIO(body))


def _fields(value, allowed):
    if not isinstance(value, dict):
        return {"object": False}
    return {"object": True, "known_fields": sorted(set(value) & allowed), "unknown_fields": bool(set(value) - allowed)}


def _string_shape(value):
    return "absent" if value is None else "empty" if value == "" else "string" if isinstance(value, str) else "other"


def chat_shape(event):
    """Use fixed vocabulary even for malicious field names and malformed JSON."""
    if event.data == "[DONE]":
        return {"kind": "done"}
    try:
        body = json.loads(event.data)
    except (ValueError, TypeError):
        return {"kind": "invalid_json"}
    if not isinstance(body, dict):
        return {"kind": "non_object"}
    if body.get("object") != "chat.completion.chunk":
        return {"kind": "other"}
    shape = {"kind": "chat_chunk", "usage": _fields(body.get("usage"), _USAGE_FIELDS)}
    choices = body.get("choices")
    shape["choices_array"] = isinstance(choices, list)
    if not isinstance(choices, list):
        return shape
    shape["choices_count"] = min(len(choices), 4)
    shape["choices_truncated"] = len(choices) > 4
    shape["choices"] = []
    for choice in choices[:4]:
        if not isinstance(choice, dict):
            shape["choices"].append({"object": False})
            continue
        reason = choice.get("finish_reason")
        delta = choice.get("delta")
        item = {
            "finish_reason": reason
            if isinstance(reason, str) and reason in _REASONS
            else "none"
            if reason is None
            else "other",
            "delta": _fields(delta, _DELTA_FIELDS),
        }
        tools = delta.get("tool_calls") if isinstance(delta, dict) else None
        if isinstance(tools, list):
            item["tools_truncated"] = len(tools) > 4
            item["tools"] = []
            for tool in tools[:4]:
                tool_shape = _fields(tool, _TOOL_FIELDS)
                if isinstance(tool, dict):
                    function = tool.get("function")
                    tool_shape.update(id=_string_shape(tool.get("id")), function=_fields(function, _FUNCTION_FIELDS))
                    if isinstance(function, dict):
                        tool_shape.update(
                            name=_string_shape(function.get("name")), arguments=_string_shape(function.get("arguments"))
                        )
                item["tools"].append(tool_shape)
        shape["choices"].append(item)
    return shape


def error_category(exc):
    if isinstance(exc, ProbeBudgetExceeded):
        return "probe_request_budget"
    if isinstance(exc, UnsupportedReasoningError):
        return "unsupported_reasoning"
    if isinstance(exc, HTTPError):
        return "upstream_http"
    if isinstance(exc, TimeoutError) or isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError):
        return "upstream_timeout"
    if isinstance(exc, URLError) or isinstance(exc, OSError) and not isinstance(exc, UnicodeError):
        return "upstream_transport"
    if isinstance(exc, UnicodeError):
        return "invalid_utf8"
    if isinstance(exc, (StreamError, ValueError)):
        return "invalid_stream"
    return "unexpected_error"


class _ObservedResponse:
    def __init__(self, response, diagnostics):
        self.response = response
        self.diagnostics = diagnostics

    def __enter__(self):
        self.response.__enter__()
        return self

    def __exit__(self, *args):
        return self.response.__exit__(*args)

    def __getattr__(self, name):
        original = getattr(self.response, name)
        if name not in {"read", "read1"}:
            return original

        def read(*args, **kwargs):
            try:
                chunk = original(*args, **kwargs)
            except Exception as exc:
                self.diagnostics.failure("transport", "read", exc)
                raise
            self.diagnostics.bytes_read += len(chunk)
            if not chunk:
                self.diagnostics.eof_count += 1
            return chunk

        return read


class StreamDiagnostics:
    """Bounded per-probe stage counters and first/last Chat frame shapes."""

    def __init__(self, *, max_records=8, max_upstream_requests=None):
        if type(max_records) is not int or not 1 <= max_records <= 32:
            raise ValueError("max_records must be between 1 and 32")
        if max_upstream_requests is not None and (type(max_upstream_requests) is not int or max_upstream_requests <= 0):
            raise ValueError("max_upstream_requests must be a positive integer")
        self.limit = max_records
        self.max_upstream_requests = max_upstream_requests
        self.calls = Counter()
        self.failures = []
        self.dropped_failures = 0
        self.frames = []
        self.frame_count = 0
        self.last_frame = None
        self.last_usage_frame = None
        self.last_delta = None
        self.states = {}
        self.requests = 0
        self.http_statuses = []
        self.retry_after = None
        self.bytes_read = 0
        self.eof_count = 0

    def failure(self, stage, operation, exc):
        if len(self.failures) < self.limit:
            self.failures.append({"stage": stage, "operation": operation, "category": error_category(exc)})
        else:
            self.dropped_failures += 1

    def _method(self, stage, operation, original):
        def observe(owner, *args, **kwargs):
            self.calls[(stage, operation)] += 1
            if stage in {"decoder", "native_decoder"} and operation == "feed":
                self.frame_count += 1
                self.last_frame = chat_shape(args[0])
                if len(self.frames) < self.limit:
                    self.frames.append(self.last_frame)
                if self.last_frame.get("usage", {}).get("object"):
                    self.last_usage_frame = self.last_frame
            if stage == "encoder" and operation == "feed":
                delta = args[0]
                self.last_delta = {
                    "kind": delta.kind if delta.kind in _KINDS else "other",
                    "input_tokens_present": delta.input_tokens is not None,
                    "output_tokens_present": delta.output_tokens is not None,
                }
            try:
                return original(owner, *args, **kwargs)
            except Exception as exc:
                self.failure(stage, operation, exc)
                raise
            finally:
                if stage in {"decoder", "encoder", "native_decoder"}:
                    self.states[stage] = {"started": bool(owner.started), "ended": bool(owner.ended)}

        return observe

    def wrap_open(self, original):
        def open_upstream(request, *, timeout):
            if self.max_upstream_requests is not None and self.requests >= self.max_upstream_requests:
                exc = ProbeBudgetExceeded()
                self.failure("probe", "open", exc)
                raise exc
            self.requests += 1
            try:
                response = original(request, timeout=timeout)
            except Exception as exc:
                if isinstance(exc, HTTPError):
                    self._status(exc.code)
                    retry = exc.headers.get("Retry-After") if exc.headers else None
                    if isinstance(retry, str) and retry.isascii() and retry.isdigit() and len(retry) <= 8:
                        self.retry_after = int(retry)
                self.failure("transport", "open", exc)
                raise
            self._status(getattr(response, "status", None))
            return _ObservedResponse(response, self)

        return open_upstream

    def _status(self, value):
        if type(value) is int and 100 <= value < 600 and len(self.http_statuses) < self.limit:
            self.http_statuses.append(value)

    @contextmanager
    def capture(self, *, observe_transport=True):
        with ExitStack() as stack:
            for stage, owner in (
                ("parser", SSEParser),
                ("decoder", StreamDecoder),
                ("encoder", StreamEncoder),
                ("ordering", _MessagesOrder),
                ("native_decoder", _LifecycleValidator),
                ("pipeline", StreamTranscoder),
                ("native_pipeline", StreamValidator),
            ):
                for operation in ("feed", "finish"):
                    if hasattr(owner, operation):
                        stack.enter_context(
                            patch.object(owner, operation, self._method(stage, operation, getattr(owner, operation)))
                        )
            if observe_transport:
                original_handler = server.make_handler

                def make_handler(config, *, upstream_open=None, observer=None):
                    opener = upstream_open or build_opener(server._RedirectBlocker()).open
                    return original_handler(config, upstream_open=self.wrap_open(opener), observer=observer)

                stack.enter_context(patch.object(server, "make_handler", make_handler))
            yield self

    def snapshot(self):
        return copy.deepcopy(
            {
                "requests": self.requests,
                "max_upstream_requests": self.max_upstream_requests,
                "http_statuses": self.http_statuses,
                "retry_after_seconds": self.retry_after,
                "bytes_read": self.bytes_read,
                "eof_count": self.eof_count,
                "calls": [
                    {"stage": stage, "operation": operation, "count": count}
                    for (stage, operation), count in sorted(self.calls.items())
                ],
                "failures": self.failures,
                "dropped_failures": self.dropped_failures,
                "frame_count": self.frame_count,
                "first_frames": self.frames,
                "last_frame": self.last_frame,
                "last_usage_frame": self.last_usage_frame,
                "last_delta": self.last_delta,
                "states": self.states,
            }
        )
