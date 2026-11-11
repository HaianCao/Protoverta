"""Fail when modules shared verbatim by the two launchers drift apart.

CodexApter and ClaudApter keep separate copies of a few security/transport
helpers so each launcher stays installable on its own. A fix applied to one
copy must reach the other; this check makes a missed copy visible. Package
names and each launcher's own error class are normalized before comparison;
nothing else is ignored.
"""

import argparse
import difflib
import sys
from pathlib import Path

SHARED_MODULES = (
    "command.py",
    "connection_probe.py",
    "gateway_options.py",
    "http_deadline.py",
    "provider_url.py",
    "retry_log.py",
    "retry_relay.py",
    "settings_store.py",
    "toml_text.py",
    "web_http.py",
    "web_security.py",
)
PACKAGES = {"codex": "codexapter", "claude": "claudapter"}
# Each launcher raises its own error type from otherwise identical code.
ERRORS = {"codex": "ConfigError", "claude": "ClaudapterError"}


def _normalized(path: Path, launcher: str) -> list[str]:
    text = path.read_text(encoding="utf-8").replace(PACKAGES[launcher], "<package>")
    return text.replace(ERRORS[launcher], "<LauncherError>").splitlines(keepends=True)


def drift(codex_source: Path, claude_source: Path, modules=SHARED_MODULES) -> list[str]:
    """Return one human-readable report per missing or differing module."""
    codex_root = codex_source / "src" / PACKAGES["codex"]
    claude_root = claude_source / "src" / PACKAGES["claude"]
    reports = []
    for name in modules:
        codex_file, claude_file = codex_root / name, claude_root / name
        missing = [str(path) for path in (codex_file, claude_file) if not path.is_file()]
        if missing:
            reports.append(f"{name}: missing {', '.join(missing)}")
            continue
        left, right = _normalized(codex_file, "codex"), _normalized(claude_file, "claude")
        if left != right:
            diff = "".join(difflib.unified_diff(left, right, f"codex/{name}", f"claude/{name}"))
            reports.append(f"{name}: copies differ\n{diff}")
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-source", type=Path, required=True)
    parser.add_argument("--claude-source", type=Path, required=True)
    args = parser.parse_args()
    reports = drift(args.codex_source, args.claude_source)
    for report in reports:
        print(report, file=sys.stderr)
    if reports:
        print(f"Launcher shared-module drift: {len(reports)} of {len(SHARED_MODULES)} modules.", file=sys.stderr)
        return 1
    print(f"Launcher shared modules in sync: {len(SHARED_MODULES)} modules.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
