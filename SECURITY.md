# Security policy

Protoverta's alpha gateway is local-only. Do not bind it to a public interface or place it behind a public reverse proxy. It accepts one fixed upstream HTTPS origin, requires an inbound bearer token, blocks redirects, and does not log bodies or secrets by default.

Report vulnerabilities privately to the repository maintainer rather than posting credentials or exploit details in a public issue. Rotate any key that may have been exposed. The gateway does not store API keys or session content on disk.

The current strict converter rejects unsupported content and state instead of mutating it silently. This is a safety property, not a guarantee of full protocol compatibility.
