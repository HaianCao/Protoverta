# Alpha-8 local validation — 2026-10-26

Windows, Python 3.13.9; keyless local fixtures. Core candidate 0.3.0a8,
consumers 0.2.0a2. No remote CI, publication or real-provider certification.

| Gate | Result |
| --- | --- |
| Core source suite and coordinated coverage | 1386 passed; Python CLI child coverage verified |
| Upgrade metadata regressions added after suite collection | 4 passed separately |
| CodexApter source suite | 202 passed, 12 subtests |
| ClaudApter source suite | 161 passed, 9 subtests |
| Installed CLI protocol/tool matrix | 6/6, including the two native controls |
| Changed-provider/model transcript resume after completed tools | Codex Responses → Chat and Claude Messages → Chat passed |
| Installed external MCP integration | Codex Chat/Messages and Claude Chat/Responses: actual fixture tool calls/results and synthetic environment expansion passed |
| Pinned independent SDKs | 19 passed; five positive routes, truncation coverage and explicit unknown-usage schema rejection, not six positive strict routes |
| Property streams | 24 properties, 50 generated examples each |
| Scoped typing and Ruff | Passed in all applicable projects |
| Chromium setup and Run handoff | Both installed consumers passed; actual CSP retained; visible Codex reasoning checkbox checked |
| Bounded resource run | 400 issued, 363 verified clients, 37 cancelled; 363 upstream streams opened/closed; no active/lifetime threads left; retained thread/handle/TCP deltas zero |
| Isolated Windows install/update/uninstall | Both consumers passed with local core wheel; config sentinel preserved; NoPathChanges and redirected app/profile directories |
| Previous core a7 → a8 | Passed in new venv; legacy config and synthetic key bytes unchanged |
| Full official OpenResponses schema | **FAILED: 0/12 passed, 12 failed, 0 skipped** |

Coverage of runtime source (line / branch): core **91.04% / 83.21%**,
CodexApter **82.82% / 73.35%**, ClaudApter **83.65% / 72.28%**.
No coverage thresholds were relaxed. The four later upgrade-validator tests
exercise development tooling; they do not change the measured runtime source.

The resource run ended on its 400-request cap after 17.744 seconds, following
5.979 seconds of same-workload warmup. It is not a 75-second or long-term soak.
Client cost fields printed by the scripted fixtures are CLI accounting metadata,
not evidence of paid requests. All provider endpoints in these gates were local.

## Full-schema findings retained

Official suite revision `8907bc3b7c4300ff5c21fd647cfde9929243afa1`, unmodified
runner SHA-256 `c825257dc3b45165e31e3b02c8e52b2188e91f1de0e7af72f613cfa4a1e80e4c`.
The JSON envelope now supplies its own lifecycle/null context and gateway
snapshots restore request facts. Remaining schema errors include absent
truncation/text defaults in standalone JSON conversion, provider sampling and
penalty fields, service tier, nullable tool strictness and unknown usage details.
These failures are not reclassified as skips or successful provider evidence.
Do not infer unreported values or manufacture reasoning signatures to fill them.
Standalone response-only conversion lacks the original request: legacy tools,
tool-choice and parallel-call defaults are not evidence of the actual upstream
request policy. The gateway now restores explicitly supplied request values;
fully strict standalone request-policy metadata remains an open contract gap.

The helper check outside TEMP removed Codex's startup refusal. No persistent
apply_patch helper file was observed from that debug invocation, so actual
apply_patch advertisement/execution remains model/CLI-specific acceptance.
The MCP fixture returns fixed synthetic data. An actual external search/fetch
server and its network access still need operational configuration/testing.

## Local evidence locations

- `TEMP/protoverta-alpha8-acceptance-6k70rl0w`: six CLI cases, two resumes,
  SDK/fuzz/types/browser, bounded resources. Its schema record's `passed=true`
  means expected failure reproduced, **not schema compliance**.
- `TEMP/protoverta-alpha8-final-a3giyfwm`: final environment-reference MCP cases,
  SDK, all Ruff, full-schema audit with explicit `FAILED_FULL_SCHEMA`, isolated
  Windows install/update/uninstall.
- `TEMP/protoverta-coverage-2htjm9t5`: coordinated coverage JSON and suite logs.

These are disposable local evidence directories, not repository resources.
Release order and unpublished wheel dependencies are described in
[alpha8-preparation.md](alpha8-preparation.md). Both consumer main branches and
the user's real install/config/PATH/sessions were left unchanged. The old user
preview under TEMP was retained.
