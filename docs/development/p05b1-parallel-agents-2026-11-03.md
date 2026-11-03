# P05b1 — Two real foreground children and isolated upstream rejection

Date: 2026-11-03. Status: COMPLETE for this bounded harness fixture gate;
UNCOMMITTED, stop for review before committing or moving to P05b2. P05 overall
remains IN_PROGRESS. Cancellation, nested/background agents, physical Windows
interrupts, worktree and two-launcher-process ownership still need P05b2.

P05a was committed first as Protoverta `6af69aa`. This item adds only a developer
fixture, its tests and documentation. Protoverta runtime and both launcher
implementations are unchanged. No branch was pushed/merged, no release was
created, and no real provider, credentials, installation, config, sessions or
PATH was used or modified. `free-claude-code` remains untouched.

## Gate and implementation

`scripts/smoke_parallel_agents.py` reuses P04's isolated source-launcher runner:
temporary homes, fixed fake provider credentials, explicit synthetic Codex
catalog, read-only fixture file, scoped stdio MCP and bounded owned process job.
Its scenario and acceptance replace the single-child scenario; a P04 driver's
boolean `passed` is deliberately not parallel-agent evidence.

The actual installed clients are pinned by that runner: **Codex CLI 0.166.0**
and **Claude Code 2.1.308**. The model remains the exact synthetic catalog ID
`p04-fixture-model`. Each callback validates its upstream endpoint and model.
Responses namespaces and call IDs use the existing mappings, without relaxed
reasoning, schema validation, model fallback or core tool execution.

Two distinct child task markers identify A and B. Their first inference
requests must both reach a two-party barrier before either can receive its
scripted tool call. The fixture records a peak of two overlapping child
inferences; serial execution fails the barrier or the acceptance gate. Both
children intentionally use `same_child_read` in their independent histories.
Parent spawn IDs remain distinct. Each successful child must replay its own
read result before receiving its own final marker.

Codex performs two real spawns, then waits only for children not already
terminal. Spawn task → child ID → completed/failed wait state → final marker
must correlate. Two successful children require two audited MCP calls. Claude
uses a batch of two foreground `Agent` calls and built-in `Read`; its own
`subagent_stats` must confirm the exact spawned/completed/failed counts, with
no permission denials and a successful parent turn.

The Codex fixture explicitly overrides the current session's concurrency cap
to two through normal CLI arguments. **Production defaults and the P04 opt-in
cap of one are unchanged.** No global file or new configuration switch was
added. Other custom agent/model overrides remain outside this tested subset.

## Final actual CLI/source-wrapper matrix

Each row passed both scenarios, with `peak_overlapping_child_inference=2`,
`lifecycle_verified=true` and no fixture errors.

| Client | Inbound → upstream | Both children succeed | Child A gets 429, B succeeds |
|---|---|---|---|
| Codex | Responses → Responses, native control | PASS | PASS |
| Codex | Responses → Chat Completions | PASS | PASS |
| Codex | Responses → Messages | PASS | PASS |
| Claude | Messages → Messages, native control | PASS | PASS |
| Claude | Messages → Chat Completions | PASS | PASS |
| Claude | Messages → Responses | PASS | PASS |

Successful Codex runs used nine inference requests; rejection runs used eight.
Claude used six and five respectively. Wait counts can vary with scheduling;
the scenario still bounds total inference requests to 16. These are generated
model turns around harness operations, not provider-generation retries.

In the negative scenario the fixture emits **one** explicit HTTP 429 for A,
with `x-should-retry: false`. A makes exactly one upstream request and performs
no read. B independently completes its read and final response. The parent
receives the failed A result and successful B result, then ends successfully.
Codex must identify the errored child as the child spawned for A and the
successful marker as belonging to B. Claude reports two spawned, one completed
and one failed. Codex audits one MCP read, rather than two.

Codex 0.166.0 replaces the provider's error-body text with its own status message
(`exceeded retry limit, last status: 429 Too Many Requests`). That wording alone
does not prove retries occurred: observed upstream A-request count is one.
The fixture accepts the observed 429 status form plus exact failed-child
identity, rather than requiring a provider-specific error string to survive.

## Tests and evidence

- **26 new tests** cover correlated success/failure lifecycle, swapped/duplicate
  child identity, malformed/text-only claims, boolean counters, permission
  denials, three-protocol batched tool framing, per-item Responses namespaces,
  inherited-driver false positives and explicit non-retryable fixture errors.
- **114 related tests passed** in 22.97 seconds: new parallel harness cases,
  prior subagent/harness/search/resume fixtures, request isolation and shutdown.
- **12 actual CLI/source-wrapper scenarios passed** on final code.
- Ruff and whitespace checks passed. This item does not change runtime code;
  the committed P05a full-core baseline remains **1791 passed**, with its SDK,
  scoped typing and measured resource evidence. That historical full run is not
  presented as a newly rerun full suite including these 26 tests.

Reports in the existing isolated preview: `reports/p05b1-{codex,claude}-
{responses,chat,messages}-{ok,429}.json`. They contain synthetic CLI data only.
Earlier `p05b-*` development reports may include failed fixtures; prefer the
final `p05b1-*` reports.

During development, the first Codex rejection fixture incorrectly required the
original provider error text. Its bounded scenario failed instead of treating
the sibling's completion as full success; the condition was corrected to use
the actual harness 429 state. Three initial codec tests also incorrectly
treated `StreamDecoder.finish()` as returning deltas; it validates EOF and
returns `None`. Only the corrected final results above count as passes.

## Reproduce

From Protoverta with an isolated matching source environment and the documented
CLI versions:

```powershell
& $PreviewPython scripts/smoke_parallel_agents.py --client codex --upstream chat --adapter-source ../free-codex-cli --report NEW_CODEX_OK.json
& $PreviewPython scripts/smoke_parallel_agents.py --client claude --upstream responses --adapter-source ../free-claude-cli --failure-429 --report NEW_CLAUDE_429.json
& $PreviewPython -m pytest tests/test_parallel_agent_harness.py -p no:cacheprovider
```

Repeat both scenarios for upstream `chat`, `messages`, `responses` and both
clients. The script configures its own temporary state and scoped overrides.
Do not substitute real provider endpoints, keys or user session transcripts.
Unexpected fixture errors return explicit non-retryable HTTP 422 with shape-only
diagnostics. CLI execution is bounded by the inherited 45-second owned-job
runner, and only that probe's process tree is cleaned on timeout.

## Remaining P05 gates

This proves two **foreground child inferences overlap**. It does not claim that
all three parent/child SSE streams were byte-interleaved: a foreground parent
can be waiting in a harness tool instead of making another inference. P05a
separately tests three interleaved HTTP lanes; background harness behavior still
needs its own acceptance. These two kinds of evidence are not interchangeable.

P05b2 must cover real child cancellation/timeout/truncated-stream behavior,
nested support or refusal at the exact installed versions, background work and
parent exit, actual Windows interrupt delivery within an isolated owned job,
two launcher processes, and temporary worktree cwd/config propagation. No
detached daemon or post-parent ownership guarantee is introduced here.

All provider responses in this report are scripted. This gate verifies harness
and adapter interoperability; it does not certify a real provider/model or its
reasoning semantics. P05 remains open until the remaining gates are resolved.
