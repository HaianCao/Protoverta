# Alpha 8: local preparation, not a published release

Candidate versions: Protoverta **0.3.0a8**, CodexApter and ClaudApter
**0.2.0a2** on their existing `integration/protoverta` branches. Consumer extras,
installer wheel URLs and CI expectations reference alpha 8. These URLs are a
release dependency, not evidence that the release exists. Until core is
published, install the three locally built wheels together as described in
[local-preview.md](local-preview.md); do not use the remote gateway installer.

Changes prepared in this continuation:

- Preview homes outside TEMP; clearer distinction between shell helper aliases
  and model-advertised tools. Existing user preview is not moved or deleted.
- Explicit Codex model-catalog override requiring an exact selected-model slug;
  no generated capabilities or GPT impersonation.
- Visible public-reasoning control and Messages route explanation in setup.
- Per-launch external stdio MCP configuration in both consumers; no global MCP
  registration. CLI fixtures verify actual tool result replay, not final text
  alone. This is not an embedded search service.
- Request context restored in generated Responses JSON and SSE snapshots,
  including original namespace tools and forced tool choice. Context stays
  local to the request; native Responses snapshots remain untouched.

## Compatibility boundaries retained

Full OpenResponses schema conformance remains an independent red gate, separate
from the supported text/function subset. Restoring request facts does not supply
missing provider facts: unknown sampling defaults, service tier and usage detail
must not be invented. A request's output-token cap does not prove the provider's
actual context window. Signed/encrypted thinking and opaque provider state cannot
be transplanted across providers. MCP does not turn provider-native WebSearch
into a portable function or relax sandbox/network permissions.

The schema gap therefore remains work, not an expected failure to relabel as
passing. Alpha 8 must continue to describe its supported subset and limitations.
Long-running real-provider behavior and an actual Internet search provider also
need separate operational acceptance; local fixtures prove neither.

## Publication order (requires a later user instruction)

1. Review and commit the final source changes in each repo; preserve both
   consumer `main` branches without integration. Record all commit IDs.
2. Build wheels/sdists; check packaged sources, metadata and absence of secrets.
   Run unit, SDK, fuzz, CLI/MCP, browser and bounded resource gates. Save failures
   from the full-schema audit alongside passing subset results.
3. Push/review Protoverta and run remote CI. Local Windows checks do not stand
   in for GitHub Actions or a second OS/Python matrix.
4. Publish core alpha 8 first; verify the immutable wheel and checksums at the
   exact URL in the consumers. Do not retag or overwrite alpha 7.
5. Only then push/review consumers' integration branches, run their remote
   released-wheel CI and preview install/uninstall tests. Publish consumer
   previews after those gates pass. Do not merge into consumer main implicitly.
6. Repeat the isolated user acceptance flow, including native direct mode,
   translated text/tools, changed-provider transcript resume and independent
   simultaneous sessions. Keep the stable installer as the rollback source.

No push, tag, release, main merge, real config/PATH change or user installation
update is part of preparing these files locally.

Local results and remaining red gates:
[alpha8-validation-2026-10-26.md](alpha8-validation-2026-10-26.md).
