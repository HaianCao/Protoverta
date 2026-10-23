"""Isolated Windows install/update/uninstall smoke for both preview launchers.

Requires a local Protoverta wheel; never changes the real user PATH or app data.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def run(script: Path, options: list[str], env: dict[str, str]) -> None:
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *options], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=420, check=False)
    if result.returncode:
        raise RuntimeError(f"{script.parent.parent.name} {script.name} failed: {(result.stdout + result.stderr)[-1600:]}")


def exercise(source: Path, wheel: Path, root: Path, *, command: str, skip: str, install_name: str) -> dict:
    roaming = root / "roaming"
    local = root / "local"
    profile = root / "profile"
    for path in (roaming, local, profile):
        path.mkdir(parents=True)
    env = dict(os.environ, APPDATA=str(roaming), LOCALAPPDATA=str(local), USERPROFILE=str(profile))
    sentinel = roaming / install_name / "preserve.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("synthetic-settings\n", encoding="utf-8")
    installer = source / "scripts" / "install.ps1"
    uninstaller = source / "scripts" / "uninstall.ps1"
    executable = local / "Programs" / install_name / "venv" / "Scripts" / f"{command}.exe"
    python = executable.with_name("python.exe")
    options = [skip, "-NonInteractive", "-NoPathChanges", "-ProtovertaWheel", str(wheel)]
    for _ in range(2):
        run(installer, options, env)
        if not executable.is_file():
            raise RuntimeError(f"{command} executable missing")
        subprocess.run([str(executable), "--help"], env=env, capture_output=True, check=True, timeout=30)
        subprocess.run([str(python), "-c", "import importlib.metadata; assert importlib.metadata.version('protoverta') == '0.3.0a3'"], env=env, capture_output=True, check=True, timeout=30)
        if sentinel.read_text(encoding="utf-8") != "synthetic-settings\n":
            raise RuntimeError("test configuration changed during install/update")
    run(uninstaller, ["-NonInteractive", "-NoPathChanges"], env)
    if executable.exists() or not sentinel.is_file():
        raise RuntimeError("uninstall did not remove only the wrapper")
    return {"project": source.name, "install": True, "update": True, "uninstall": True, "config_preserved": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="protoverta-windows-install-") as directory:
        isolated = Path(directory)
        results = [
            exercise(root / "free-codex-cli", wheel, isolated / "codex", command="codex-ca", skip="-SkipCodexInstall", install_name="codex-ca"),
            exercise(root / "free-claude-cli", wheel, isolated / "claude", command="claude-ca", skip="-SkipClaudeInstall", install_name="claudapter"),
        ]
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
