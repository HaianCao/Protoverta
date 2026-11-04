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

## Latest result, 2026-11-04 (snapshot consistency follow-up)

The same unmodified pinned runner still returns **0 passed, 12 failed, 0 skipped**
and exit code **1**, but the `truncation` and `text` findings are eliminated.
Field-error occurrences across the twelve selected cases fall from **132 to
112**; this is not twenty passing test cases or full-schema acceptance.

JSON/SSE share truthful generated envelope facts. The HTTP gateway reports its
unstructured text mode from the actual converted outbound request and preserves
explicit structured formats. It also reports an output cap actually introduced
by its Codex profile. No upstream sampling defaults, serving tier, reasoning
subtotal, penalty or logprob setting was fabricated.

Remaining findings:

| Field | Cases with a finding |
|---|---:|
| `tools` | 10 |
| `tool_choice`, `parallel_tool_calls` | 12 each |
| `temperature`, `top_p` | 12 each |
| `service_tier` | 12 |
| `usage` (Messages lacks reasoning-token subtotal) | 6 |
| `frequency_penalty`, `presence_penalty`, `top_logprobs` | 12 each |

Unknown sampling now remains absent consistently in JSON and SSE. Earlier SSE
findings saying "Expected number, received null" therefore become "Required";
they are still failures, not repaired values. The standalone encoder has no
request provenance and no longer advertises an assumed text format; the gateway
can supply this fact because it holds the actual outbound request.

Evidence: [actual pinned-runner report](response-snapshot-audit-2026-11-04.json),
[changes, source/wheel identity and validation](response-snapshot-fixes-2026-11-04.md).
No official validator, fixture expectations or selected cases were patched.

## Previous result, 2026-11-04 (P13 Re-audit)

Re-running the unmodified pinned suite (`8907bc3b7c4300ff5c21fd647cfde9929243afa1`)
in `P13` confirms **0 passed, 12 failed, 0 skipped** (`summary.outcome=FAILED_FULL_SCHEMA`,
exit code `1`). Reports now also classify every missing or mismatched field by
provenance (`summary.provenance_classes`):

- `gateway-owned`: `truncation` (10) — plus already satisfied `id`, `object`,
  `created_at`, `completed_at`, `status`, `store=False`, `background=False`,
  `previous_response_id=None`, `max_tool_calls=None`, `safety_identifier=None`,
  `prompt_cache_key=None`.
- `request-declared`: `parallel_tool_calls` (12), `temperature` (12), `text` (10),
  `tool_choice` (12), `tools` (10), `top_p` (12) — restored by `response_context.py`
  when the inbound request explicitly declares them, and never fabricated when
  omitted by the compliance runner.
- `upstream-measured`: `service_tier` (12), `usage` (6 on Messages where
  `reasoning_tokens` is unavailable).
- `unavailable`: `frequency_penalty` (12), `presence_penalty` (12),
  `top_logprobs` (12) — outside the portable 3-protocol subset.

## Previous result, 2026-10-28

After the K1 request-policy correction and the K2 nullable metadata fixes, the
same pinned suite still reports **0 passed, 12 failed, 0 skipped**. The audit
process exits **1**, with `summary.outcome=FAILED_FULL_SCHEMA`. This is a partial
improvement, not full-schema acceptance. All six selected cases run on each of
the two synthetic upstreams; missing, duplicate, skipped or unknown case statuses
are rejected. Reports now group field errors so that progress cannot be confused
with a passing verdict.

Safe corrections made in K2:

- SSE includes nullable `max_tool_calls`, `safety_identifier` and
  `prompt_cache_key`, matching the generated JSON envelope.
- Explicit function-tool metadata includes `description`, `parameters` and
  `strict` as null when omitted. Explicit empty strings/objects and false remain
  unchanged; input requests are not mutated.
- The synthetic Chat SSE fixture now carries the same explicitly declared zero
  reasoning-token measurement as its JSON fixture. Messages cannot carry that
  subtotal, so its converted usage still leaves it unknown. This fixture change
  is not provider evidence.

These remove the two `tools.0.strict` findings and six nullable-envelope findings.
The fixture correction reduces usage findings from seven to six. Remaining
findings (counts across the twelve selected cases) are: tools/text/truncation
10 each; tool_choice/parallel_tool_calls/temperature/top_p/service_tier 12 each;
presence_penalty/frequency_penalty/top_logprobs 12 each; usage 6.

The suite omits several request metadata fields that K1 deliberately refuses to
guess. Sampling defaults and serving tier require evidence from the upstream;
Messages lacks the reasoning usage breakdown this schema requires. Penalty and
logprob controls are outside the current portable request subset. A real
full-schema solution therefore needs supported explicit controls and measured
provider facts, or a separately specified contract for unavailable data. It
cannot be achieved by silently substituting zero/default/empty values or
discarding known usage totals as null. Native Responses is the option when the
upstream supplies its own complete contract.

Evidence report: `%TEMP%/protoverta-k2-final-rgjvtnd6/audit.json`. The pinned
validator and test cases were not changed. SDK/semantic acceptance remains a
separate gate; it cannot override this full-schema failure.

## Historical result, 2026-10-25

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
