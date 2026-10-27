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
    openrouter = (endpoint.scheme == "https" and endpoint.hostname == "openrouter.ai"
                  and endpoint.port in (None, 443) and endpoint.path.rstrip("/") == "/api/v1"
                  and endpoint.username is None and endpoint.password is None
                  and not endpoint.query and not endpoint.fragment)
    return ("openrouter_disabled" if openrouter and upstream != "messages" else "disabled"), False
