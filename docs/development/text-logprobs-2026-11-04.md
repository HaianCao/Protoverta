# Lossless text-logprob follow-up

This uncommitted follow-up extends the preceding generation-control work.
Protoverta HEAD remains `1d2113b`; neither these changes nor the preceding
generation controls have been committed/pushed. Consumer source is unchanged.

## Implementation

`logprobs.py` validates the shared selected-token/alternative record, finite
scores, explicit integer bytes, requested top-k and concatenated UTF-8 text.
Records are copied, never inferred from display labels. The canonical Part and
Delta preserve the actual observations. JSON encoders and incremental SSE
encoders map them in both Chat/Responses directions. Responses done/content/item/
terminal records are type-validated and checked against accumulated deltas.
Transport fragments and token boundaries are independent, including Unicode
characters split across token byte arrays.

Bare APIs require `text_logprobs=True`; strict defaults still reject probability
payloads. Gateway mode is request-scoped and automatic: Chat true or Responses
probability include enables it. Messages enabled routes reject before I/O.
Selected-only requests send explicit top-k zero. Gateway output enforces the
applied top-k and rejects missing measurements for nonempty requested text.
No new launcher setting or setup UI choice is required. Native passthrough and
reasoning policy remain unchanged.

No probability value, bytes, tokenizer output, serving tier or reasoning usage
is invented. Null byte records, unsupported fields, malformed scores, wrong
record types and mismatched measurements fail. Errors use fixed descriptions
without copying token data. Stream success remains withheld until verified EOF.

See the [public contract and references](../compatibility/text-logprobs.md).

## Tests and final evidence

60 new core tests cover JSON round trips, fragmented SSE in both directions,
Unicode split-token bytes, nested alternative records, malformed/nonfinite/
overflowing scores, missing bytes, type/range validation, repeated-snapshot
tampering (including float/int equality), requested top-k enforcement, missing
measurements, request-copy isolation, gateway auto-selection, Messages rejection
and malformed Codex include values with zero provider I/O.

The pinned SDK gate retains its 29 mandatory cases and adds JSON/SSE probability
subscenarios to the existing positive Chat/Responses cases. Handwritten source
observations are parsed by strict official SDK models, including nested candidate
bytes/scores. SDK fixture usage explicitly matches its synthetic known values;
this is not a guessed real-provider subtotal or a change to the external schema.

Final source counts, SDK/property/type gates, six actual CLI tool loops and
matched wheel hashes are in [the receipt](text-logprobs-validation-2026-11-04.json).
Older/intermediate previews are not final acceptance evidence. All upstream I/O
was synthetic loopback; no real key, installed wrapper, config, session or PATH
was changed. Remote CI, a new soak and real-model support were not tested here.

## Full-schema boundary

The unchanged official baseline remains 0 passed / 12 failed / 0 skipped,
with 78 missing-field occurrences. It supplies no sampling profile, reported
tier or explicit penalty/probability control. Enabling lossless probability
mapping therefore does not reduce those baseline counts. Messages reasoning
usage still requires genuine source measurements. Full-schema compliance is
not claimed; see [the audit](openresponses-audit.md).
