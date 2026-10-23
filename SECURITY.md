# Security policy

Protoverta's alpha gateway is local-only. Do not bind it to a public interface or place it behind a public reverse proxy. It accepts one fixed upstream base URL using HTTPS (or HTTP for loopback only), requires an inbound bearer token, blocks redirects, and does not log bodies or secrets by default.

Report vulnerabilities privately to the repository maintainer rather than posting credentials or exploit details in a public issue. Rotate any key that may have been exposed. The gateway does not store API keys or session content on disk.

The strict cross-protocol converter rejects unsupported content and state. Native forwarding preserves payloads after lifecycle validation. Optional Codex and Claude client profiles apply only the compatibility policies documented in the README; they do not provide equivalent hidden reasoning state across providers. Deferred Messages streaming uses provisional initial token counts and bounded pending-block buffering; do not use interim counts for accurate context or quota decisions. Upstream native API errors are returned to the authenticated local client, not logged; providers may include request details in their error messages.
