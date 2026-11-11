"""Isolated Windows install/update/uninstall smoke for both preview launchers.

Requires a local Protoverta wheel; never changes the real user PATH or app data.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import tomllib
from pathlib import Path

from test_environment import sanitized_environment


def run(script: Path, options: list[str], env: dict[str, str]) -> None:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *options],
        env=env,
        cwd=env["USERPROFILE"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=420,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"{script.parent.parent.name} {script.name} failed: {(result.stdout + result.stderr)[-1600:]}"
        )


def run_remote(repo: str, name: str, options: list[str], env: dict[str, str]) -> None:
    url = f"https://raw.githubusercontent.com/HaianCao/{repo}/integration/protoverta/scripts/{name}.ps1"
    command = f'& ([scriptblock]::Create((irm "{url}"))) ' + " ".join(options)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        env=env,
        cwd=env["USERPROFILE"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=420,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"{repo} remote {name} failed: {(result.stdout + result.stderr)[-1600:]}")


def exercise(
    source: Path, wheel: Path, root: Path, *, command: str, skip: str, install_name: str, remote_repo: str | None = None
) -> dict:
    roaming = root / "roaming"
    local = root / "local"
    profile = root / "profile"
    # A working directory outside every source checkout proves the installed
    # launcher does not depend on repository-relative imports or files.
    outside_cwd = root / "unrelated cwd"
    for path in (roaming, local, profile, outside_cwd):
        path.mkdir(parents=True)
    env = dict(
        sanitized_environment(),
        APPDATA=str(roaming),
        LOCALAPPDATA=str(local),
        USERPROFILE=str(profile),
        HOME=str(profile),
        PIP_CACHE_DIR=str(local / "pip-cache"),
        PSModuleAnalysisCachePath=str(local / "psmodule-cache"),
    )
    sentinel = roaming / ("codexapter" if command == "codex-ca" else "claudapter") / "preserve.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("synthetic-settings\n", encoding="utf-8")
    installer = source / "scripts" / "install.ps1"
    uninstaller = source / "scripts" / "uninstall.ps1"
    install_root = local / "Programs" / install_name
    executable = install_root / "bin" / f"{command}.exe"
    with (source / "pyproject.toml").open("rb") as project_file:
        metadata = tomllib.load(project_file)["project"]
    requirements = metadata.get("dependencies", []) + metadata.get("optional-dependencies", {}).get("gateway", [])
    expected_version = next(item.split("==", 1)[1] for item in requirements if item.startswith("protoverta=="))
    options = (
        [skip, "-NonInteractive", "-NoPathChanges", "-WithGateway", "-SourceBranch", "integration/protoverta"]
        if remote_repo
        else [skip, "-NonInteractive", "-NoPathChanges", "-ProtovertaWheel", str(wheel)]
    )
    for index in range(2):
        if remote_repo:
            run_remote(
                remote_repo,
                "install",
                options
                if index == 0
                else [skip, "-NonInteractive", "-NoPathChanges", "-SourceBranch", "integration/protoverta"],
                env,
            )
        else:
            run(installer, options, env)
        executable = install_root / "bin" / f"{command}.exe"
        # Published older installers may still use the legacy venv layout.
        if remote_repo and not executable.is_file():
            executable = install_root / "venv" / "Scripts" / f"{command}.exe"
        if not executable.is_file():
            raise RuntimeError(f"{command} executable missing")
        if executable.parent.name == "bin":
            generation = (install_root / "current.txt").read_text(encoding="ascii").strip()
            if len(generation) != 32 or any(char not in "0123456789abcdef" for char in generation):
                raise RuntimeError("invalid environment pointer")
            python = install_root / "environments" / generation / "Scripts" / "python.exe"
            launch = [str(executable)]
        else:
            python = executable.with_name("python.exe")
            launch = [str(executable)]
        subprocess.run([*launch, "--help"], env=env, capture_output=True, check=True, timeout=30)
        check_shells(executable, env, outside_cwd)
        subprocess.run(
            [
                str(python),
                "-c",
                "import importlib.metadata,sys; assert importlib.metadata.version('protoverta') == sys.argv[1]",
                expected_version,
            ],
            env=env,
            capture_output=True,
            check=True,
            timeout=30,
        )
        if sentinel.read_text(encoding="utf-8") != "synthetic-settings\n":
            raise RuntimeError("test configuration changed during install/update")
    if remote_repo:
        run_remote(remote_repo, "uninstall", ["-NonInteractive", "-NoPathChanges"], env)
    else:
        run(uninstaller, ["-NonInteractive", "-NoPathChanges"], env)
    if executable.exists() or not sentinel.is_file():
        raise RuntimeError("uninstall did not remove only the wrapper")
    return {
        "project": source.name,
        "install": True,
        "update": True,
        "uninstall": True,
        "config_preserved": True,
        "install_root_has_space": " " in str(install_root),
        "shells": ["cmd", "powershell"],
        "outside_repo_cwd": True,
        "absolute_and_child_path_launch": True,
        "user_path_changed": False,
    }


def check_shells(executable: Path, env: dict[str, str], cwd: Path) -> None:
    """Launch the installed executable through CMD and PowerShell from ``cwd``."""
    if any(char in str(executable) for char in "\"'%&^|<>"):
        raise RuntimeError("synthetic install path contains shell metacharacters")
    if executable.stem not in {"codex-ca", "claude-ca"}:
        raise RuntimeError("shell smoke accepts only the two synthetic wrapper executables")
    commands = {
        "cmd": f'cmd.exe /d /s /c ""{executable}" --help"',
        "powershell": [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"& '{executable}' --help; exit $LASTEXITCODE",
        ],
    }
    for shell, command in commands.items():
        result = subprocess.run(
            command,
            env=env,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if result.returncode or "usage" not in (result.stdout + result.stderr).lower():
            raise RuntimeError(f"{shell} launch of {executable.name} failed: {(result.stdout + result.stderr)[-800:]}")
    # Prove bare-command resolution too, without changing User/Machine PATH.
    child_env = {**env, "PATH": str(executable.parent) + os.pathsep + env.get("PATH", "")}
    bare = {
        "cmd": ["cmd.exe", "/d", "/s", "/c", f"{executable.stem} --help"],
        "powershell": [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"$resolved = (Get-Command {executable.stem} -CommandType Application | Select-Object -First 1).Source; if ($resolved -ne '{executable}') {{ throw \"Wrong wrapper resolved from child PATH: $resolved\" }}; & {executable.stem} --help; exit $LASTEXITCODE",
        ],
    }
    for shell, command in bare.items():
        result = subprocess.run(
            command,
            env=child_env,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if result.returncode or "usage" not in (result.stdout + result.stderr).lower():
            raise RuntimeError(
                f"{shell} bare command lookup failed in isolated child PATH: {(result.stdout + result.stderr)[-1000:]}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument(
        "--remote", action="store_true", help="Use the published one-line preview installer and release wheel"
    )
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    root = Path(__file__).resolve().parents[2]
    # The prefix deliberately contains spaces so APPDATA/LOCALAPPDATA/USERPROFILE
    # and the generated install paths all exercise quoted-path handling.
    with tempfile.TemporaryDirectory(prefix="protoverta windows install ") as directory:
        isolated = Path(directory)
        results = [
            exercise(
                root / "free-codex-cli",
                wheel,
                isolated / "codex",
                command="codex-ca",
                skip="-SkipCodexInstall",
                install_name="codex-ca",
                remote_repo="CodexApter" if args.remote else None,
            ),
            exercise(
                root / "free-claude-cli",
                wheel,
                isolated / "claude",
                command="claude-ca",
                skip="-SkipClaudeInstall",
                install_name="claudapter",
                remote_repo="ClaudApter" if args.remote else None,
            ),
        ]
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
