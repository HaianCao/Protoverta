"""Conservative UI status for a text-stream route, without network or writes."""

from dataclasses import fields
from typing import Any

from .capabilities import POLICY_VALUES
from .core import Protocol


def _count_status(protocol: Protocol | str, overrides: dict[str, Any] | None) -> tuple[str, str, str]:
    runtime = overrides or {}
    preflight = runtime.get("context_preflight", "off") or "off"
    count_mode = runtime.get("token_count_mode", "off") or "off"
    protocol = Protocol(protocol)
    count_category: str
    if count_mode == "native":
        count_category = "exact" if protocol in (Protocol.MESSAGES, Protocol.RESPONSES) else "unavailable"
    elif count_mode == "estimate" or preflight in ("estimate", "reject_estimate"):
        count_category = "estimate"
    else:
        count_category = "unavailable"
    return preflight, count_mode, count_category


def selection_status(
    registry: Any,
    base_url: str,
    protocol: Protocol | str,
    model: Any,
    *,
    source_protocol: Protocol | str,
    client_profile: str,
    overrides: dict[str, Any] | None = None,
    freshness_required: bool = False,
) -> dict[str, Any]:
    preflight, count_mode, count_category = _count_status(protocol, overrides)
    record = None
    if registry is not None and isinstance(model, str) and model:
        try:
            record = registry.lookup(base_url, protocol, model)
        except ValueError:
            record = None
    window = record.context_window_tokens if record is not None else None
    meta = {
        "context_preflight": preflight,
        "token_count_mode": count_mode,
        "count_category": count_category,
        "context_window_tokens": window,
    }
    count_note = f" Token count: {count_category}; context window: {window if window is not None else 'unavailable'}."
    unknown = {
        "status": "unverified",
        "message": "Unverified route. Model discovery is not capability certification." + count_note,
        **meta,
    }
    if registry is None:
        return unknown
    from .server import GatewayConfig

    defaults = {item.name: item.default for item in fields(GatewayConfig)}
    policies = {key: defaults[key] for key in POLICY_VALUES}
    policies["tool_names"] = "reject" if client_profile == "strict" else "alias"
    policies.update({key: value for key, value in (overrides or {}).items() if key in POLICY_VALUES})
    features = {"text", "streaming"}
    reports = [
        registry.assess(base_url, protocol, model, features),
        registry.assess_route(
            base_url,
            protocol,
            model,
            features,
            source_protocol=source_protocol,
            streaming=True,
            client_profile=client_profile,
            policies=policies,
        ),
    ]
    if any(report.unsupported for report in reports):
        return {
            "status": "unsupported",
            "message": "Text streaming is unsupported on this route according to configured evidence." + count_note,
            **meta,
        }
    if freshness_required or any(report.unknown for report in reports):
        return unknown
    return {
        "status": "verified",
        "message": "Text streaming verified. Tools, effort and context are checked separately at Run." + count_note,
        **meta,
    }
