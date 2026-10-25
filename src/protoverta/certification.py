"""Bounded, opt-in route probes with synthetic text and a pure local tool."""

import json
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from .capabilities import route_policies
from .core import Protocol, _decode_result, convert_request
from .evidence_identity import route_identity
from .resources import RequestBudget
from .server import GatewayHTTPServer, make_handler
from .sse import SSEParser
from .streaming import StreamDecoder
from .transport import DeadlineHTTPHandler


class _IncompleteCheck(ValueError):
    pass

CASES = ("text", "streaming", "tools", "streaming_tools", "tool_result", "history_replay")


@dataclass(frozen=True)
class CheckResult:
    case: str
    passed: bool
    outcome: str
    requests: int
    duration_ms: int


@contextmanager
def _listener(config, upstream_open):
    server = GatewayHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=upstream_open))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def check_route(config, model, source_protocol, *, cases=CASES, allow_live=False, upstream_open=None):
    """Never run a provider request implicitly; injected transport is fixture-only.

    history_replay is explicit transcript replay, NOT a real CLI resume test.
    The only executed tool returns a generated nonce and has no side effects.
    """
    source = Protocol(source_protocol)
    if type(allow_live) is not bool or upstream_open is not None and not callable(upstream_open):
        raise ValueError("probe transport/permission must be explicit")
    if upstream_open is None and not allow_live:
        raise ValueError("route probes require explicit allow_live (requests may incur cost)")
    if not isinstance(model, str) or not model or any(c.isspace() for c in model) or not cases or len(cases) != len(set(cases)) or set(cases) - set(CASES):
        raise ValueError("invalid exact model or probe case selection")
    results = []
    with _listener(config, upstream_open) as base:
        opener = build_opener(ProxyHandler({}), DeadlineHTTPHandler())
        for case in cases:
            started = time.monotonic()
            used = 0
            nonce = "nonce_" + secrets.token_hex(8)
            body = {"model": model, "messages": [{"role": "user", "content": "Reply with exactly " + nonce}], "max_tokens": 96}

            def send(value, expected_nonce=nonce):
                nonlocal used
                used += 1
                if used > 2:
                    raise ValueError("probe request budget exceeded")
                value = convert_request(value, Protocol.CHAT, source)
                suffix = {Protocol.CHAT: "/chat/completions", Protocol.MESSAGES: "/messages", Protocol.RESPONSES: "/responses"}[source]
                request = Request(base + "/v1" + suffix, data=json.dumps(value).encode(), headers={"Authorization": "Bearer " + config.inbound_token, "Content-Type": "application/json"})
                budget = RequestBudget(10, 30, 15)
                request.protoverta_budget = budget
                with opener.open(request, timeout=budget.remaining()) as response:
                    raw = bytearray()
                    while len(raw) <= 2 * 1024 * 1024:
                        chunk = budget.read(response, min(4096, 2 * 1024 * 1024 + 1 - len(raw)), streaming=True)
                        if not chunk:
                            break
                        raw.extend(chunk)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError("probe response budget exceeded")
                if value.get("stream"):
                    parser, decoder = SSEParser(), StreamDecoder(source, public_reasoning=config.public_reasoning)
                    deltas = [delta for event in parser.feed(bytes(raw)) for delta in decoder.feed(event)]
                    parser.finish(strict=True)
                    decoder.finish()
                    if any(delta.kind == "finish" and delta.reason == "length" for delta in deltas):
                        raise _IncompleteCheck()
                    if expected_nonce is not None and expected_nonce not in "".join(delta.text or "" for delta in deltas if delta.kind == "text"):
                        raise ValueError("stream did not preserve synthetic nonce")
                    return deltas
                result = _decode_result(json.loads(raw), source, public_reasoning=config.public_reasoning)
                if result.finish_reason == "length":
                    raise _IncompleteCheck()
                return result

            try:
                if case == "streaming":
                    send(body | {"stream": True})
                elif case in ("tools", "tool_result", "streaming_tools"):
                    body["tools"] = [{"type": "function", "function": {"name": "fixture_nonce", "description": "Return a synthetic nonce", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}]
                    body["tool_choice"] = {"type": "function", "function": {"name": "fixture_nonce"}}
                    body["messages"][0]["content"] = "Call fixture_nonce with {}. Then return its result exactly."
                    if case == "streaming_tools":
                        deltas = send(body | {"stream": True}, expected_nonce=None)
                        calls = [part for part in deltas if part.kind == "tool_start"]
                        arguments = "".join(part.text or "" for part in deltas if part.kind == "tool_delta")
                        if len(calls) != 1 or calls[0].name != "fixture_nonce" or not calls[0].call_id or json.loads(arguments) != {}:
                            raise ValueError("streaming tool contract not satisfied")
                    else:
                        result = send(body)
                        calls = [part for part in result.parts if part.kind == "tool_call"]
                        if len(calls) != 1 or calls[0].name != "fixture_nonce" or not calls[0].call_id or json.loads(calls[0].arguments) != {}:
                            raise ValueError("tool call contract not satisfied")
                    if case == "tool_result":
                        call = calls[0]
                        body.pop("tool_choice")
                        body["messages"] += [{"role": "assistant", "content": None, "tool_calls": [{"id": call.call_id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}]}, {"role": "tool", "tool_call_id": call.call_id, "content": nonce}]
                        result = send(body)
                        if nonce not in "".join(part.text for part in result.parts if part.kind == "text"):
                            raise ValueError("tool result was not used")
                else:
                    result = send(body)
                    text = "".join(part.text for part in result.parts if part.kind == "text")
                    if nonce not in text:
                        raise ValueError("text contract not satisfied")
                    if case == "history_replay":
                        body["messages"] += [{"role": "assistant", "content": text}, {"role": "user", "content": "Repeat the nonce from earlier exactly."}]
                        result = send(body)
                        if nonce not in "".join(part.text for part in result.parts if part.kind == "text"):
                            raise ValueError("history replay contract not satisfied")
                results.append(CheckResult(case, True, "passed", used, int((time.monotonic() - started) * 1000)))
            except _IncompleteCheck:
                results.append(CheckResult(case, False, "incomplete", used, int((time.monotonic() - started) * 1000)))
            except HTTPError as exc:
                status = exc.code
                exc.close()
                results.append(CheckResult(case, False, "http_" + str(status), used, int((time.monotonic() - started) * 1000)))
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                results.append(CheckResult(case, False, "contract_or_transport_failure", used, int((time.monotonic() - started) * 1000)))
    return [asdict(result) for result in results]


def evidence_report(config, model, source_protocol, results, *, verified_with):
    """Export only tested positive features. Failures never certify unsupported."""
    routes = []
    for streaming in (False, True):
        relevant = [item for item in results if (item["case"] in ("streaming", "streaming_tools")) == streaming and item["passed"] is True]
        features = {}
        for item in relevant:
            if item["case"] in ("text", "streaming"):
                features["text"] = True
            if item["case"] == "streaming":
                features["streaming"] = True
            if item["case"] == "streaming_tools":
                features["streaming"] = True
            if item["case"] in ("tools", "tool_result", "streaming_tools"):
                features.update(tools=True, forced_tool_choice=True)
        if features:
            routes.append({"base_url": config.upstream_base_url, "protocol": config.upstream_protocol.value, "model": model, "source_protocol": Protocol(source_protocol).value,
                           "streaming": streaming, "client_profile": config.client_profile, "policies": route_policies(config), "features": features,
                           "checked_at": date.today().isoformat(), "evidence": "bounded synthetic route checks; history_replay does not certify CLI resume", "verified_with": verified_with, "identity": route_identity(config)})
    return {"schema_version": 3, "models": [], "routes": routes}
