# P05b2 — Child cancellation, background completion and nesting controls

Date: 2026-11-03. Status: COMPLETE for the bounded fixture gates below;
awaiting user review before commit. **P05 overall remains IN_PROGRESS.**
P05b1 was committed first as `9551291`. This batch changes developer fixtures,
their tests and documentation only; production gateway and launcher behavior
are unchanged. No provider credentials, real user configuration, installation,
sessions or PATH were modified. No push, merge or release was performed.

## Actual client cancellation gate

`scripts/smoke_agent_cancel.py` runs source wrappers with Codex CLI 0.166.0 or
Claude Code 2.1.308 against a scripted loopback provider. Both real child
inference requests must start before the parent receives a stop instruction.
The child A upstream socket must report EOF/reset **after** the requested stop.
Child B must still be waiting when A disconnects; only then is B released to
perform its read and replay the result. A receives no fabricated successful
terminal response. The parent must finish normally after B's verified result.

Codex uses actual `spawn_agent` IDs, closes only A through `close_agent`, and
waits for B. Completed collaboration events must correlate task → child ID →
close/wait result. Exactly one audited MCP read belongs to B.

Claude starts two actual background `Agent` tasks and stops only A through
`TaskStop`. Acceptance requires two spawned/background tasks, one parent-killed
task, one completed task, zero failures and no permission denials. B's native
completion notification must contain B's actual ID and verified result. The
tested binary did not advertise `TaskOutput`; this fixture does not invent or
call it. A read of the fixed fixture file supplies a tool boundary for native
notification delivery; the fixture never reads/tails background transcripts.

Notifications in the observed requests use a system message; Responses encodes
its text as `input_text`. The verifier handles native non-assistant notification
containers without rewriting them into assistant output. Initial development
failures here were verifier assumptions about role/content shape, not runtime
protocol fixes.

| Inbound client | Scripted upstream | Cancel A, B succeeds |
|---|---|---|
| Codex Responses | Responses, native control | PASS |
| Codex Responses | Chat Completions | PASS |
| Codex Responses | Messages | PASS |
| Claude Messages | Messages, native control | PASS |
| Claude Messages | Chat Completions | PASS |
| Claude Messages | Responses | PASS |

Each row requires physical upstream disconnect, the requested child identity,
sibling completion and real harness lifecycle evidence; textual success alone
cannot pass. Claude background work is proven while its parent remains alive,
not detached work surviving parent exit.

## Nested delegation and depth limits

`scripts/smoke_nested_agents.py` tests Claude with explicit temporary depth
limits. At depth 2 the parent spawns a child, the child spawns a leaf, the leaf
performs a real read, and both nested results return to the parent. Parent and
child deliberately reuse `same_spawn` in separate histories. Claude's counters
must report two completed spawns, maximum depth 2 and one spawn by a subagent.

At explicit depth 1, the child's advertised tool schema must omit `Agent`;
the child instead performs the local read and returns the fixture's explicit
depth-limit marker. This is a limit-control test, not a nested success or a
gateway fallback. Both depth settings pass on all three upstream protocols:
**six actual Claude runs**.

Three Codex controls run P04's existing single-child profile, verify its actual
lifecycle/read, and confirm that the child schema does not advertise nested
`spawn_agent`. This establishes that profile's boundary only. It does not prove
that every Codex configuration refuses nesting; no guessed depth setting was
added. The production opt-in cap remains one. Parallel fixture runs continue
to override the cap to two only inside the temporary test session.

The [Claude subagent documentation](https://code.claude.com/docs/en/sub-agents)
describes background task notification, stopping tasks and configurable spawn
depth. Our evidence is limited to explicit depths 1/2 and the tested binary;
we did not validate its default depth or every agent type. The
[Codex subagent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents)
provides configuration context; exact tool schema/lifecycle acceptance comes
from the installed CLI captures.

## Fixture isolation and regression checks

P04's runner now redirects `TEMP` and `TMP` into its owned temporary root before
starting the CLI. Claude background output metadata must resolve under that
root; otherwise the gate fails. Owned process-job cleanup precedes temporary
directory removal. Earlier development probes used the native temporary task
location; broad native directories were not removed to avoid touching unrelated
sessions. Subsequent final probes use the new owned location.

The shared parallel runner now exposes a scenario driver and its owned upstream
socket to developer callbacks. Expected peer-close during cancellation is
handled around both response headers and body writes. Fixture errors remain
recorded and fail acceptance; no runtime error policy or reasoning contract was
relaxed. Requests, waits and process lifetimes remain bounded.

Validation on Windows/Python 3.13.9:

- 49 new verifier/isolation tests; related regression suite: **163 passed in
  26.30s** on the final fixture snapshot.
- Six actual cancellation routes passed again after the final shared fixture
  socket-write adjustment.
- Nine nesting/profile controls passed (six Claude depth cases, three Codex
  existing-profile controls).
- All 12 P05b1 actual parallel scenarios passed again: six route/control rows
  times two-success/one-429 scenarios, with verified lifecycle and no fixture
  errors.
- Scoped Ruff checks passed. No production runtime changed. P05a's historical
  full core baseline was 1791 tests plus 26 SDK tests; that is **not** a new
  full-suite result including this batch's tests.

JSON evidence lives under the existing isolated preview's `reports` directory:
`p05b2-cancel-{client}-{upstream}.json`,
`p05b2-nested-claude-{upstream}-{1,2}.json`,
`p05b2-nested-codex-{upstream}-1.json`, and
`p05b2-regression-{client}-{upstream}-{ok,429}.json`.
These are real CLI/source-wrapper gates with a synthetic model/provider, not
certification of arbitrary models, native provider reasoning or release wheels.

## Reproduce in an isolated environment

Use the preview Python with all three source projects installed, real CLI
binaries on PATH and temporary CLI homes. From the Protoverta repo, for example:

```powershell
& $previewPython scripts/smoke_agent_cancel.py --client codex --upstream chat --adapter-source ../free-codex-cli --report $newReportPath
& $previewPython scripts/smoke_agent_cancel.py --client claude --upstream responses --adapter-source ../free-claude-cli --report $anotherNewReportPath
& $previewPython scripts/smoke_nested_agents.py --client claude --upstream chat --depth-limit 2 --adapter-source ../free-claude-cli
& $previewPython scripts/smoke_nested_agents.py --client claude --upstream chat --depth-limit 1 --adapter-source ../free-claude-cli
& $previewPython scripts/smoke_nested_agents.py --client codex --upstream messages --adapter-source ../free-codex-cli
```

Report parent directories must already exist. Use distinct report paths. See
the local-preview guide for environment creation; these commands do not install
or replace the user's normal wrappers.

## Remaining P05b3 gates

- Actual harness child timeout and truncated/invalid stream with sibling isolation.
- Parent exit/background cleanup and physical Windows interrupt in an owned job.
- Two independent launcher processes with distinct providers/models.
- Temporary worktree cwd/config behavior and cleanup.

Stop for review of P05b2 before committing or proceeding to these gates. Do not
mark all P05 checkboxes complete from the results above.
