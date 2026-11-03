# P05a — Gateway request lifetime and isolation

Date: 2026-11-03. Status: P05a COMPLETE for its documented core contract;
UNCOMMITTED, stop for review. P05 overall remains IN_PROGRESS. P05 is split for review because its first checks exposed
runtime shutdown defects. P05a covers gateway requests; P05b must still verify
concurrent/nested/background/worktree behavior with actual agent harnesses.
Do not mark all of P05 complete from this report.

P04 was committed first: Protoverta `eb3e32c`, CodexApter `ee80019`,
ClaudApter `9f45d02`. Only Protoverta has new code in P05a. Consumer integration
branches and production main remain separate. No push, merge, release, real
provider calls or user installation/config/session/PATH changes.

## Confirmed issues and fixes

1. `gateway_session` closed its listener but did not cancel an inference still
   using an accepted client connection. A real-socket regression failed with
   “Session exit left its upstream inference alive”. The server now tracks
   accepted clients and request budgets, stops accepting work on close,
   cancels only its own budgets and shuts down/closes those client sockets.
2. Closing Windows sockets alone still left a silent, partially sent HTTP header
   blocked inside a makefile read. The regression additionally required empty
   handler/budget registries and detected the retained handler. Inbound reads
   now wait in a cancellation-aware readiness poll, without resetting the
   original I/O deadline or the request's total/progress deadlines.
3. A cancelled upstream stream could wake with an OS error and be reported as
   `invalid_stream`. The new isolation test reproduced that observation.
   Request-budget body reads now check cancellation/deadlines after an OS read
   error; a genuine uncancelled transport error is still propagated unchanged.
4. Gateway serve-thread startup failure could leave its already bound listener
   open. Startup failure/interrupt now closes the listener before propagating.

The server's drain wait is bounded to two seconds **total**, with separate
serve-loop shutdown/join overhead. Late budget registration is immediately
cancelled once closing starts. Failed handler-thread startup releases its
connection slot. Disconnect monitoring accepts a closed-descriptor race during
owned shutdown. No thread, other process or another session is forcibly killed.

## What the tests actually demonstrate

New tests use default HTTP transport and real loopback sockets with synthetic
upstreams. All files, credentials, quota databases and state are disposable.

| Check | Verified behavior |
|---|---|
| Normal session exit and Python KeyboardInterrupt unwinding | Open upstream cancelled; partial unauthenticated client closed; listener refused new connection; handlers, budgets and monitors drained |
| Late budget registration and thread-start failures | Cancellation is not missed; handler admission slot and bound listener are not retained |
| Inbound silent read | Cancellation observed promptly; polling does not restart the per-read I/O timeout or advance content progress |
| Three overlapping inference lanes, all six translated directions | Parent-like lane and two child-like lanes can reuse `same-call`; each result retains its own model/content/tool state |
| One lane returns 429, invalid EOF or timeout | Other two finish; failed generation is not secretly retried; shared concurrency leases release; failed attempts remain charged to rate quota |
| Cancel one lane while two others stay open | Only its default upstream closes; its lease becomes available; siblings finish successfully |
| Two gateway sessions sharing an admission database with different fake credentials | Closing A leaves B alive; credentials/tokens, observations and request IDs stay isolated |

The inference-lane tests are **not** actual parent/two-child harness tests.
The two-session test runs two core gateway sessions in one process, not two
launcher processes. Python exception unwinding is not a physical Windows Ctrl+C
delivery test. Those distinctions determine the remaining P05b work.

P04's actual-CLI/source-wrapper matrix was also rerun after the request-lifetime
changes: Codex Responses → Chat/Messages/Responses and Claude Messages →
Chat/Responses/Messages all passed, with verified single-child lifecycle. Codex
still uses five inference requests and Claude four. Their existing opt-in and
one-child limits were not widened.

## Validation

- New regression/isolation cases: **24** (10 lifetime and 14 isolation).
- Related regression before the final two inbound and two startup additions:
  **53 passed**, including HTTP boundary, deadlines, cancellation and resources.
- New lifetime file on final code: **10 passed**.
- Core full suite before the last startup-cleanup addition: **1789 passed**.
- Final core full suite: **1791 passed** on final runtime code.
- Official SDK gate: **26 passed**, 30.27 seconds. Existing negative strict
  schema and truncation gates retain their actual verdicts.
- Scoped mypy 2.4.0 baseline and positive/negative boundary fixtures: passed.
- Ruff and Git diff whitespace checks: passed on final code.
- Measured synthetic soak: **passed**, 120.207 seconds workload, 222 issued,
  201 upstream opens/closes, zero active upstream, zero retained heartbeat or
  lifetime monitors; 21 cancellations and 19 correctly classified invalid
  streams. Resource policy passed: retained threads -1, handles +2, TCP +0;
  warm RSS growth 942,080 bytes. This is short-run observation, not a long-term
  resource guarantee or a forced shutdown-under-load test.

Evidence is in the existing isolated preview's `reports/p05a-soak.json` and
`p05a-wrapper-{codex,claude}-{responses,chat,messages}.json`. These reports use
synthetic data, not private prompts or session transcripts. Pinned mypy was
installed only in that preview environment; global Python was untouched.

During test construction, an observer assertion initially assumed dictionaries;
the actual observer contract is `RequestObservation`. It was corrected to use
the existing typed fields. No production observer schema was changed.

From the Protoverta checkout, with an isolated matching preview Python:

```powershell
& $PreviewPython -m pytest tests/test_session_shutdown.py tests/test_request_isolation.py -p no:cacheprovider
& $PreviewPython -m pytest -p no:cacheprovider
& $PreviewPython scripts/check_sdk.py
& $PreviewPython scripts/check_types.py
& $PreviewPython scripts/soak_local.py --sustained --seconds 120 --requests 1000 --concurrency 2 --interval 1 --measure-resources
```

SDK, typing and measured soak checks require their pinned optional test
dependencies. These commands do not require provider credentials. The real CLI
matrix uses `scripts/smoke_subagents.py` with `--adapter-source` as documented
in the P04 report; it creates its own temporary launcher configuration.

## Limits and P05b

The bounded drain cannot preempt OS DNS, a blocking trusted injected transport
or arbitrary Python callbacks. Such code must honor its timeout/cancellation
contract and may outlive the drain wait. Default HTTP sockets are the supported
interruption path verified here. No detached-agent daemon or durable background
gateway was added.

Before declaring P05 complete, still verify:

1. Actual CLI parent plus two overlapping children, with matching harness
   lifecycle evidence; do not substitute the core inference-lane tests.
2. Actual harness child failure/cancellation, sibling isolation and nested-agent
   support/refusal for the exact installed versions.
3. Background work within the parent session, parent exit and actual Windows
   interrupt/process behavior in an isolated owned job.
4. Two launcher processes with independent gateways and credentials.
5. Worktree cwd and session configuration, without touching a real user worktree.

Installed-wheel and real-provider acceptance remain separate later gates.
No reasoning state/signature/usage was fabricated or discarded, no model
fallback was added, and the adapter still does not execute harness tools.

Stop for P05a review before committing this runtime change or beginning P05b.
