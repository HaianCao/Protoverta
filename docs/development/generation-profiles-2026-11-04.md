# Generation contracts validation — 2026-11-04

Added exact evidence-scoped sampling profiles for Chat, Messages and Responses,
and an opt-in Responses penalty dialect. Defaults remain absent unless explicitly
configured. Messages temperature is bounded to one. Capability negatives retain
precedence. Strict reasoning and native response handling are unchanged.

## Local results

Windows / Python 3.13 only; no remote workflow was run or code pushed.

| Check | Result |
|---|---|
| New generation-profile tests | 47 cases, included below |
| Focused profile regression suites | 76 passed |
| Full Protoverta source suite | 2544 passed / 384.23 s |
| Full Codex source suite | 400 passed + 12 subtests / 294.83 s |
| Full Claude source suite | 432 passed / 298.07 s; five expected compatibility warnings |
| Ruff, all declared core CI paths and both consumers | Pass, from each project's own working directory |
| Installed pinned SDK gate | 29 passed; includes four JSON/SSE probability scenarios |
| Installed property gate | 24 properties, 50 examples each |
| Scoped strict types | Pass, six modules plus boundary fixtures |
| Real Codex/Claude CLI with synthetic loopback upstream | All six protocol/client tool loops pass |
| Unchanged official full-schema runner | **0/12 pass, 78 field findings**, no skipped cases |

The official audit runs without operator defaults or a declared penalty dialect.
Its missing sampling (24), penalties (24), top-logprobs (12), service tier (12),
and Messages reasoning usage (6) are still unknown. This feature does not certify
full OpenResponses conformance. No value is invented to satisfy its schema.

## Artifact provenance

Raw logs, XML, installed wheels and `manifest.json` are under the local-only
`%LOCALAPPDATA%/ProtovertaPreviews/snapshot-20261104-generation-final` directory.
Build checked packaged runtime bytes against source and excluded credentials.
`pip check` and site-packages import checks passed in a fresh matched venv.

| Artifact | SHA-256 |
|---|---|
| protoverta-0.3.0a8 | `aec2525e946810f06ea471def1dc6e3bce0cc848dee6da98d289a26a97bfbbb9` |
| codexapter-0.2.0a2 | `531e3b02e48a161fa91fbbc05ba30741053d8dd57d970306524554ba5a3f2530` |
| claudapter-0.2.0a2 | `fa45ce17b53fae73120da1adc05500659d4d32647e42b801c362beb7b68ba5ba` |

The manifest records core HEAD `fcb6690` plus the then-uncommitted generation
runtime hash `3869f1dbc7dc8acfb752be7b9a68ab7fa5818f1a0a469ff87388ced5b7473690`.
Consumer integration HEADs are `e698b09` and `f85e06f`. Documentation added after
the build does not change these runtime bytes.

## Real-provider boundary

See [bounded live logprob results](live-logprobs-2026-11-04.md). Four authorized
short requests were made without retries. Neither approved provider/model route
was certified for final-text logprobs. Those negative results are separate from
synthetic wire-contract tests and are not overridden by these generation profiles.
No additional provider calls were made for this stage.

## Remaining acceptance

Windows install/update/uninstall isolation is tracked separately. Interactive
browser/PTY acceptance on the user's chosen provider remains manual. Ubuntu and
Python 3.11 jobs, remote CI and release validation are not claimed by this local
Windows run. Publication is deferred at the user's request.
