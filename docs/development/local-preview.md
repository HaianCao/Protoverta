# Local preview and acceptance checks

This procedure tests the integration checkouts without replacing an existing
CodexApter/ClaudApter installation or changing the user PATH. Stable launcher
`main` branches do not include this integration. Local commits and wheels are
not a published release.

## 1. Build an isolated preview

Open a new PowerShell window at the workspace directory containing `protoverta`,
`free-codex-cli`, and `free-claude-cli`. Both launcher checkouts must be on
`integration/protoverta`. Review `git status` first; do not switch branches over
uncommitted work. Python 3.11+ and the underlying Codex/Claude CLIs are required.

```powershell
$workspace = (Get-Location).Path
$previewRoot = Join-Path $env:TEMP ("protoverta-preview-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $previewRoot | Out-Null
py -3 -m venv "$previewRoot\venv"
if ($LASTEXITCODE) { throw "Preview venv creation failed" }
$previewPython = "$previewRoot\venv\Scripts\python.exe"
& $previewPython -m pip install build hatchling pytest
if ($LASTEXITCODE) { throw "Build tools installation failed" }
foreach ($repo in @("protoverta", "free-codex-cli", "free-claude-cli")) {
    & $previewPython -m build --wheel --no-isolation --outdir "$previewRoot\dist" "$workspace\$repo"
    if ($LASTEXITCODE) { throw "Wheel build failed: $repo" }
}
$previewWheels = @(Get-ChildItem "$previewRoot\dist\*.whl" | Select-Object -ExpandProperty FullName)
if ($previewWheels.Count -ne 3) { throw "Expected exactly three matching wheels" }
& $previewPython -m pip install --no-deps @previewWheels
if ($LASTEXITCODE) { throw "Preview installation failed" }
& $previewPython -m pip check
if ($LASTEXITCODE) { throw "Preview dependency check failed" }
```

Use the executable paths below, not bare `codex-ca`/`claude-ca`, which could
still select an older installed wrapper. No activation or persistent PATH change
is needed. Keep `$previewRoot` displayed for locating this preview later.

## 2. Run keyless acceptance experiments first

Run from the workspace root, using the variables from step 1:

```powershell
foreach ($client in @("codex", "claude")) {
    foreach ($protocol in @("chat", "messages", "responses")) {
        & $previewPython "$workspace\protoverta\scripts\smoke_clients.py" --client $client --upstream $protocol --installed-adapter --tool-loop
        if ($LASTEXITCODE) { throw "CLI smoke failed: $client / $protocol" }
    }
}
& $previewPython "$workspace\protoverta\scripts\smoke_resume.py" --client codex --source responses --target chat --adapter-source "$workspace\free-codex-cli" --change-provider --change-model --tool-loop --trace
if ($LASTEXITCODE) { throw "Codex portable-history resume failed" }
& $previewPython "$workspace\protoverta\scripts\smoke_resume.py" --client claude --source messages --target responses --adapter-source "$workspace\free-claude-cli" --change-provider --change-model --tool-loop
if ($LASTEXITCODE) { throw "Claude portable-history resume failed" }
$coreWheel = Get-ChildItem "$previewRoot\dist\protoverta-*.whl" | Select-Object -ExpandProperty FullName
& $previewPython "$workspace\protoverta\scripts\smoke_windows_install.py" --wheel $coreWheel
if ($LASTEXITCODE) { throw "Isolated install/update/uninstall failed" }
```

Expected results are `passed: true` for each CLI/resume check, and successful
install/update/uninstall/config-preservation records for both wrappers. Fixture
upstreams use loopback listeners and synthetic credentials. CLI state is temporary;
tools are read-only (`get_goal` or a temporary-file `Read`). This does not certify
a real provider/model. The resume script disables Codex summaries from the initial
session because this probe tests portable text/tool history.

The installer smoke redirects app data/profile directories and uses
`-NoPathChanges`, skips installing the real CLI, and uses a local core wheel.
Its "update" is a repeat installation of the same checkout/version, not a test
of every historical release upgrade. It can download ordinary build dependencies.
Do not add `--remote` when testing unpublished local changes.

## 3. Configure a separate manual preview

In this same disposable PowerShell window, redirect all preview state before
opening setup. These assignments affect this shell and its children only:

```powershell
foreach ($folder in @("profile", "roaming", "local", "codex", "claude", "work")) {
    New-Item -ItemType Directory -Force -Path "$previewRoot\$folder" | Out-Null
}
$env:USERPROFILE = "$previewRoot\profile"
$env:HOME = "$previewRoot\profile"
$env:APPDATA = "$previewRoot\roaming"
$env:LOCALAPPDATA = "$previewRoot\local"
$env:CODEX_HOME = "$previewRoot\codex"
$env:CLAUDE_CONFIG_DIR = "$previewRoot\claude"
$env:CODEX_CA_CONFIG = "$previewRoot\codex-providers.toml"
$env:CODEX_CA_ENV_FILE = "$previewRoot\codex.env"
$env:CODEX_CA_CACHE = "$previewRoot\codex-cache"
$env:CLAUDE_CA_CONFIG = "$previewRoot\claude-providers.toml"
$env:CLAUDE_CA_ENV_FILE = "$previewRoot\claude.env"
$env:CLAUDE_CA_CACHE = "$previewRoot\claude-cache"
$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"
$env:CLAUDE_CODE_DISABLE_AUTO_MEMORY = "1"
Set-Location "$previewRoot\work"
& "$previewRoot\venv\Scripts\codex-ca.exe" setup
# After closing/stopping that setup server:
& "$previewRoot\venv\Scripts\claude-ca.exe" setup
```

An existing shell can contain provider settings, telemetry/proxy settings and CLI
credentials not listed above. The keyless scripts scrub those automatically. For
manual live checks, review inherited settings and use a fresh shell without
unrelated credentials. Redirecting HOME alone is not a credential scrubber.

In setup, add a provider with its **exact API prefix**, normally
`https://host/v1`, rather than `/chat/completions`, `/messages` or `/responses`.
Select the actual upstream protocol and gateway mode for conversion. Bearer is
the recommended default unless the provider requires `x-api-key`. Enter the key
in this preview's setup form; do not put it in Git or chat. A model catalog entry
is discovery, not evidence of streaming/tool compatibility.

Check create/edit/delete, display-name persistence, manual model entry, model
refresh/search, light/dark themes, and saved default selection. For the first
isolated manual pass, use **Save as default**, close setup, then invoke the preview
CLI explicitly. This permits a read-only Codex invocation with daemon bypass:

```powershell
& "$previewRoot\venv\Scripts\codex-ca.exe" run -- --no-daemon --sandbox read-only exec --ignore-user-config --ignore-rules --skip-git-repo-check "Reply briefly. Do not use tools."
& "$previewRoot\venv\Scripts\claude-ca.exe" print -- --setting-sources "" --strict-mcp-config --tools "" --no-session-persistence "Reply briefly. Do not use tools."
```

The keyless harness verifies its CLI version's flags. If your installed CLI
rejects a flag, inspect that CLI's `--help` before changing the test; never remove
isolation controls just to obtain a pass. Native Responses sessions intended for
later portable replay should start with `-c "model_reasoning_summary='none'"`.
Existing opaque reasoning state is not migrated or rewritten.

Codex's [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
documents its `CODEX_HOME`-relative configuration and read-only sandbox setting;
the fixture harness additionally verifies the installed CLI's concrete flags.

For a real provider, start with a few short requests. Gateway doctor checks make
live calls only after explicit opt-in, for example:

```powershell
& "$previewRoot\venv\Scripts\codex-ca.exe" doctor -p PROVIDER_ID -m MODEL_ID --allow-live --check text --check streaming
& "$previewRoot\venv\Scripts\claude-ca.exe" doctor -p PROVIDER_ID -m MODEL_ID --allow-live --check text --check streaming
```

Only after those pass, add `--check streaming_tools --check tool_result` and test
one read-only tool, then a completed-history resume, and finally simultaneous
sessions with different providers/models. Use bounded budgets; a free model can
still have rate limits. No current local-fixture result replaces these checks.
For manual setup's **Run** button, verify it closes setup and keeps the CLI alive;
that combined browser-to-interactive-terminal flow still needs this manual check.

Close this PowerShell window to discard process environment overrides. The
preview is confined to `$previewRoot`; no uninstaller against the real installation
is needed. Keep any reports you need before removing that exact preview directory.

## Further automated gates

The full core/consumer pytest suites, SDK and property gates, strict type scope,
browser QA, load/cancellation checks and official schema audit have different
purposes. See [conformance](conformance.md), [validation matrix](validation-matrix.md)
and [the latest local report](validation-2026-10-26.md). Passing unit tests or high
line coverage is not proof of exhaustive input coverage or full protocol support.
