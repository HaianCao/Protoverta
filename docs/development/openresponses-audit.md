# Official OpenResponses schema audit

Protoverta does **not** currently claim full OpenResponses schema compliance.
Protocol-subset interoperability through the official SDK parsers is separate.

The official runner and its Zod validators were pinned to
`openresponses/openresponses@8907bc3b7c4300ff5c21fd647cfde9929243afa1`.
`check_openresponses.py` checks the checkout revision and tracked cleanliness,
records the bundled runner hash, and runs six unmodified official test cases
through the local gateway for Chat and Messages upstreams. Upstream transport is
a synthetic fixture; no provider, image URL, tool or coding CLI is contacted.
The Messages target needs an explicit output cap, so these routes use the Codex
client profile with an explicit 128-token default. No other protocol semantics
are relaxed. The runner receives a synthetic local key only.

## Reproduce outside the repository

1. Clone the official repository into a disposable directory and check out the
   exact revision above. Review the suite before running it.
2. In a separate disposable tools directory install **only**
   `esbuild@0.25.12 zod@3.25.76` using npm `--ignore-scripts --no-audit --no-fund`.
   The tested Node runtime was 24.13.1. No Astro install/build is required.
3. Bundle without patching official source:

   ```text
   node scripts/build_openresponses_runner.cjs SUITE TOOLS_DIRECTORY OUTPUT.mjs
   python scripts/check_openresponses.py --suite SUITE --runner OUTPUT.mjs --report REPORT.json
   ```

The report includes the exact selected IDs: basic-response, streaming-response,
system-prompt, tool-calling, image-input and multi-turn. Skipped/missing selected
cases fail. The runner is trusted test tooling: a source revision check does not
prove that an arbitrary operator-supplied bundle was built from that source.
Keep its hash with the test record. No foreign source/validator is vendored.

## Observed result, 2026-10-25

**12/12 selected checks fail the full required response schema**, on synthetic
Chat and Messages routes. JSON output is missing required snapshot fields such as
completed_at, instructions, truncation, text, sampling settings, reasoning,
max_tool_calls, store/background, service_tier and cache/identifier metadata.
SSE additionally reports unknown sampling values as null while this pinned
schema requires numbers. The runner exits nonzero; this is not an accepted pass,
an xfail or positive capability evidence. Existing SDK/semantic gates passing do
not cancel these findings.

Some fields can be completed from gateway-owned lifecycle or explicit request
data in future work. Others describe facts that these upstream responses do not
report (effective sampling defaults, serving tier, provider-side storage). They
cannot be filled with guessed numbers, fabricated provider facts or silent policy
changes just to satisfy a validator. Resolving the entire gap requires a bounded
snapshot/provenance design and, where necessary, explicit evidence-backed provider
profiles. Native Responses remains subject to its own upstream schema.

WebSocket state/reconnect, hosted operations, phase semantics and opaque compaction
are not certified by this selected suite. Exclusion is not a pass and does not
remove the existing explicit unsupported behavior. This audit reports schema
findings before some downstream semantic validators can run; separate semantic
and SDK gates remain necessary.

Sources: [official runner and schema](https://github.com/openresponses/openresponses/tree/8907bc3b7c4300ff5c21fd647cfde9929243afa1),
[specification](https://www.openresponses.org/specification).
