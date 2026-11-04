# P05 completion — Concurrency, cancellation and lifecycle

Date: 2026-11-03. **P05 COMPLETE within the versioned, isolated acceptance
scope documented here.** The user requested completion of the entire milestone
in one turn, superseding intermediate stop-for-review checkpoints. Final changes
remain uncommitted; no push, merge, release or real-provider call was performed.

This closes the remaining three-stream/worktree/accounting gates and audits the
previous P05 evidence. It does not declare P06–P14 complete or widen production
compatibility beyond the existing model/version/catalog gates.

## Checklist evidence

| P05 requirement | Evidence | Result |
|---|---|---|
| Parent + two child SSE streams interleave; colliding call IDs remain isolated | New real CLI/source-wrapper three-stream matrix: six routes/controls; six synchronized frame rounds per stream, one shared call ID, three distinct read values correctly replayed | PASS |
| One child 429/timeout/invalid stream does not poison sibling | P05b1 two-child 429 matrix (12 success/failure scenarios); P05b3a 18 timeout/truncated/invalid scenarios, requiring native lifecycle and fault-stage evidence | PASS |
| Cancelling one child leaves sibling/session usable; quota and leases reconcile safely | P05b2 actual stop/EOF/sibling matrix (six routes), P05a request-isolation integration, plus six new socket cancellation/accounting cases with live SQLite state | PASS |
| Two launchers use separate gateways/auth/models/diagnostics | P05b3c six paired scenarios/12 actual launcher sessions; cross-token 401, independent shutdown, one selected model/key per upstream, isolated journals | PASS |
| Nested agents follow tested harness support/boundary | P05b2 six Claude depth-1/depth-2 runs, three Codex existing-profile controls; no global unsupported claim for other Codex configurations | PASS within declared profile |
| Background tasks and parent exit/Ctrl+C follow native contract | P05b2 background completion/stop, P05b3b 12 normal-exit/drain/native Windows Ctrl+C scenarios; pre-protective-cleanup zero process count and upstream closure | PASS |
| Worktrees preserve cwd and session config | New six paired linked-worktree scenarios/12 real launcher sessions, plus three native Claude Agent worktree isolation/cleanup scenarios | PASS |

Previous reports: [P05a](p05a-request-lifetime-2026-11-03.md),
[P05b1](p05b1-parallel-agents-2026-11-03.md),
[P05b2](p05b2-cancel-background-nesting-2026-11-03.md),
[P05b3a](p05b3a-agent-faults-2026-11-03.md),
[P05b3b](p05b3b-parent-exit-2026-11-03.md),
[P05b3c](p05b3c-two-launchers-2026-11-03.md).
Their original review-checkpoint wording is historical; this report records
the final milestone status and additional evidence.

## Three actual overlapping SSE streams

`scripts/smoke_three_streams.py` starts two real children and a parent inference
through one source launcher/gateway. All three upstream HTTP responses emit
real tool-call SSE frames, with a three-party barrier before/after each frame.
The recorded order must contain contiguous synchronized frame rounds, each
containing exactly parent/A/B. Serial-lane traces, missing lanes and duplicate
lanes fail the verifier. Final evidence has six rounds per stream on every row.

The three histories deliberately reuse `same_three_lane_read`. Each read returns
its own lane's value; the next request must replay that exact value and none of
the other two values under that ID. Claude performs real `Read` calls against
three distinct fixture files. Codex uses a read-only local MCP fixture whose
advertised lane parameter selects a fixed synthetic value; three audited calls
are required. This MCP variation is fixture-only and leaves the default tool
schema/result unchanged when lane mode is unset.

Codex spawns A/B and performs the parent read while both children perform theirs,
then correlates real child wait states to final markers. Claude uses background
Agent calls, performs the parent read concurrently, then receives both native
completion notifications with matching child IDs/results. Real lifecycle counts,
successful parent exit and all three independently replayed reads are required.

| Inbound client | Upstream | Three synchronized streams |
|---|---|---|
| Codex Responses | Responses / Chat / Messages | PASS on all three |
| Claude Messages | Messages / Chat / Responses | PASS on all three |

Evidence: `reports/p05-final-three-{codex,claude}-{upstream}.json` in the existing
isolated p00 preview. The final stricter frame-order verifier was also applied
to all six recorded traces successfully. This is actual harness traffic, unlike
the earlier P05a synthetic three-HTTP-lane test.

## Linked worktree cwd/config isolation

`scripts/smoke_two_launchers.py --worktrees` creates one temporary Git repository
and two real linked worktrees. Main, alpha and beta have different fixture-file
contents. Two actual launchers run concurrently from alpha/beta with separate
selected providers/models/keys. The observer records each source wrapper's
actual cwd, and each CLI must perform/replay a relative fixture read from its
own worktree. All original auth/gateway/diagnostics isolation checks still apply.

Claude reads relative `fixture.txt` with its native Read tool. Codex uses an
actual local read-only MCP server that inherits the working directory and
refuses to read unless it is inside this owned linked worktree. An initial
PowerShell read probe was blocked by the existing read-only harness policy;
the fixture switched to MCP without changing sandbox or approval policy.

All six P05b3c client/endpoint pairs pass in worktree mode, each with exactly two
upstream inferences per launcher (read call + final result). Both cwd checks and
relative reads pass; the main checkout file remains unchanged. The temporary
repository, linked worktrees, Git metadata, generated configs/homes and workers
are removed after their owned jobs close. `temporary_state_removed=true` is
required. No real checkout/worktree registry or user Git configuration is used.

Evidence: `reports/p05-final-worktree-{0..5}.json`. These tests use distinct
temporary configs/homes, not simultaneous writes to a shared user config.

## Native Claude Agent worktree isolation

`scripts/smoke_agent_worktree.py` additionally configures the actual custom
Claude subagent with `isolation: worktree`, as described by the
[official subagent documentation](https://code.claude.com/docs/en/sub-agents).
Its parent runs in a temporary Git repo. The child must create exactly one
linked worktree under that owned root. The fixture briefly places a child-only
marker in its tracked fixture file; the child's real relative Read must return
that marker rather than the parent's file. The fixture restores its own probe
change before returning the verified child result, leaving the worktree clean.

The parent must receive that result, retain its original file, and observe the
native harness remove the clean child worktree and unregister it. Three routes
(native Messages, Chat, Responses) pass with one real completed child and no
permission denials. Only our synthetic file modification is restored; dirty
user work is never deleted or reset. Dirty-worktree preservation and every
possible native worktree configuration are outside this acceptance subset.

Evidence: `reports/p05-final-agent-worktree-{chat,messages,responses}.json`.
Codex's linked-checkout launch behavior is verified above; this report does not
invent a corresponding native Codex spawn-isolation option.

## Cancellation quota/lease audit

Six new real-socket integration cases cover both harness-facing protocols
(Responses/Messages) against all three upstream protocols. Three requests are
active. Cancelling A must close its upstream while B/parent stay active. Tests
query live SQLite state to require exactly two active concurrency leases and
an inactive A token reservation retaining its full unknown-usage amount:
10 exact operator-counted input + 32 reserved output = 42 tokens.

A further reservation of 59 fails against the 100-token per-model limit, proving
that ambiguous cancelled usage was not refunded or replaced by zero. A new
inference can use the freed concurrency slot, B/parent complete, and a fifth
request exceeds the four-request rate quota without upstream dispatch. Thus
cancellation frees concurrency but retains dispatched rate work and unknown
token charge. The counts are synthetic admission tests, not billing estimates.

This core/socket accounting evidence is separate from the actual CLI stop
matrix; it does not claim the native clients' internal recovery attempts have
been disabled or that provider invoices can be inferred from local quotas.

## Final validation

All commands used the disposable preview Python 3.13.9 on Windows. Native CLI
versions: Codex 0.166.0 and Claude Code 2.1.308. All providers/models/credentials
were scripted loopback fixtures; real `.env` contents were not read.

| Check | Result |
|---|---|
| Full Protoverta source tests | 1977 passed, 273.17s |
| Full CodexApter source tests | 354 passed + 12 subtests, 343.07s |
| Full ClaudApter source tests | 413 passed, 340.85s; five expected mocked-version warnings |
| Independent pinned SDK gate | 26 passed, 29.80s |
| Final fixture/verifier supplement | 99 passed, 20.19s; includes six cases added after full-suite collection |
| Ruff | All three repos passed |
| Scoped strict mypy | Six runtime boundary modules passed |
| Three-stream real CLI matrix | 6/6 |
| Two-launcher linked-worktree matrix | 6/6 pairs, 12 actual sessions |
| Native Claude Agent worktree matrix | 3/3 |

The full-core run includes the first 19 completion tests. Six additional strict
verifier cases were added afterward and run in the final supplement; do not
describe these as a single 1983-test full run. No runtime change was made during
this closing batch, so no additional soak was required. P05a's resource/lifetime
soak remains its documented evidence. No remote CI or wheel certification is
implied by these source-checkout runs.

Reproduce from Protoverta using the disposable preview Python:

```powershell
& $previewPython scripts/smoke_three_streams.py --client codex --upstream chat --adapter-source ../free-codex-cli
& $previewPython scripts/smoke_three_streams.py --client claude --upstream responses --adapter-source ../free-claude-cli
& $previewPython scripts/smoke_two_launchers.py --pair 4 --worktrees
& $previewPython scripts/smoke_agent_worktree.py --upstream chat --adapter-source ../free-claude-cli
& $previewPython -m pytest tests/test_p05_completion.py -p no:cacheprovider
```

## Preserved boundaries

- Existing production version/model/catalog/selected-provider gates and Codex's
  default subagent cap are unchanged; test-only overrides are explicit.
- Strict reasoning/signature/usage rules remain unchanged. No dropped reasoning,
  fabricated terminal/signature/usage, model fallback or retry policy widening.
- Claude background drain/partial-hand-back counter distinctions and native
  recovery retries remain documented; counters alone never establish success.
- Detached tasks surviving parent exit remain out of scope. Forced root kill,
  OS shutdown, arbitrary terminals/models and shared mutable user config writes
  were not certified by these tests.
- No real installation/config/session/PATH changes, no `free-claude-code` edits,
  no remote push/merge/release. P06 onward is separate work.
