# Coordinated preview validation and release

Prepared versions (not published): Protoverta **0.3.0a8**, CodexApter and
ClaudApter **0.2.0a2** on their integration branches. Stable launcher main
branches remain separate. Never replace a6 release assets with newer code.

Publish order matters: both launcher installers download
`releases/download/v0.3.0a8/protoverta-0.3.0a8-py3-none-any.whl`. Push the core
and its `v0.3.0a8` tag first and wait for the Release workflow to attach the
wheel; only then push launcher branches that pin it. Until the asset exists the
installers stop before creating a venv with an explicit message (or accept
`-ProtovertaWheel PATH`). Bump the core version and both pins/URLs together for
any later release.

## Local acceptance

```powershell
python scripts/validate_ecosystem.py --codex-source ../free-codex-cli --claude-source ../free-claude-cli --output PATH_TO_EMPTY_TEMP_DIRECTORY
```

Install the core's test/typing extras and build/Twine tooling in a disposable
development environment first. The gate checks scoped strict types (including
positive/negative fixtures), source suites, lint, matching dependency pins, distributions,
installed runtime constructors and all three suites using the installed wheels.
It also installs the pinned test-only SDKs into the fresh wheel environment and
runs independent stream conformance with the installed core (no provider calls).
Gateway tests may not skip; an absent/obsolete core fails explicitly. The wheel
phase overrides source-pythonpath so editable checkout imports cannot hide a
bad wheel. Output must be empty, preventing reuse of stale installed artifacts.

Use that temporary installed Python for browser checks (--installed) and local
CLI fixtures from docs/compatibility/client-gates.md. Browser Run is stubbed;
the actual CLI checks are separate. No API key or live model is required.

scripts/validate_upgrade.py accepts the immutable public a6 wheel and all three
new wheels. It creates another temporary venv, checks old configuration with a6,
upgrades the core and verifies config/key bytes remain unchanged while the new
runtime API works. It never edits a real installation or configuration.

## CI layers

- Core CI runs unit/fault/churn tests, lint and the scoped type gate on
  Windows/Linux, Python 3.11/3.13. Release and ecosystem gates also check types.
- Consumer CI has direct-only and pinned-gateway jobs. Required gateway tests
  cannot silently skip. These jobs need the a7 public asset after publication;
  they must fail, not silently fall back to a6, while it is unavailable.
- Ecosystem integration is an explicit workflow_dispatch with required consumer
  commit refs, using the selected core workflow ref. It builds matching snapshots,
  runs installed-wheel suites/browser/real-CLI local fixtures, isolated installer
  lifecycle and a6 upgrade. It uses read-only GitHub permissions, no provider
  secrets and no live inference. Selected commits must already exist on GitHub.
  A draft local workflow is not evidence of a remote CI pass.

## Publication order (requires an explicit user release/push request)

1. Review/commit the three source snapshots and run all local gates. Record refs
   and artifact hashes. Consumer integration commits must be available remotely
   before dispatching the ecosystem workflow against those exact refs.
2. Run core platform CI and the ecosystem job. Do not merge integration into
   launcher main as part of validation.
3. Only after passing gates, publish a NEW v0.3.0a7 tag/assets through the release
   workflow. The tag must match pyproject.toml. Never force/reuse an old tag.
4. Verify a7 asset availability, then run both consumer pinned-gateway CI jobs
   and one-line preview install/update/uninstall checks. Do not publish a consumer
   preview relying on an unavailable core release.
5. Real-provider certification is opt-in, model/endpoint/feature-specific and
   separate from local fixtures; it requires approved keys/models and quota.
   See [the certification checklist](compatibility/certification.md).

Keep signed/hidden reasoning and unsupported protocol features strict. No
automatic fallback, production state mutation or release is authorized by this
document or by a passing fixture.
