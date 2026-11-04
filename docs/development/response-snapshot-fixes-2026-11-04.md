# Responses snapshot consistency — 2026-11-04

Completed locally, uncommitted. No push, release, native install/config/session,
PATH or real provider key changes. Existing CI changes from the preceding review
remain uncommitted and were preserved.

## Result

The unmodified pinned OpenResponses audit no longer reports `truncation` or
`text` findings. There are **112 field-error occurrences instead of 132**, while
the verdict remains **FAILED_FULL_SCHEMA: 0 passed / 12 failed / 0 skipped**.
Twenty eliminated field occurrences are not twenty passing tests. Remaining
failures still concern undeclared tool/sampling policies, serving tier, Messages
reasoning usage subtotals and unsupported penalty/logprob controls.

See [the actual audit output](response-snapshot-audit-2026-11-04.json) and
[bounded validation receipt](response-snapshot-validation-2026-11-04.json).

## Changes

- `response_context.generated_response_metadata()` supplies the same known
  stateless envelope facts to JSON and SSE. Both report input
  `truncation="disabled"`, no storage/background work and no previous-response
  link. Output-limit truncation still returns `status="incomplete"`, its
  `incomplete_details` and no fabricated completion time.
- Bare encoders without request provenance no longer infer text format or
  insert null sampling placeholders. Unknown reasoning configuration is null;
  actual supported reasoning content and measured usage remain separate.
- The HTTP gateway knows the converted outbound request: absence of a
  structured-output constraint means its requested mode is unconstrained text.
  It restores that known mode as `text.format.type="text"`, including omitted
  and empty input format settings. Explicit JSON Schema settings are preserved.
  This does not certify a provider's obedience to a schema or infer its sampling
  defaults. A bare response converter with no request cannot make this claim.
- If the Codex profile really inserts an output cap for Messages, generated
  snapshots report that cap. A configured default unused on a Chat route is not
  reported as applied; explicit caller limits retain precedence.
- Native Responses JSON/SSE keeps its upstream metadata unchanged, including
  `truncation="auto"`, persistence/background flags, previous-response IDs,
  sampling and serving tier. These policies affect generated Responses only.

There is no change to provider selection, retries/fallback, supported request
parameters or reasoning migration. Unknown usage is not replaced with zero.
The six official cases, validator, synthetic upstream fixture and selected
upstream protocols were not patched.

## Verification of the final revision

Windows / Python 3.13.9; Codex CLI 0.166.0, Claude Code 2.1.308, Node 24.13.1.
All inference uses scripted loopback fixtures, not real providers.

| Gate | Actual result |
|---|---|
| Focused metadata/streaming/audit regression group | 135 passed |
| Full Protoverta suite | 2,208 passed, 331.85 s |
| Full CodexApter suite | 400 passed + 12 subtests, 297.68 s |
| Full ClaudApter suite | 432 passed, 299.21 s; five existing conservative version-fixture warnings |
| Official SDK against installed wheel | 29 passed, 66.36 s; OpenAI 3.37.0 / Anthropic 2.3.0 |
| Generated stream properties against installed wheel | 24 passed, 50 examples/property, 16.93 s |
| Scoped types | PASS; six runtime/boundary modules plus positive/negative fixtures, not whole-project strict typing |
| Actual CLI tool loops through installed launchers | 6/6 passed; both clients via Chat/Messages/Responses; each two rounds, two tool results, exit 0 |
| Packaging | Three wheels built, package/asset bytes matched source; all three imports verified under new venv site-packages; pip check PASS |
| Ruff and diff whitespace checks | PASS |
| Official full schema | FAIL remains correctly reported, with the two finding categories eliminated |

The 42 new cases cover shared JSON/SSE facts, completed/incomplete output,
unknown/null context, explicit zero/false/empty controls, gateway-applied limits,
plain versus JSON Schema settings, input immutability and native passthrough.
The older standalone-encoder test was updated to stop demanding a format it
cannot know; it now verifies that unknown format/sampling are absent. The HTTP
tests separately require known format metadata and check the actual outbound
request. No test was skipped to obtain a pass.

An initial local harness imposed a shared `CODEX_CA_ENV_FILE` override, causing
three unrelated file-precedence/diagnostics tests to fail. The override was
removed from source-suite environments, retaining isolated HOME/APPDATA/TEMP;
the final full suites above passed with fresh processes. Production credential
code and those tests were not weakened. Initial intermediate artifacts remain
under `snapshot-20261104-bd986baa`; final evidence is separate.

Final preview: `%LOCALAPPDATA%/ProtovertaPreviews/snapshot-20261104-902bb5cf`.
It contains `manifest.json`, `dist/`, and `reports/` with the source JUnit files,
SDK evidence, property/type logs, official audit and six CLI reports. Dependency
packages were copied from the existing isolated test venv, excluding adapter
packages/editable `.pth`; the adapters were installed from newly built wheels.
This does not certify a clean Internet dependency installation.

Core baseline HEAD: `decee93`, dirty working tree. Tested runtime SHA-256:
`164a9c516d90cb6a792f46ada2d5de4ae0e35d8a0a5d722671d2c638f6dfb0b1`.
Tested core wheel SHA-256:
`1506eafd6f4237807ec50303cd8ec6d8e0f42ab8d76db03037e0f98d25ab6c59`.
Consumer runtime/wheel hashes remain unchanged from P14; their CI/test working
trees are dirty. Version strings alone do not identify these unreleased bytes.

## Remaining boundaries

Full-schema metadata still needs explicit/evidence-backed policies or real
upstream measurements. Native Responses remains subject to its own upstream
contract. No signed/encrypted state migration, hosted tool emulation or model
fallback was added. SDK candidate, remote CI/other platforms, live provider/MCP
and native interactive UI certification retain their separately pending scope.
No new soak, browser QA or installer lifecycle run was needed for this change;
frontend and installer code did not change. Earlier P14 evidence is historical,
not substituted for the new runtime tests above.
