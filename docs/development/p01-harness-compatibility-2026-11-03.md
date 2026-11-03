# P01: version-scoped harness compatibility

Status: **COMPLETE within the configuration-compatibility scope**, awaiting user
review. P02 auxiliary-model policy and P04 subagent execution are not implemented
by this item. No provider calls, global installation/configuration changes,
consumer-main merge, push or release.

P00 was committed as `c7c5121`. This P01 working change is uncommitted. Consumer
baselines are CodexApter `cde71b2` and ClaudApter `d067eee`, both on
`integration/protoverta`; Protoverta remains on main.

## Contract and implementation

Each launcher has a standard-library-only `harness.py` module, independent of
Protoverta. Only translated routes query the already selected safe CLI prefix
with `--version`. Direct and native gateway invocation construction does not add
a version probe. Existing auth, model selection and reasoning policies remain.

The query has a three-second subprocess timeout, no stdin, no captured stderr,
and a fresh temporary cwd/profile. Only OS executable-discovery essentials are
inherited; credential, proxy, provider and Python import settings are excluded.
At most 257 bytes of the version file are read and only an exact stable release
line of at most 256 bytes is accepted. A malformed, prerelease, failed or timed-out
query becomes UNKNOWN; raw output is never included in the warning.

The result cache holds at most 32 prefix/file-identity entries, refreshed when
the executable or npm entry's stat changes. It stores no environment or secret.
This is not binary attestation. The trusted installed CLI owns the implementation
of its version command; its scratch-file output and subprocess descendants are
not an adversarial executable sandbox. No HTTP request probe is used at launch.

| Client/version | Translated-route decision | Evidence scope |
|---|---|---|
| Codex 0.164.0 | Retain legacy `features.multi_agent=false`, summary none, web search disabled | Historical client fixture acceptance; not rerun here |
| Codex 0.166.0 | Same controls, plus `agents.enabled=false` | Fresh local feature listing and source-launcher Chat smoke |
| Other/unknown Codex | Warn and retain legacy conservative controls | Not version-verified; no automatic agent enablement |
| Claude 2.1.308 | Existing beta/structured-output/cache/tool-search restrictions | Fresh help and source-launcher Chat smoke |
| Other/unknown Claude | Warn and retain existing restrictions | No blanket compatibility claim or version-based native-route block |

Exact versions are intentional: a newer version is not automatically verified
by tuple ordering. The policy does not infer model capabilities or certify
subagents. Native routes preserve the previous user configuration behavior.

Fixture scripts now obtain policy from the selected launcher source or installed
launcher module instead of maintaining a separate copy of the version decisions.
The npm prefix resolver is reused when probing batch shims. A standalone fixture
without a launcher policy explicitly warns that its legacy controls are not
version-verified; a broken dependency or invalid explicitly selected source is
not silently substituted.

## Fresh observations

With isolated Codex 0.166.0 configuration, `features list` reported multi_agent
true by default, false with `features.multi_agent=false`, and still true when
only `agents.enabled=false` was supplied. The feature listing alone does not
prove that the latter agent setting has no separate effect. Both controls are
retained for the observed 0.166.0 profile; the legacy key was not removed.

Codex's root command rejected `--ignore-user-config` before `features list`.
The inspection was rerun with isolated CODEX_HOME/cwd and `--no-daemon`, without
that unsupported root flag. Existing exec-scoped fixture arguments were retained.

Fresh Chat-upstream source-launcher smokes passed for Codex 0.166.0 and Claude
2.1.308, one synthetic request each, exit 0. Codex's captured function list had
no spawn-agent tool. Claude's converted upstream request had no thinking,
structured-output, cache or deferred-tool fields. This is a main-session
configuration check, not subagent, all-route, real-model or wheel acceptance.
The Codex smoke retained the known TEMP helper warning and synthetic-model
metadata warning; no file-edit/helper capability is claimed by this test.

## Validation

All tests ran in the isolated P00 venv using Python 3.13.9, pytest 9.2.0 and
Ruff 0.17.2. Test/build dependencies were installed there only; matched source
packages were installed editable, not release wheels. Hatchling 1.33.0 and
editables 0.6 were needed for the no-build-isolation editable setup.

| Check | Fresh result |
|---|---|
| Codex runtime suite | **301 passed + 12 subtests**, 42.07s |
| Claude runtime suite | **378 passed + 9 subtests**, 43.90s |
| Core script-policy/tool-success/resume-harness tests | **26 passed**, 2.15s |
| Real source CLI Chat smoke | Codex and Claude passed, one local request each |
| Ruff | All three repositories passed using each repository as cwd |
| Whitespace | Checked separately before handoff |

Both runtime suites exclude unchanged `tests/test_install_environment.py`.
No full core, official SDK/schema, installer, soak, browser or remote CI rerun.
Five Claude test warnings come from synthetic CLI/version fixtures, not actual
provider traffic. Earlier targeted checks passed 40 Codex and 58 Claude tests;
the runtime results above supersede those narrower counts for this change.

The new regressions cover stable/malformed/oversized version output, nonzero
exit, timeout/OS errors, environment isolation and no parent mutation, cache
refresh, known/unknown profiles, and native routes avoiding probes. Core tests
cover shared fixture decisions, selected-source loading, missing-policy warnings,
dependency failures and npm-prefix resolution. Existing resume argument tests
mock the new policy boundary because their checkout/executable are synthetic.

Initial validation commands exposed a nonexistent test filename, placeholder
checkout tests needing that mock, and import classification when Ruff was run
from another repository. These were corrected without relaxing assertions or
reformatting unrelated source. The final results above use the proper cwd.

## Remaining work

P02 must manage auxiliary-model environment conflicts and explicit inherit/force
policy. P04 must execute child agents; keeping agent tools disabled in this item
does not complete that acceptance. P03 still owns downstream heartbeat. Full
OpenResponses schema remains a separate failed gate; this change does not fill
unknown metadata or reasoning usage.

Official references checked on 2026-11-03:

- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference): both `features.multi_agent` and `agents.enabled` are documented; local CLI behavior must still be checked.
- [Claude environment variables](https://code.claude.com/docs/en/env-vars): beta and structured-output restrictions used for translated routes.
- [Claude CLI reference](https://code.claude.com/docs/en/cli-reference): model, settings, effort and agent flags.
