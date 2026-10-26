# Risk-based coverage expansion, 2026-10-26

This follows the [earlier validation](validation-2026-10-26.md). Work proceeded
in the requested order: stream integrity, cancellation/cleanup, setup/CLI/doctor
handoff, then Python CLI child coverage. No provider calls, real installation,
PATH changes or session migration were performed. Validation was local; no
publication was performed as part of this checkpoint.

## Stream integrity

Added 48 adversarial wire cases based on independent hand-authored fixtures.
Across translated routes, changed text snapshots, item/call identity, argument
snapshots, model/final output, malformed deltas, unsupported finish reasons and
open content must fail without emitting successful target completion.

Two cases exposed a real native SSE lifecycle defect: Messages and Responses
accepted reuse of a closed block/output index. The native validator now retains
seen indices and rejects duplicate starts even after closure. It still preserves
valid native feature payloads without interpreting hidden/vendor-owned content.
Sparse distinct indices are not renumbered by this validation.

## Cancellation and cleanup

Four new socket-watch tests cover exceptional sockets, select errors, peer EOF
and pipelined HTTP bytes. Cancellation occurs once, watch threads terminate, and
normal shutdown does not consume or discard bytes belonging to the next request.
The readable-socket test synchronizes with the actual watch thread rather than
assuming it has run after a sleep. Existing transport deadline, process-tree
containment and runtime stress tests were also rerun in the full suites.

## Setup, foreground launch and doctor

Both launchers add CLI contract tests for literal argument forwarding, selected
provider/model, nonzero child return codes, defaults, unknown providers, typed
launch errors, interrupts, doctor opt-in and report-file preservation: eight
new CodexApter tests and nine ClaudApter tests.

`browser_setup_smoke.py --launch-fixture` tests the combined browser Run path
through the real foreground launcher and gateway. Only executable discovery and
upstream transport use controlled fixtures. A Python CLI child checks the model,
verifies it did not inherit the upstream key, and completes a gateway HTTP round
trip. The setup listener must already be closed while the child remains alive.
Releasing the child must propagate exit code 7. Both consumers pass this check.
This is not a real interactive agent/PTY test; actual CLI smoke and manual
interactive acceptance remain distinct.

## Child coverage and result

`check_coverage.py` is an optional development tool, pinned by `coverage-test`.
It runs the source suites with branch collection and verifies nonempty CLI-only
child coverage using keyless `--help` canaries. The pytest-only guard passes
coverage configuration through scrubbed environments; it does not restore
credentials, mutate those environment mappings or change the OS allowlist. Two
tests cover that boundary. No interpreter startup files are modified.

Instrumentation is limited to `python -m protoverta`, `codex_adapter.cli` and
`claudapter.cli`. Native executables, unrelated Python timeout fixtures and
arbitrary grandchildren are not measured. Coverage contexts prove CLI child
execution: a Windows venv launcher's Popen PID can differ from Python's measured
PID. The checker fails on a missing/wrong-checkout child result or failing suite.

An initial broad instrumentation attempt slowed a process-containment fixture
past its 0.5-second startup assumption. That attempt failed; its result was not
accepted and the timeout test was not weakened. Restricting instrumentation to
project CLI modules resolved the measurement interference. Initial PID-based
shard matching also failed on Windows; context-based verification replaced it.

| Package | Previous lines / branches | Current lines / branches |
| --- | --- | --- |
| Protoverta | 90.6% / 82.4% | 91.0% / 83.3% |
| CodexApter | 81.2% / 70.9% | 82.4% / 72.6% |
| ClaudApter | 78.3% / 68.0% | 83.5% / 72.0% |

These separate statement/branch percentages are not coverage.py's combined
percentage. The increase mixes new tests and previously unmeasured CLI child
execution; it is not a pure comparison of additional behavioral coverage alone.
Browser and native-agent execution are not folded into these percentages.

All final source suites pass under the coordinated coverage checker: core 1378,
CodexApter 189 plus 12 subtests, ClaudApter 157 plus 9 subtests. Installed-wheel
suites pass with the same counts. Added cases total 71. The pinned installed SDK
gate remains 19 passed (including its explicit negative usage-schema cases),
and property gate 24 passed. Ruff passes all three repos; scoped mypy passes.
All three runtime wheel payloads match source and six wheel/sdist artifacts
pass Twine. Only a disposable test environment was reinstalled.

Coverage remains incomplete. The full OpenResponses required snapshot/schema
and unknown usage-detail limitations from the earlier report remain unresolved;
these tests do not add reasoning-state migration or universal provider/model
support. No remote CI, new live-provider evidence or long-duration soak is claimed.
Follow the [preview guide](local-preview.md) for reproduction and manual checks.
