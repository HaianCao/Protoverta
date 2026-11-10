# Development scripts

None of these scripts are part of the installed package. Most run offline against loopback fixtures; scripts marked **live** send real provider requests, need an explicit flag and API keys, and may cost money.

## CI gates

| Script | What it checks |
|---|---|
| `check_types.py` | Strict scoped mypy gate and positive/negative typing fixtures |
| `check_fuzz.py` | Pinned property-based stream tests (missing dependencies or skips fail) |
| `check_sdk.py` | Pinned official OpenAI/Anthropic SDKs against the gateway, loopback only |
| `check_openresponses.py`, `build_openresponses_runner.cjs` | The official OpenResponses compliance runner against loopback fixtures |
| `check_coverage.py`, `coverage_subprocess_guard.py` | Coverage across source suites and CLI child processes |
| `check_test_report.py`, `gateway_test_guard.py` | JUnit evidence is present and gateway tests did not skip |
| `check_launcher_sync.py` | Modules shared verbatim by CodexApter and ClaudApter have not drifted |
| `check_web_runtime.cjs` | Inline setup-page runtime controls with a minimal DOM |
| `validate_ecosystem.py` | Core and both launchers built, installed and wired together (ecosystem workflow) |
| `validate_upgrade.py` | A previous Protoverta wheel upgrades to the current build with configs intact |
| `compatibility_snapshot.py` | Payload-free version and source identity for a validation run |

## Local smoke tests with the real CLIs (no keys)

`smoke_clients.py`, `smoke_heartbeat.py`, `smoke_resume.py`, `smoke_compaction_resume.py`, `smoke_subagents.py`, `smoke_parallel_agents.py`, `smoke_nested_agents.py`, `smoke_three_streams.py`, `smoke_agent_cancel.py`, `smoke_agent_faults.py`, `smoke_agent_worktree.py`, `smoke_aux_models.py`, `smoke_parent_exit.py`, `smoke_model_picker.py`, `smoke_two_launchers.py`, `smoke_windows_install.py`, `soak_local.py`, `browser_setup_smoke.py`.

They drive the installed Codex CLI or Claude Code against scripted loopback upstreams with isolated homes and clean up the processes they start. Helpers: `harness_policy.py`, `inspect_model_contracts.py`, `probe_console.py`, `probe_process.py`, `probe_gateway_context.py`, `setup_launch_fixture.py`, `test_environment.py`, `trace_adapter.py`, `resource_monitor.py`, `windows_probe_job.py`.

## Live provider probes (opt-in, may cost money)

`probe_clients.py`, `probe_models.py`, `probe_formats.py`, `probe_images.py`, `probe_logprobs.py`, `probe_namespace.py`, `smoke_tool_search.py`, `stream_diagnostics.py`.

Each needs an explicit `--allow-live` (or equivalent) flag and reads keys from the environment; results report shapes and counts, never response content or keys. Provider findings are summarised in [`docs/compatibility/providers.md`](../docs/compatibility/providers.md).
