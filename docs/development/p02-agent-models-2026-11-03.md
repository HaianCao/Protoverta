# P02: session-scoped auxiliary model policy

Status: **COMPLETE for configuration, environment and UI**, awaiting review and
commit. Actual agent spawning/concurrent execution remain P04/P05; auxiliary
model route certification remains P06. No paid/live provider requests, global
installation/configuration changes, merge, push or release.

P01 was committed before this item: Protoverta `fbfa3d1`, CodexApter `83988d2`,
ClaudApter `63c4c9c`. Consumers remain on `integration/protoverta`, core on main.

## User-facing behavior

New providers created through setup default to **Use selected model at launch**.
Existing files with no `agent_models` field, existing-provider edits that omit
the field, and programmatically constructed providers preserve **Inherit harness
settings**. Saving an older provider does not silently enable a new policy.

Both web editors have an Advanced: agent models section. Custom fields appear
only for the custom policy. A saved-policy summary appears before Run. Editing
another provider or adding one resets the editor from that provider's values;
unsaved input is not presented as the saved Run policy.

TOML examples:

```toml
# Both launchers; omit the field or use inherit to preserve harness behavior.
agent_models = { policy = "selected" }
```

```toml
# CodexApter custom model: explicit effort is required for the different model.
agent_models = { policy = "custom", default = "provider/worker", effort = "low" }
```

```toml
# ClaudApter: unspecified roles use the model chosen at launch.
agent_models = { policy = "custom", default = "provider/worker", haiku = "provider/fast" }
```

Policies are provider-scoped and apply to the current launch only. They do not
follow subsequent in-CLI model changes automatically. Explicit extra CLI
arguments/settings and organization-managed constraints retain harness behavior;
this is not a security enforcement mechanism.

## Codex contract

Selected/custom policies require the observed **0.166.0** CLI. They set
`agents.default_subagent_model` and `agents.default_subagent_reasoning_effort`.
Selected uses the launch model/effort, with an optional explicit low/medium/high
agent effort. Custom requires an exact model and explicit low/medium/high effort
to avoid inheriting an unknown setting from a different model.

These are defaults: explicit spawn settings and custom agent files retain CLI
precedence. The policy does not add an agent-enablement setting. P01's agent
disable controls remain on translated routes. Native routes now query the CLI
version too if the user requests an additional agent-model override; native
inherit continues without that query.

Runtime model overrides serialize Unicode scalars directly into TOML strings,
including non-BMP model IDs, rather than emitting invalid surrogate escapes.

## Claude contract

Selected/custom policies require the observed **2.1.308** CLI. They manage
`ANTHROPIC_DEFAULT_{HAIKU,SONNET,OPUS,FABLE}_MODEL`,
`CLAUDE_CODE_SUBAGENT_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`:

- Selected maps all roles to the launch model and sets FORCE=1, explicitly
  overriding custom agent model definitions.
- Custom accepts `default`, `haiku`, `sonnet`, `opus`, `fable`; absent roles use
  the launch model, FORCE=0, and normal definition/invocation precedence applies.
- Inherit supplies no managed role values, retaining existing model-role env.

Managed values are supplied in the child environment and the session --settings
env overlay. The existing thinking-disabled setting is merged into that same
overlay on converted routes. Neither parent env nor persistent agent/settings
files are changed. Family aliases, including extended-context alias forms, are
not accepted as exact model IDs for this mapping, avoiding alias self-reference.

An agent-only effort control is deliberately absent: using the global effort
variable would also affect the main session. Native agent effort configuration
and conversion reasoning restrictions still apply. User-declared model capability
metadata is outside these role mappings; no thinking/context capability is
fabricated or recertified here.

## Validation and limits

Malformed policy types, unexpected keys, empty/oversized IDs, whitespace/control
characters and invalid Unicode reject before publication. Invalid saves preserve
existing config bytes. Existing fieldless files load without implicit migration.
Unknown CLI versions reject a requested selected/custom override during preflight
with an actionable inherit option; they do not silently weaken the policy. This
is an intentional current version limit, including for newly created providers.

The policy validates requested settings, not provider availability, tools or
effort support. A custom model still needs its own evidence; no launch-time live
probe or automatic fallback was added. Preserving custom files in inherit mode
and applying documented precedence do not replace P04 child-execution tests.

Fresh checks, isolated P00 venv, Python 3.13.9 / pytest 9.2.0 / Ruff 0.17.2:

| Check | Result |
|---|---|
| Codex runtime suite | **331 passed + 12 subtests**, 40.34s |
| Claude runtime suite | **406 passed + 9 subtests**, 46.23s |
| Core fixture-policy/resume/tool-success tests | **37 passed**, 2.38s |
| Explicit selected-policy CLI Chat smoke | Codex 0.166.0 and Claude 2.1.308 passed, one local request each |
| Claude role aliases with stale inherited env | Haiku/Sonnet/Opus/Fable each resolved to the selected exact model, one local request each |
| Chromium setup, both consumers | New-provider default, save/reopen, custom fields, inherit/custom visibility, saved Run summary, themes/mobile and foreground handoff passed |
| Ruff | All three repos checked using their own cwd |

Consumer suites exclude unchanged installer-environment tests. Core runtime src
was not changed and its full suite/schema/SDK/soak were not rerun. Browser checks
used Playwright 1.64.1 and existing Chromium 1234 executable in fresh contexts,
synthetic credentials and local endpoints. External page requests were blocked.
No real Codex/Claude terminal was launched by the browser test: a synthetic
foreground child completed a real local gateway round trip and returned exit 7.
Real CLI checks were separate keyless main-session fixtures, without agent spawn.

The earlier Claude runtime attempt exposed an outdated fake-executable fixture:
a new setup provider now selects an explicit version-gated policy, so that test
reached version rejection before its intended missing-MCP-variable error. The
fixture now declares the observed version, preserving the MCP assertions. No
MCP error was suppressed or version guard removed. A Codex test initially merged
TOML tables incorrectly; its parser was fixed, not the production contract.

The browser foreground fixture now answers its own --version query so real
launcher policy can run against that synthetic CLI. The normal smoke script
adds `--agent-model-policy inherit|selected`; it requires an adapter, never
spawns agents itself, and writes only disposable synthetic configuration.

Reproduce the selected-policy main-session check from Protoverta using the
isolated interpreter with matching local packages installed:

```text
python scripts/smoke_clients.py --client codex --upstream chat --adapter-source ../free-codex-cli --agent-model-policy selected
python scripts/smoke_clients.py --client claude --upstream chat --adapter-source ../free-claude-cli --agent-model-policy selected
```

Detailed alias-injection reports and browser screenshots are local P00 preview
artifacts under reports/p02-*. They are not live provider certificates or release
wheel evidence. Editable source installations were used; global wrappers remain
unchanged. No full-schema failure or live-search pending gate was reclassified.

## References and next step

The policy follows [Codex agent defaults and precedence](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[Claude model precedence and FORCE](https://code.claude.com/docs/en/sub-agents),
and [Claude model environment variables](https://code.claude.com/docs/en/env-vars).
These contracts do not guarantee third-party model behavior.

Stop for P02 review before committing. Next is P03, downstream heartbeat and
deadline/error behavior; do not enable subagents as a side effect of that work.
