# Reported service-tier preservation

The effective-tool-policy and sampling work was committed as `c368f9a` without
push. This follow-up preserves actual upstream tier observations. Its runtime
changes remain uncommitted until reviewed.

## Fix

Completed Chat/Responses JSON previously lost `service_tier` even when upstream
reported it. The canonical Result now carries the bounded common tier value and
generated Chat/Responses output retains it. A missing/null observation stays
unknown; request context cannot substitute a requested tier for a reported one.

Canonical finish deltas now carry reported tier metadata. Chat decoding accepts
early or late observations, requiring nonnull observations to agree. Responses
decoding reads the terminal snapshot, leaving the created snapshot's provisional
mode out of the final observation. Generated SSE publishes the tier only at
completion, plus the Chat usage postlude. Invalid types, unknown values and
conflicting Chat observations fail rather than generating successful completion.

This is observational output metadata, not a routing or billing control.
Messages does not advertise this OpenAI-specific tier. Native passthrough is
unchanged, and translated service-tier request selection is still unsupported.
See [contract and primary references](../compatibility/service-tier.md).

## Verification

61 new synthetic tests cover the six common values, both JSON directions,
fragmented SSE, early/late Chat metadata, provisional Responses metadata,
missing/null observations, request-context isolation, malformed/unknown tiers,
conflicting observations, HTTP error redaction and Messages output boundaries.
The matched-wheel and full-core results are recorded in
[validation receipt](reported-tier-validation-2026-11-04.json).

No provider requests, real install/config/session changes, release or push were
performed. The preceding launcher source-suite results remain applicable to
their unchanged source; actual CLI loops separately exercise the new core wheel.

## Remaining fields

| Group | Current behavior | Further work or source requirement |
|---|---|---|
| Sampling | Explicit values preserved; scoped Chat defaults are applied and sent | Real provider/model evidence before configuring defaults; Messages/Responses profile defaults need separate contracts |
| Serving tier | Reported common Chat/Responses values preserved; no invented default | Upstream must actually supply it; new protocol-specific values need explicit target mapping |
| Messages reasoning usage | Missing subtotal stays unknown | A genuine source measurement; total output tokens cannot establish the reasoning subtotal |
| Penalties/logprobs | Unsupported translated controls remain rejected | Pair-specific request/output semantics, streaming mapping and negative-route coverage before enabling them |

The official baseline still has **78 field findings, 0 passed / 12 failed /
0 skipped**. Its fixtures supply no serving tier or sampling profile. No external
validator or fixture was changed to hide missing facts. Informational metadata
support is not full OpenResponses certification.
