"""Conservative UI status for a text-stream route, without network or writes."""

from dataclasses import fields

from .capabilities import POLICY_VALUES


def selection_status(registry, base_url, protocol, model, *, source_protocol, client_profile, overrides=None, freshness_required=False):
    unknown = {"status": "unverified", "message": "Unverified route. Model discovery is not capability certification."}
    if registry is None:
        return unknown
    from .server import GatewayConfig

    defaults = {item.name: item.default for item in fields(GatewayConfig)}
    policies = {key: defaults[key] for key in POLICY_VALUES}
    policies["tool_names"] = "reject" if client_profile == "strict" else "alias"
    policies.update({key: value for key, value in (overrides or {}).items() if key in POLICY_VALUES})
    features = {"text", "streaming"}
    reports = [registry.assess(base_url, protocol, model, features),
               registry.assess_route(base_url, protocol, model, features, source_protocol=source_protocol,
                                     streaming=True, client_profile=client_profile, policies=policies)]
    if any(report.unsupported for report in reports):
        return {"status": "unsupported", "message": "Text streaming is unsupported on this route according to configured evidence."}
    if freshness_required or any(report.unknown for report in reports):
        return unknown
    return {"status": "verified", "message": "Text streaming verified. Tools, effort and context are checked separately at Run."}
