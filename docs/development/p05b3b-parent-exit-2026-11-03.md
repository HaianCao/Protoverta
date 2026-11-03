# P05b3b — Parent exit, background drain and Windows console interruption

Date: 2026-11-03. Status: COMPLETE for the bounded gates below, awaiting review
before commit. P05b3a was committed first as `5431271`. **P05 remains IN_PROGRESS**:
two independent launcher processes and temporary worktree behavior remain.

This batch changes developer probe infrastructure, tests and documentation.
No production gateway or consumer implementation changed. No live provider,
credentials, installed wrappers, real config/session/PATH, push, merge or release
was involved. `free-claude-code` remains untouched.

## Gate design

`scripts/smoke_parent_exit.py` runs the actual source wrappers with Codex CLI
0.166.0 or Claude Code 2.1.308. Each scenario starts one real child, obtains its
real spawn ID and waits for the child's actual upstream inference request
before reaching the parent exit/interrupt boundary. All state and fake provider
credentials remain under the existing isolated fixture environment.

`scripts/probe_console.py` starts a controller in a new hidden Windows console.
The source wrapper and descendants enter a nested kill-on-close Windows Job
before their suspended root is resumed. The outer probe job also owns the
controller. No console is attached to the user's terminal, no process is found
or killed by image name, and no job breakaway is permitted.

Acceptance is measured **before** the nested job's protective close:

- The wrapper exits within the bounded wait; fallback timeout cleanup is a failure.
- Its own job has exactly zero remaining processes, queried by native job membership.
- The upstream child connection has already reported EOF/reset to the fixture.
- The real CLI spawn/lifecycle evidence matches the observed child and scenario.
- Exit code is 0 for normal completion, 130 for the tested Ctrl+C path.

A later protective kill cannot make these assertions pass. A truncated or
unavailable membership snapshot fails closed; inaccessible process image names
are not mistaken for an empty job.

## Normal exit: different native harness contracts

**Codex:** after spawning an active child, the parent returns its verified final
answer without calling `close_agent` or waiting for child completion. The actual
CLI/launcher exits normally, the stalled child's upstream receives EOF/reset,
and the owned job is already empty. The fixture supplies no successful child
terminal and performs no read on behalf of that cancelled child. Its native
completed spawn event must correlate the task and child ID.

**Claude:** a final parent response does not necessarily end headless processing
while a background task remains active. An initial development probe kept the
child blocked; Claude remained alive until the safety timeout, so that probe
was rejected rather than counted as successful exit cleanup. This is a native
background-drain distinction, not evidence that the adapter leaked after exit.

The final normal Claude gate deliberately lets the child proceed **after the
first parent final response has been written**. It performs a real `Read`,
replays the fixture value, and produces its own verified result. Its final
upstream response is written, and the gateway's connection close is observed.
The parent then receives the native completion notification correlated to the
child ID and result, responds again, and exits. Claude must report one spawned
background task, one completion, zero failures and no permission denials.
Background output metadata must resolve under the owned temporary root; no
background transcript is read or tailed.

Thus the normal Claude result proves drain-to-completion and cleanup after
actual exit. It does not claim a still-running background task is detached,
cancelled by every parent final answer, or preserved after process exit.

## Actual Windows Ctrl+C event

For interruption, parent and child upstream requests are both waiting. The
fixture writes a local readiness marker only after actual child inference and
the next parent request have arrived. The controller then enumerates its own
console membership, requiring the controller and wrapper to be present and
every other console process to belong to its nested job. Any unknown member
or exited wrapper prevents signal generation.

Only then does it call native `GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)` in
that **newly created isolated console**. A controller-local handler keeps only
the controller alive; it does not install an inherited ignore-Ctrl+C flag in
the wrapper. Both clients' source wrappers return 130, close the upstream
child and leave zero owned descendants before job close. Claude's final native
result reports an aborted stream rather than a successful completed task.

Microsoft documents that [CTRL+C cannot be restricted to a nonzero process-group
ID](https://learn.microsoft.com/en-us/windows/console/generateconsolectrlevent),
which is why this fixture broadcasts only inside a separately created console.
The [process-creation flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags)
define new-console isolation, while [console control handlers](https://learn.microsoft.com/en-us/windows/console/setconsolectrlhandler)
are process-local unless the inheritable ignore attribute is explicitly set.

This exercises the real Windows console control-event mechanism. It is not a
physical keyboard automation test, UI-terminal-close test, forced root kill,
OS shutdown test or guarantee about every terminal/signal integration.

## Final actual CLI matrix

| Inbound client | Scripted upstream | Normal exit/drain | Isolated native Ctrl+C |
|---|---|---|---|
| Codex Responses | Responses, native control | PASS, child cancelled on exit | PASS, 130 |
| Codex Responses | Chat Completions | PASS, child cancelled on exit | PASS, 130 |
| Codex Responses | Messages | PASS, child cancelled on exit | PASS, 130 |
| Claude Messages | Messages, native control | PASS, background drained | PASS, 130 |
| Claude Messages | Chat Completions | PASS, background drained | PASS, 130 |
| Claude Messages | Responses | PASS, background drained | PASS, 130 |

All 12 final cases require native lifecycle evidence, upstream closure and
zero job members before protective cleanup. Test state and processes are owned;
the normal cases do not send console events. Real provider/model compatibility
and release-wheel installation are outside this gate.

## Validation and reproduction

36 new tests cover pre-cleanup evidence, remaining-process/timeout failures,
exact exit codes, unowned console members, spawn identity, Claude drain/abort
counters, and truncated Windows Job snapshots. Related harness/process/resource
regression: **235 passed in 28.96s**; scoped Ruff passed.
There is no new full runtime/SDK suite or soak claim for these fixture-only changes.

Run on Windows with the isolated preview Python, from the Protoverta repo:

```powershell
& $previewPython scripts/smoke_parent_exit.py --client codex --upstream chat --mode normal --adapter-source ../free-codex-cli --report $newReportPath
& $previewPython scripts/smoke_parent_exit.py --client claude --upstream responses --mode interrupt --adapter-source ../free-claude-cli --report $anotherNewReportPath
```

The report parent directory must exist. Do not replace the helper's hidden new
console with attachment to an existing terminal. Native clients keep their
P04 version/catalog gates and temporary config; the controller uses only the
synthetic bundle generated by this fixture. The helper waits up to 15 seconds
for the readiness boundary, then up to 15 seconds for exit, with bounded drain
and owned-job fallback. A fallback timeout remains an explicit failed gate.

Final evidence lives in the existing p00 preview under
`reports/p05b3b-{client}-{upstream}-{normal,interrupt}.json`. Reports contain
synthetic native output and pre-cleanup metrics; they contain no real provider
keys. Initial failed development probes are not passing acceptance evidence.

Next P05 gates: two independent launcher processes with distinct provider and
model choices, plus temporary worktree cwd/config/cleanup. The broader P05
checklist still needs a final evidence audit, including its parent-plus-two-child
SSE-interleaving and quota/lease wording; do not infer those claims from this
one-child lifecycle fixture. Stop for review before committing or continuing.
