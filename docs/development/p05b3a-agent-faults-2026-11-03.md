# P05b3a — Real child timeout and broken-stream isolation

Date: 2026-11-03. Status: COMPLETE for this fixture gate, awaiting review before
commit. P05b2 was committed first as `5d26026`. **P05 remains IN_PROGRESS**;
parent exit/Windows interrupt, two independent launchers and worktrees are not
covered by this batch.

Only developer fixtures, verifier tests and documentation changed. No gateway
runtime or consumer source changed. No push, merge, release, provider call,
credentials, installed wrapper, real configuration/session or PATH changes.
`free-claude-code` remains untouched.

## Faults and acceptance

`scripts/smoke_agent_faults.py` reuses P05b1's two-real-child runner. Source
wrappers start Codex CLI **0.166.0** or Claude Code **2.1.308** with isolated
homes, owned process jobs and a scripted authenticated loopback provider.
Both child inference requests must reach the barrier. Child B performs its
normal read/replay and returns its own unique verified marker. Child A gets:

- `timeout`: a real SSE start and partial text, then a stalled upstream body.
  The temporary provider config uses a 2-second progress deadline (I/O 15s,
  total 30s). Acceptance requires a transport-read timeout diagnostic and
  upstream EOF/reset after the gateway closes its timed-out request.
- `truncated`: a real SSE start and partial text followed by EOF with no success
  terminal. Acceptance requires the decoder/pipeline to reject stream finish.
- `invalid`: start/partial text followed by malformed SSE JSON. Acceptance
  requires failure during decoder/pipeline feed, not merely a later EOF.

The partial prefix is deliberately `P05_PARTIAL_NOT_SUCCESS`; it never contains
the verified A result or a fabricated successful terminal. The real gateway
detects and emits its normal failure. Native trace instrumentation observes
and rethrows errors; it does not substitute responses or error categories.

Acceptance requires the injected failure stage, actual child lifecycle and
normal parent completion. Codex spawn task → child ID → errored/completed wait
states must match, with exactly one MCP read belonging to B. Claude's own A
tool result must report failure/incompleteness, and the B tool result must carry
B's verified marker. Aggregate counters or model claims alone cannot pass.

## Final actual client matrix

| Client inbound | Scripted upstream | Timeout | Truncated | Invalid |
|---|---|---|---|---|
| Codex Responses | Responses, native control | PASS | PASS | PASS |
| Codex Responses | Chat Completions | PASS | PASS | PASS |
| Codex Responses | Messages | PASS | PASS | PASS |
| Claude Messages | Messages, native control | PASS with incomplete hand-back | PASS with incomplete hand-back | PASS |
| Claude Messages | Chat Completions | PASS | PASS | PASS |
| Claude Messages | Responses | PASS | PASS | PASS |

All 18 cases have the expected diagnostic stage, no fixture errors, a verified
B read/result and a normal parent exit. This is source-wrapper/real-harness
evidence against a synthetic model/provider, not arbitrary-provider or release
wheel certification. It does not prove parent and both children continuously
emit interleaved SSE bytes.

## Important native Claude distinction

For native Messages timeout/truncation, this binary returns A's partial output
with its own explicit early-API-error and unfinished-task warnings. It counts
that hand-back as `completed`, reporting two completed and zero failed children.
The fixture therefore requires the warnings in **A's own result**, the partial
marker, absence of A's verified success marker, B's separately verified result,
and the transport/stream diagnostic. It does not count A's task as successful.
The parent is allowed to finish reporting the failed/incomplete A and successful
B. This distinction is a harness behavior, not a gateway-generated completion.

On translated Claude routes, the observed binary may retry a failed streaming
request as a non-streaming request. The fixture returns the corresponding broken
JSON body or stall on that request too; it never changes to a working model or
pretends the original stream succeeded. These runs observed one or two A
requests. Native Messages timeout/truncation observed four streaming attempts
before the incomplete hand-back. Codex fault cases observed one A request.

Claude's [documented retry setting](https://code.claude.com/docs/en/env-vars)
`CLAUDE_CODE_MAX_RETRIES` is set to `0` **only in this fixture's child process**.
It reduces ordinary retry waits; the native recovery attempts observed above
still occur. Neither that setting nor Codex's test-only retry overrides prove
all internal recovery paths are disabled. This experiment has no A-side tool
execution: it cannot establish that arbitrary tool turns are safe to replay.
No retry policy for normal user sessions was changed.

## Implementation and bounds

The shared runner accepts scenario-owned streamed fault replies and optional
shape-only tracing. Normal parallel/429/cancellation scenarios keep their
default settings. A custom failure predicate is used only by the fault gate;
P05b1's default expected-429 predicate is unchanged.

P04's runner accepts validated positive finite timeout overrides and writes
them only to its single owned temporary provider. The direct fixture gateway
receives the same values. Model/endpoint checks also apply to retry requests.
The faulting child is limited to six upstream attempts; ordinary scenario
requests retain the existing budget, and the owned client process retains its
45-second outer limit with process-tree cleanup. A budget failure fails the
gate; it is never accepted as the injected provider fault.

During development, the initial one-request-only assertion rejected Claude's
native recovery behavior. Later checks exposed the native incomplete-hand-back
counter distinction. Those failed probes were not gateway fixes and are not
reported as passing runs. Final reports require both diagnostic and correlated
harness evidence; no failure was reclassified solely to satisfy a counter.

## Validation and reproduction

- 32 new fixture/verifier tests; related harness/shutdown/isolation suite:
  **195 passed in 26.08s**.
- All 18 actual fault scenarios above passed. Six additional Chat-route real
  harness regressions passed: parallel success and one-429 for both clients,
  plus child cancellation for both clients.
- Scoped Ruff passed. No new full runtime/SDK suite or soak was needed for these
  fixture-only changes; earlier full-suite baselines remain historical.

Using the existing isolated preview Python from the Protoverta directory:

```powershell
& $previewPython scripts/smoke_agent_faults.py --client codex --upstream chat --fault timeout --adapter-source ../free-codex-cli --report $newReportPath
& $previewPython scripts/smoke_agent_faults.py --client claude --upstream responses --fault truncated --adapter-source ../free-claude-cli --report $anotherNewReportPath
& $previewPython scripts/smoke_agent_faults.py --client claude --upstream messages --fault invalid --adapter-source ../free-claude-cli
```

Report directories must exist. Matrix evidence is under the existing p00
preview's `reports/p05b3-{client}-{upstream}-{fault}.json`; final regression
reports are `p05b3-regression-*.json`. Reports include native lifecycle output
and synthetic diagnostics; do not attach raw real-provider/session payloads to
future reports. No live quota or user credentials are used here.

Remaining P05b3 work: actual parent exit/background cleanup, physical Windows
interrupt in an owned job, two independent launcher processes with distinct
provider/model selections, and temporary worktree cwd/config/cleanup. Stop for
review of this batch before committing or starting those gates.
