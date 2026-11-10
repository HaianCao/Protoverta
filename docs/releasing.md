# Validation and release

Published previews: Protoverta **0.3.0a8** (2026-11-06), **0.3.0a10**
(2026-11-07) and **0.3.0a11** (2026-11-07; Responses streams without
unrequested text logprobs, connection-level slot grace, Python 3.14 in CI).
`v0.3.0a9` was tagged but never released (the tag was removed on 2026-11-07): its Windows
validation exposed a request-slot release race and slow per-transaction SQLite
connections, both fixed in a10 (WAL plus one kept connection; a 0.5 s slot
grace). CodexApter and ClaudApter **0.2.0a2** on their integration branches pin
the latest core preview. Stable launcher main branches remain separate. Never
replace published release assets or move pushed tags; every code change ships
under a new version.

Publish order matters: both launcher installers download
`releases/download/vX/protoverta-X-py3-none-any.whl`. Push the core and its tag
first and wait for the Release workflow to attach the wheel; only then push
launcher branches that pin it. Until the asset exists the installers stop before
creating a venv with an explicit message (or accept `-ProtovertaWheel PATH`).
Bump the core version and both pins/URLs (`pyproject.toml` gateway extra,
`scripts/install.ps1`, `.github/workflows/ci.yml`, README) together for any
later release.

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

Versions are plain `x.y.z`. Protoverta is released first; the launchers pin
it exactly and verify its wheel by SHA-256.

1. Run all local gates on the three release branches and record their SHAs.
   Push the branches and run core CI plus the ecosystem workflow against the
   exact launcher SHAs.
2. Merge Protoverta's release branch into `main` and push a NEW tag `vX.Y.Z`
   that matches `pyproject.toml` (never move or reuse a tag). The release
   workflow re-runs CI, builds the wheel and sdist, writes `SHA256SUMS`,
   attests build provenance, creates the GitHub release and, when the
   repository variable `PYPI_PUBLISH` is `true`, publishes to PyPI through
   Trusted Publishing (environment `pypi`).
3. Download the published wheel, check it against `SHA256SUMS`, and set that
   hash as `$protovertaSha256` in both launchers' `scripts/install.ps1`
   (replacing `PENDING-RELEASE`). The launchers' release workflow refuses to
   run without a real hash.
4. Push the launchers' release branches, open a pull request into `main`, wait
   for CI (it installs the published Protoverta wheel), merge, and push their
   tags `vX.Y.Z`; their release workflow publishes the same way.
5. Check the one-line install, update from the previous release and uninstall
   on Windows, and the setup page in a browser.
6. Real-provider certification is opt-in, model/endpoint/feature-specific and
   separate from local fixtures; it requires approved keys/models and quota.
   See [the certification checklist](compatibility/certification.md).

PyPI setup (once per project, by the account owner): on pypi.org add a
"pending publisher" for `protoverta`, `claudapter` and `codexapter` with owner
`HaianCao`, the repository, workflow `release.yml` and environment `pypi`;
create the `pypi` environment in each repository and set `PYPI_PUBLISH=true`.

Keep signed/hidden reasoning and unsupported protocol features strict. No
automatic fallback, production state mutation or release is authorized by this
document or by a passing fixture.
