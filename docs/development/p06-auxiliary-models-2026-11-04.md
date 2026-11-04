# P06 — Auxiliary model on the same provider

Date: 2026-11-04. **P06 COMPLETE within the versioned fixture scope below.**
P05 was committed as `6da3f0c`. P06 changes remain uncommitted for review;
no push, merge, release or real-provider call was performed.

## Contract and evidence

The parent uses model A and the configured child default uses model B on the
same provider. This is not cross-provider routing or arbitrary agent-file
certification. Existing strict reasoning, evidence policy and production
concurrency limits remain unchanged.

| Requirement | Evidence | Result |
|---|---|---|
| Independent model capabilities and route evidence | Per-model A/B registry tests; strict verification accepts each model's own evidence and never borrows A's certificate for B | PASS |
| Known unsupported tools/effort rejected before upstream dispatch | Three upstream protocols, four negative capability/context/output cases each; parent remains usable | PASS |
| Unknown is distinct from unsupported | Missing B capability/evidence follows the existing require-verified policy; permissive unknown stays unknown, strict unknown is rejected | PASS |
| Safe requested/effective model tracing, no fallback | Gateway-session HMAC identities and end-to-end A/B/A observations; actual CLI fixtures reject any model other than the role's exact model | PASS |
| Budget/context checks use the dispatched model | B's own output/context limits are enforced, independently of A's larger allowance | PASS |
| Continuation preserves model/history or explicitly rejects unsafe reopen | Six positive live-child continuation cases and three expected Codex closed-child reopen rejections | PASS within stated scope |

CodexApter now permits the existing translated-subagent opt-in with either
`selected` or `custom` agent policy on Codex CLI **0.166.0**. Each parent/child
model requires its own exact catalog entry, explicit `supports_search_tool=false`
and no code-only tool mode. Custom child effort must be declared in that child's
own `supported_reasoning_levels`. Missing catalog metadata is a launcher error;
this does not reinterpret unknown upstream capability evidence as unsupported.
The existing production cap of one child remains. Explicit spawn overrides and
custom agent files retain native precedence and are outside this certified
configured-default subset. ClaudApter's existing custom policy already supplies
the correct child model; this batch updates its documentation without changing
its runtime.

## Actual CLI matrix

All tests use source launchers, pinned native executables, synthetic prompts,
fake credentials and loopback upstreams in disposable homes/workspaces. They
prove harness/gateway behavior, not real model quality or provider availability.

| Harness | Upstream Chat | Upstream Messages | Upstream Responses |
|---|---|---|---|
| Codex 0.166.0: parent A, child B, continue same child B | PASS | PASS | PASS |
| Claude Code 2.1.308: parent A, child B, continue same child B | PASS | PASS | PASS |
| Codex 0.166.0: close child B, attempt `resume_agent` | Expected rejection | Expected rejection | Expected rejection |

Positive cases perform a real child Read/MCP tool call, replay its distinct
value, complete once, then continue the same native child ID with prior history
and complete again. Codex uses `send_input`; Claude uses background Agent plus
`SendMessage` and matching native notifications. Verifiers reject a replacement
child, incorrect ID/model, missing replayed value, permission denial, failed
native lifecycle or fabricated boolean statistics. Role classification is based
on task markers, not the observed model, so fallback cannot masquerade as a new
parent request.

### Confirmed native closed-child limitation

An exploratory Codex close/reopen probe reproduced child B being reopened with
parent model A by CLI 0.166.0. The fixture rejected that request. This was a real
native behavior, not a provider failure or guessed incompatibility.

CodexApter therefore enables `codex_model_safe_resume` for gateway sessions
whose configured child default differs from the main model. Protoverta rejects
the namespaced `multi_agent_v1.resume_agent` tool item before forwarding it for
execution. This applies to translated JSON/SSE and native Responses through the
gateway. The fixed error recommends continuing a retained child with
`send_input` or starting a new child. Other namespaces and `send_input`/spawn
remain unchanged. The guard is opt-in for core callers and strict-boolean
validated.

The three negative matrix cases PASS because they reject safely: their positive
history/lifecycle completion fields are deliberately false. They must not be
reported as successful closed-child resume. Direct native Responses mode
bypasses the gateway and follows Codex's behavior; that path is not protected
by this guard. Persisted/closed child resume remains outside the accepted subset
and broader context/resume work remains P08.

## Diagnostics and state boundaries

Observations optionally include `requested_model_digest` and
`effective_model_digest`: HMAC-SHA256 values with a fresh random secret per
gateway handler/session. They permit equality checks within that gateway, not
global model tracking. Names and the secret are not journaled. Malformed model
metadata cannot break observation emission. Old journal rows remain readable;
invalid/raw digest fields are rejected.

Requested means the incoming HTTP model and effective means the outbound model
after gateway profile application. These fields do not reveal an alias already
resolved inside the harness or certify the provider's internal deployment.
Provider-owned previous-response IDs and opaque reasoning state on incompatible
cross-protocol paths remain rejected before network access. Nothing fetches,
rewrites or transfers such state between models/resources.

## Reproduction and artifacts

Run from the Protoverta checkout with the disposable preview Python. Substitute
the source launcher directory and a private temporary report path:

```powershell
& $PreviewPython -B scripts/smoke_aux_models.py --client codex --upstream chat --adapter-source ../free-codex-cli --report "$ReportRoot/codex-chat.json"
& $PreviewPython -B scripts/smoke_aux_models.py --client claude --upstream chat --adapter-source ../free-claude-cli --report "$ReportRoot/claude-chat.json"
& $PreviewPython -B scripts/smoke_aux_models.py --client codex --upstream chat --adapter-source ../free-codex-cli --closed-resume --report "$ReportRoot/codex-chat-closed.json"
```

Repeat each applicable command with `messages` and `responses`. Each owned
fixture job has a bounded lifetime and request ceiling; no external provider
or real credential is used. The source smoke driver supplies the custom child
policy and synthetic exact catalog entries. Production code never copies
parent metadata to certify a child.

Final raw artifacts in the existing isolated P00 preview's `reports` directory:
`p06-final-{codex|claude}-{chat|messages|responses}-continue.json` (six), and
`p06-final-codex-{chat|messages|responses}-closed.json` (three). Prefer these final
reports over developmental probe artifacts. Failed exploratory verifier/schema
assumptions and the initial error-category implementation were corrected and
covered before this final matrix.

## Validation

Final full source suites: **Protoverta 2,038 passed (291.06s)**;
**CodexApter 360 passed + 12 subtests (354.24s)**;
**ClaudApter 413 passed (353.33s)** with five expected mocked-version warnings.
Additional checks: independent pinned SDK gate **26 passed (28.75s)**; Ruff run separately
using each of the three repositories' own configuration passed; scoped strict
mypy passed for all six configured boundary modules.

A 30-second synthetic resource run with model identity tracing enabled passed:
1,059 requests issued, 962 upstreams opened/closed, zero active upstreams,
request lifetime monitors or heartbeat workers left over, no recorded failure
types. See `p06-resource-check.json`. This run preceded the final stateless
closed-resume guard; JSON/SSE guard rejection/cleanup is separately covered by
tests and the final three native negative cases. This is not a long soak or
provider billing assertion.

No real config/session, installed launcher, global environment/PATH, `.env`,
provider key or `free-claude-code` was changed. P07–P14 remain pending. No new
schema-drift, packaging or remote CI certification is claimed by this milestone.
