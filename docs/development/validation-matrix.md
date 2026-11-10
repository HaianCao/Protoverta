# Versioned compatibility and validation records

## Latest local acceptance — 2026-11-04

Latest runtime follow-up: [Responses snapshot consistency](response-snapshot-fixes-2026-11-04.md),
with its own final source/wheel hashes and fresh SDK/CLI/full-suite results.
It removes `truncation`/`text` audit findings; full OpenResponses schema still
fails. The CI/P14 records below remain distinct historical checkpoints.

The subsequent [CI job review](ci-jobs-review-2026-11-04.md) adds candidate
evaluation, installed-wheel and artifact gates, fixes standalone-core test
isolation, and records local validation separately from pending remote jobs.

See [P14 source/wheel acceptance](p14-packaging-acceptance-2026-11-04.md): core
0.3.0a8 at `52b201c` plus the uncommitted Responses SSE parameter correction;
consumers 0.2.0a2 at `e757fd3` / `1d727fa`. It records runtime/wheel hashes,
all three full source suites, installed SDK/property gates, six real CLI tool
loops, both installed browser flows and isolated Windows lifecycle/shell checks.
Codex 0.166.0 / Claude 2.1.308 and pinned SDK 3.37.0 / 2.3.0 are the tested
baselines. No new provider live probe, remote CI, or soak is claimed. Full-schema
OpenResponses remains failing; a newer-SDK candidate evaluation remains pending.
The dated matrix below is historical, not a description of the current build.

Record source identity with `python scripts/compatibility_snapshot.py --output
SNAPSHOT.json`. It captures each project's version, Git HEAD, dirty flag and hash
of runtime Python/HTML bytes, plus local interpreter/platform and installed test
dependency versions. A dirty checkout needs its hash: HEAD alone does not identify
the tested code. A snapshot is not a test or provider capability record.
The earlier [identity snapshot](validation-2026-10-25.json) and
[validation results](validation-results-2026-10-25.json) keep passes and the
full-schema audit failures separate.
The later [three regression fixes](review-fixes-2026-10-25.md) have their own
validation scope and do not overwrite that historical identity.

## Current scope (2026-10-25, Windows / Python 3.13.9)

| Gate | Version/scope | Interpretation |
| --- | --- | --- |
| Core/launchers | Protoverta 0.3.0a7; consumers 0.2.0a1; local uncommitted snapshots | Match source hashes and wheel bytes; not published versions |
| SDK consumers | OpenAI 3.37.0, Anthropic 2.3.0; five positive SSE routes, Messages → Responses unknown-usage schema rejection, six-route truncation checks | Independent loopback fixtures, no real provider; see [scope](conformance.md) |
| Real CLI tool loops | Codex CLI 0.164.0, Claude Code 2.1.308; each against Chat/Messages/Responses | Six installed-launcher cases, two rounds/two read-only tool results each; temporary client state |
| Property streams | Hypothesis 6.170.9; 24 properties x50 examples | Text/JSON/partitions/invalid terminals, bounded generated coverage |
| Official schema | OpenResponses revision 8907bc3; Node 24.13.1 | Full required schema audit currently fails; see audit document |
| Browser | Playwright 1.64.1; Chromium 156.0.8078.4 | Both local setup apps, desktop/mobile, two themes; synthetic APIs and stubbed CLI launch |
| Windows lifecycle | Local source installer, matched local core wheel, isolated APPDATA/LOCALAPPDATA/profile, NoPathChanges | Install/update/uninstall and preserved config; no system prerequisites or registry PATH changes tested |
| Measured soak | psutil 7.2.2; two independent gateway listeners/accounts; eight clients | Attach exact duration/counters/resources; not a provider billing or production leak guarantee |

Provider/model/CLI-version certification must be a separate dated record with
exact model, API prefix, source/upstream protocol, JSON/SSE/tool cases, profiles,
core identity and tested CLI/SDK version. Existing historical live reports must
not be silently upgraded to this snapshot. No new live route or actual CLI resume
was tested by these additions. Existing CI Linux/Windows/Python matrices are job
definitions; local Windows results do not prove a remote CI run or Linux pass.

## Browser checks

Install the `browser-test` extra into a disposable environment and install its
matching Chromium into a dedicated test cache. Set `PLAYWRIGHT_BROWSERS_PATH`
explicitly if using a cache outside the usual location. Then run:

```text
python scripts/browser_setup_smoke.py --source ../free-codex-cli --package codexapter --screenshots TEMP_DIRECTORY
python scripts/browser_setup_smoke.py --source ../free-claude-cli --package claudapter --screenshots TEMP_DIRECTORY
```

An explicit `--browser-executable` can select a reviewed test Chromium binary;
`--installed` tests installed template/assets. The harness creates new browser
contexts and temporary adapter settings, aborts external page requests, injects
synthetic discovery and stubs CLI launch. It checks empty state, display-name
escaping, provider persistence, all-model search/manual entry, disabled controls
during save, Escape/focus return, discovery-network failure recovery, themes,
correct logo/favicon roles, no horizontal overflow at 390px, and Run shutdown.
Screenshots contain synthetic data only. It does not certify WCAG, every keyboard
interaction, all screen sizes, or the real terminal handoff.

Add `--launch-fixture` to replace only CLI discovery with a controlled Python
child while exercising the real foreground launcher and gateway. That child must
complete a local HTTP round trip without receiving the upstream key. Setup's
listener must already be closed while the child remains alive, and its exit code
must propagate when released. This covers browser-to-foreground-process wiring;
it does not replace real agent/PTY testing. See [the preview guide](local-preview.md).

## Source and Python CLI child coverage

```text
pip install -e ".[test,coverage-test]"
python scripts/check_coverage.py --codex-source ../free-codex-cli --claude-source ../free-claude-cli
```

Use a disposable development venv. The checker creates a fresh output directory,
collects branch coverage and per-suite logs, and requires nonempty child-only
coverage for a keyless CLI `--help` canary. Child identity is verified by coverage
context rather than OS PID because Windows venv launchers can redirect to another
Python process. The explicit pytest plugin adds only coverage configuration to
scrubbed environments for the three project CLI modules. It does not expand the
credential allowlist, instrument unrelated timeout fixtures, modify interpreter
startup files, or measure native agents/arbitrary grandchildren. This optional
tool does not impose a percentage threshold or a runtime dependency.

## Resource and installer checks

For a duration-enforced run and outstanding live acceptance gates, see
[operational acceptance](operational-acceptance.md). The bounded command below
may stop at its request cap; it is not evidence of a multi-hour soak.

```text
pip install -e ".[soak-test]"
python scripts/soak_local.py --seconds 300 --requests 15000 --concurrency 8 --measure-resources
python scripts/smoke_windows_install.py --wheel LOCAL_MATCHED_CORE_WHEEL
```

The sampler records RSS, native threads, handles/fds and process TCP connections
once per second with a bounded sample count. Missing counters fail the measured
gate. Its declared heuristic policy is <=64 MiB warm RSS growth, <=2 retained
threads, <=4 retained handles/fds and TCP count no higher than baseline. These
tolerances are observations under a finite synthetic workload, not universal
resource limits. Retained-resource comparisons use an explicit five-second
same-workload warmup baseline; cold counters and the warmup's own outcomes remain
in the report. A reproduced two-cycle check found a first-use +50 Windows handles
followed by zero additional handles in the second cycle, including after return,
GC and quiescence; the warmup separates this startup cost without widening the
thresholds. It does not establish that every startup allocation is harmless.
RSS growth uses the first sample after min(30 seconds, measured duration / 3)
as its reference; retained thread/handle/TCP deltas use the prewarmed start.
Every rejected HTTP response is closed by the load client too.

Installer smoke skips official CLI installation, uses temporary user directories
and NoPathChanges, preserves a sentinel config, and removes only temporary wrapper
programs. It deliberately does not test registry environment reset, destructive
data deletion or a first-time Python/Node installation on this workstation. Those
require a disposable VM/runner. No remote one-line installation of unpublished
integration artifacts is claimed.
