"""Deterministic route compatibility, without probes or model-name guesses."""

from urllib.parse import urlsplit


def automatic_reasoning(client: str, upstream: str, base_url: str) -> tuple[str, bool]:
    """Return the effective request policy and unsigned-public-text mapping.

    This selects a representation, not a claim about a model's capabilities.
    A provider ignoring the disable request is still rejected by strict codecs.
    No retry, provider switch, signature creation or output filtering occurs.
    """
    protocols = {"chat", "messages", "responses"}
    if client not in protocols or upstream not in protocols:
        raise ValueError("Automatic reasoning requires a supported protocol pair")
    if client == upstream:
        return "provider_default", False
    if {client, upstream} == {"chat", "responses"}:
        return "provider_default", True
    endpoint = urlsplit(base_url)
    openrouter = (
        endpoint.scheme == "https"
        and endpoint.hostname == "openrouter.ai"
        and endpoint.port in (None, 443)
        and endpoint.path.rstrip("/") == "/api/v1"
        and endpoint.username is None
        and endpoint.password is None
        and not endpoint.query
        and not endpoint.fragment
    )
    return ("openrouter_disabled" if openrouter and upstream != "messages" else "disabled"), False


def automatic_route_reasoning(client: str, upstream: str) -> tuple[str, bool, str]:
    """Return ``(upstream_reasoning, public_reasoning, reasoning_output)`` for ``auto``.

    Live provider checks (2026-11-06) showed that disabling reasoning is not a
    portable request: some models reject ``reasoning_effort: none`` outright
    (HTTP 400, reasoning mandatory) and others ignore it and still stream
    ``reasoning_content``. ``auto`` therefore leaves the provider default in
    place and, on translated routes, drops reasoning output the client cannot
    represent. Unsigned public reasoning keeps flowing between Chat and
    Responses. Nothing is fabricated, retried or forwarded elsewhere; choose an
    explicit ``disabled`` policy to request less reasoning compute instead.
    """
    protocols = {"chat", "messages", "responses"}
    if client not in protocols or upstream not in protocols:
        raise ValueError("Automatic reasoning requires a supported protocol pair")
    if client == upstream:
        return "provider_default", False, "reject"
    return "provider_default", {client, upstream} == {"chat", "responses"}, "omit"
