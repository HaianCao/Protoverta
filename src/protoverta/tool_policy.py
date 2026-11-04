"""Effective request permissions, checked before a translated tool is exposed."""

import copy

from .core import Protocol, _optional_list, _parallel_flag, _tool_choice
from .errors import ConversionError
from .streaming import StreamError


class ToolPolicyViolation(StreamError):
    """The upstream result violates this request's declared tool permissions."""


def normalize_tool_policy(body, protocol):
    """Apply gateway defaults to a copy; native forwarding never calls this."""
    protocol = Protocol(protocol)
    result = copy.deepcopy(body)
    tools = _optional_list(result.get("tools"), "tools")
    choice = _tool_choice(result.get("tool_choice"), protocol)
    if choice is None:
        choice = "auto" if tools else "none"
        result["tool_choice"] = {"type": choice} if protocol == Protocol.MESSAGES else choice
    if protocol == Protocol.MESSAGES:
        raw = result["tool_choice"]
        disabled = raw.get("disable_parallel_tool_use")
        if disabled is not None and type(disabled) is not bool:
            raise ConversionError("disable_parallel_tool_use must be a boolean")
        parallel = not disabled if disabled is not None else bool(tools) and choice != "none"
        if choice != "none":
            raw.setdefault("disable_parallel_tool_use", not parallel)
    else:
        parallel = _parallel_flag(result.get("parallel_tool_calls"))
        if parallel is None:
            parallel = bool(tools)
        result["parallel_tool_calls"] = parallel
    result["tools"] = tools
    return result, choice, parallel


class BoundToolPolicy:
    """One response only; names refer to the actual aliased upstream catalog."""

    def __init__(self, body, protocol):
        protocol = Protocol(protocol)
        self.allowed = set()
        for tool in body.get("tools", []):
            kind = tool.get("type", "function") if protocol != Protocol.MESSAGES else "function"
            definition = tool.get(kind, {}) if protocol == Protocol.CHAT else tool
            if kind not in {"function", "custom"} or not isinstance(definition.get("name"), str):
                raise ConversionError("tool policy requires a portable callable catalog")
            self.allowed.add((kind, definition["name"]))
        self.choice = _tool_choice(body.get("tool_choice"), protocol) or ("auto" if self.allowed else "none")
        if protocol == Protocol.MESSAGES:
            raw = body.get("tool_choice", {})
            self.parallel = not raw.get("disable_parallel_tool_use", False) if self.allowed and self.choice != "none" else False
        else:
            value = body.get("parallel_tool_calls")
            self.parallel = bool(self.allowed) if value is None else _parallel_flag(value)
        if self.choice == "required" and not self.allowed:
            raise ConversionError("required tool choice needs an available tool")
        if isinstance(self.choice, dict) and (self.choice["type"], self.choice["name"]) not in self.allowed:
            raise ConversionError("forced tool choice must name an available tool")
        self.count = 0

    def call(self, kind, name):
        if self.choice == "none":
            raise ToolPolicyViolation("upstream emitted a tool call while tool choice is none")
        if (kind, name) not in self.allowed:
            raise ToolPolicyViolation("upstream emitted a tool not authorized by this request")
        if isinstance(self.choice, dict) and (kind, name) != (self.choice["type"], self.choice["name"]):
            raise ToolPolicyViolation("upstream violated the forced tool choice")
        if self.count and not self.parallel:
            raise ToolPolicyViolation("upstream emitted multiple calls while parallel tool calls are disabled")
        self.count += 1

    def finish(self, *, truncated=False):
        if not truncated and self.count == 0 and (self.choice == "required" or isinstance(self.choice, dict)):
            raise ToolPolicyViolation("upstream completed without the required tool call")

    def observe_delta(self, delta):
        if delta.kind == "tool_start":
            self.call(delta.tool_type, delta.name)
        elif delta.kind == "finish":
            self.finish(truncated=delta.reason == "length")

    def check_result(self, body, protocol):
        protocol = Protocol(protocol)
        if protocol == Protocol.RESPONSES:
            for item in body["output"]:
                if item["type"] in {"function_call", "custom_tool_call"}:
                    self.call("custom" if item["type"] == "custom_tool_call" else "function", item["name"])
            truncated = body["status"] == "incomplete"
        elif protocol == Protocol.MESSAGES:
            for item in body["content"]:
                if item["type"] == "tool_use":
                    self.call("function", item["name"])
            truncated = body["stop_reason"] == "max_tokens"
        else:
            choice = body["choices"][0]
            for call in choice["message"].get("tool_calls", []):
                self.call(call["type"], call[call["type"]]["name"])
            truncated = choice["finish_reason"] == "length"
        self.finish(truncated=truncated)
