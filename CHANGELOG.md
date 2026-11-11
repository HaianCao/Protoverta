# Changelog

All notable changes to Protoverta are listed here. Each version is published
as a GitHub release with a wheel, an sdist and `SHA256SUMS`. Versions before
0.3.0 were previews (`0.3.0aN`). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `CatalogPolicy(invalid_entries="skip")`: one invalid entry no longer fails
  the whole model catalog. Entries without a valid ID are left out and
  malformed optional metadata (name, token limits, modalities) is dropped,
  never guessed. The default stays `"reject"`.
- `fetch_catalog` returns a `Catalog`: still a tuple of `ModelInfo`, with
  `skipped` and `dropped_fields` counts.

### Changed
- The HTTPS transport keeps the TLS context it was given instead of reading
  CPython's private `HTTPSHandler._context`.
- Response headers and chunked framing are read a buffered run at a time
  instead of byte by byte; every socket read still runs under the request
  deadline.

## [0.3.0] - 2026-11-10

First stable release.

### Added
- Error-class retry policy (`GatewayConfig.retry_policy`, `--retry-policy`):
  failed provider attempts are classified (rate limit, overloaded, server,
  network, stream interrupted, backend mismatch, auth, billing, model not
  found, context length, content policy, invalid request, unknown) and
  transient classes are resent after a configurable wait. Streams are only
  retried before any output. `protoverta.error_classes` exposes the
  classifier for the launchers' direct-mode relay.
- README badges, launcher links, package metadata (authors, keywords,
  classifiers, project URLs), contributor guide and issue templates.
- Documentation site (Sphinx, published on GitHub Pages): guides, the
  gateway HTTP API, command reference and Python API reference.
- Release assets include `SHA256SUMS` and build provenance attestations;
  PyPI publishing through Trusted Publishing.
- `protoverta` console command (also `python -m protoverta`), `--version`,
  `protoverta.__version__` and a `py.typed` marker (PEP 561).

### Changed
- `/v1/models` accepts requests that carry both `Authorization` and
  `x-api-key` when every credential matches (Claude Code 2.1.248+ sends both).
- The source distribution no longer includes internal development reports.

### Fixed
- An unexpected gateway fault is answered with HTTP 500 `internal_error`
  and `x-should-retry: false` instead of a dropped connection, and no
  traceback is printed into the client's terminal.

## [0.3.0a11] - 2026-11-07

### Fixed
- Responses streams whose text events omit `logprobs` (for example
  TokenHarbor) are accepted when logprobs were not requested. They were
  rejected with "unsupported Responses content part metadata" (HTTP 502).
- A connection that arrives while the gateway is at its connection limit waits
  up to 0.5 s for a slot before receiving 503, so an immediate reconnect after a
  closed connection is served.

### Changed
- CI also tests Python 3.14. GitHub Actions moved to Node 24 releases.
- Provider compatibility notes cover TokenHarbor's Responses and Messages
  endpoints, AgentRouter in direct mode, and a live re-check on
  Claude Code 2.1.319 and Codex CLI 0.167.0.

## [0.3.0a10] - 2026-11-07

### Fixed
- A request that arrives right after a response finished could get a spurious
  503 "capacity reached" because the slot was released late. Requests now wait
  up to 0.5 s for a slot.
- Shared admission storage uses SQLite WAL and one kept connection per gateway
  (about 0.2 ms per admission instead of about 11 ms), closed with the server.
  Busy storage is waited on for up to 5 s.

`v0.3.0a9` was tagged but never released (its validation exposed the issues
fixed in 0.3.0a10); the tag was removed.

## [0.3.0a8] - 2026-11-06

### Added
- `--reasoning-output omit` (`reasoning_output` policy): reasoning a translated
  route cannot represent is dropped instead of failing the request. Nothing is
  fabricated or forwarded.
- Provider errors reported inside an HTTP 200 stream reach the client with the
  provider's status and message.
- `docs/compatibility/providers.md`: tested providers, their limits and
  recommended settings.

### Fixed
- Chat responses with `function_call: null` or `audio: null` are accepted.
- Over-capacity connections receive 503 with `Retry-After` instead of a reset;
  request headers must arrive within 15 s.

### Changed
- Default timeouts: 300 s socket and progress, 3600 s total, matching the
  Codex and Claude Code idle limits.
- The strict type gate covers seventeen runtime modules.

## [0.3.0a6] - 2026-10-23

Earlier previews (0.3.0a5 and 0.3.0a6) are described in their
[GitHub releases](https://github.com/HaianCao/Protoverta/releases).

[Unreleased]: https://github.com/HaianCao/Protoverta/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/HaianCao/Protoverta/compare/v0.3.0a11...v0.3.0
[0.3.0a11]: https://github.com/HaianCao/Protoverta/releases/tag/v0.3.0a11
[0.3.0a10]: https://github.com/HaianCao/Protoverta/releases/tag/v0.3.0a10
[0.3.0a8]: https://github.com/HaianCao/Protoverta/releases/tag/v0.3.0a8
[0.3.0a6]: https://github.com/HaianCao/Protoverta/releases/tag/v0.3.0a6
