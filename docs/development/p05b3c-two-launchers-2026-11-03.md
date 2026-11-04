# P05b3c — Two independent launcher processes

Date: 2026-11-03. Status: COMPLETE for this bounded isolation gate, awaiting
review before commit. P05b3b was committed first as `b95734e`. **P05 remains
IN_PROGRESS**; worktree behavior and the final checklist evidence audit remain.

Only developer fixtures, verifier tests and documentation were added. No
production gateway or consumer code changed, and no real provider, credentials,
installation, configuration, sessions or PATH were used or modified. No push,
merge or release. `free-claude-code` remains untouched.

## Test architecture

`scripts/smoke_two_launchers.py` launches two separate Python worker processes,
each running an actual source adapter and its installed native CLI. Workers
have separate temporary homes/configs and distinct synthetic provider IDs
(`alpha`, `beta`), model IDs, upstream keys, provider origins and answer markers.
Each launcher creates its own real local gateway and random inbound token.
The fixture uses Codex CLI **0.166.0** and Claude Code **2.1.308**, checking the
versions before starting inference.

Using separate workers matters: the fixture does not concurrently monkeypatch
Python process-creation functions in one shared interpreter. Every new worker
and native-client tree uses the existing bounded owned-process runner. No
unrelated processes are enumerated or killed.

`scripts/probe_gateway_context.py` observes the original gateway context only:
it records the synthetic gateway origin/token into an owned temporary file,
yields the original origin, and writes a closed marker after normal context
shutdown. It does not replace authentication, routing, requests or responses.
The private token file is deleted with the owned temporary state and is never
included in the report or diagnostics. Only the fixed synthetic credential
prefix is allowed by this observer.

## Required assertions

1. Both workers' first upstream inference requests must be pending together.
   A serial launcher run cannot reach this boundary before the deadline.
2. Each upstream verifies its exact endpoint, selected model and its own auth
   credential. Any wrong model/key/endpoint or extra dispatch fails the gate.
3. Gateway origins and inbound tokens differ. Swapping the two inbound tokens
   returns HTTP 401 in **both** directions, before upstream dispatch.
4. Release A and require its actual CLI result, normal worker completion,
   gateway context-close marker and a closed local listening port.
5. While B's upstream request is still blocked, its worker must remain alive.
   A correctly authenticated malformed JSON probe to B must still return local
   HTTP 422. It cannot dispatch upstream or be treated as authentication failure.
6. Release B; its own CLI must finish with B's marker and without A's marker.
   B's gateway must then close too. Each upstream receives exactly one inference.
7. Both providers deliberately share one **temporary diagnostics base directory**.
   They must produce separate provider subdirectories and session journals,
   each with its own upstream protocol and a successful request. Request-ID sets
   must be nonempty/disjoint. No test key, raw model ID or answer marker may
   appear in either journal.

The authentication probes are local rejected requests, not extra model calls.
The initial fixture expected HTTP 400 for malformed JSON, whereas the existing
gateway contract returns 422. The final assertion checks the actual 422
contract and still requires wrong-token 401 and exactly one upstream dispatch.
No gateway behavior was changed to satisfy the fixture.

## Final actual CLI matrix

Each row runs A and B simultaneously, completes A first, and then proves B
survives and completes independently. All assertions above passed.

| Pair | A client → upstream | B client → upstream | Result |
|---|---|---|---|
| 0 | Codex → Responses | Codex → Chat Completions | PASS |
| 1 | Codex → Messages | Codex → Responses | PASS |
| 2 | Claude → Messages | Claude → Chat Completions | PASS |
| 3 | Claude → Responses | Claude → Messages | PASS |
| 4 | Codex → Chat Completions | Claude → Responses | PASS |
| 5 | Claude → Chat Completions | Codex → Messages | PASS |

This is six paired scenarios, **12 real source-launcher/native-CLI sessions**.
It includes two Codex instances, two Claude instances and both mixed-client
directions. Every client exercises all three upstream protocols across the
matrix; native protocol rows are controls.

## Validation and reproduction

- 24 new tests cover fail-closed gate predicates, process-local environment
  isolation, actual local authentication-before-JSON parsing, journal protocol/
  request-ID/secret checks, and ownership checks in the context observer.
- Related harness/process/shutdown/isolation suite: **259 passed in 30.07s**.
- Scoped Ruff passed. All six paired scenarios passed on the final fixture.
- No new full runtime/SDK suite or soak was required for fixture-only additions;
  earlier full-suite baselines remain historical.

From Protoverta, using the isolated preview Python and the sibling source repos:

```powershell
& $previewPython scripts/smoke_two_launchers.py --pair 0 --report $newReportPath
& $previewPython scripts/smoke_two_launchers.py --pair 4 --codex-source ../free-codex-cli --claude-source ../free-claude-cli --report $anotherNewReportPath
```

Choose `--pair 0` through `--pair 5`. Report parent directories must already
exist. Final evidence is in the existing p00 preview's
`reports/p05b3c-pair-{0..5}.json`. Public reports contain only fixed observations
and version labels, never private local tokens, headers or credential bundles.
Each worker's native client is bounded to 30 seconds, inference overlap startup
has an 18-second deadline, each upstream release is bounded to 25 seconds, and
completion waits/process cleanup are scoped to owned jobs.

## Limits and remaining work

This proves separate-process session routing/auth/diagnostics and independent
normal gateway shutdown with text inference. It does not certify arbitrary
providers, model capabilities, forced-kill recovery, shared mutable user-config
writes, shared native CLI homes, or tool-side-effect replay. It is not a test
of subagent parallelism inside one launcher; earlier P05b1/b2 gates cover their
documented subsets. It does not establish three simultaneous parent/child SSE
streams or cancellation quota/lease reconciliation.

Next: temporary worktree cwd/config/cleanup, then audit all remaining P05
checklist wording/evidence before declaring the whole milestone complete.
Stop for review of this batch before commit or the next implementation step.
