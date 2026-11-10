# Security policy

Protoverta's gateway is local-only. Do not bind it to a public interface or place it behind a public reverse proxy. It accepts one fixed upstream base URL using HTTPS (or HTTP for loopback only), requires an inbound bearer token, blocks redirects, and does not log bodies or secrets by default.

The gateway does not store API keys or session content on disk.

The strict cross-protocol converter rejects unsupported content and state. Native JSON routes forward protocol payloads without conversion; native SSE validates framing and lifecycle. An explicitly configured upstream reasoning policy can change either route. Optional Codex and Claude client profiles apply only the compatibility policies documented in the README; they do not provide equivalent hidden reasoning state across providers. Deferred Messages streaming uses provisional initial token counts and bounded pending-block buffering; do not use interim counts for accurate context or quota decisions. Upstream native API errors are returned to the authenticated local client, not logged; providers may include request details in their error messages.

## Supported versions

Security fixes are released for the latest minor version only.

| Version | Supported |
|---|---|
| 0.3.x | Yes |
| Older versions | No — update with the installer command or `pip install -U protoverta` |

## Reporting a vulnerability

Report vulnerabilities privately through
[GitHub private vulnerability reporting](https://github.com/HaianCao/Protoverta/security/advisories/new).
Do not post credentials or exploit details in a public issue. Rotate any key
that may have been exposed. You will get an answer within a week; a fix is
released as soon as it is ready, and the advisory is published with it.
