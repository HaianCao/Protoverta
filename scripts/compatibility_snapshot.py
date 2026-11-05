"""Payload-free version/source identity for a validation matrix, not certification."""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
import tomllib
from pathlib import Path

from test_environment import sanitized_environment

CLI_VERSION_MATRIX = {
    "codex": {
        "accepted_baselines": ["0.163.0", "0.164.0", "0.166.0"],
        "evaluated_candidates": ["0.166.0"],
    },
    "claude": {
        "accepted_baselines": ["2.1.308"],
        "evaluated_candidates": ["2.1.308"],
    },
}
PINNED_SDK_BASELINE = {
    "openai": "3.37.0",
    "anthropic": "2.3.0",
}


def project_identity(root):
    body = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    digest = hashlib.sha256()
    for path in sorted((root / "src").rglob("*")):
        if path.is_file() and path.suffix in (".py", ".html"):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
    env = sanitized_environment()
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], env=env, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], env=env, text=True).strip())
    return {"name": body["name"], "version": body["version"], "head": head, "dirty": dirty, "runtime_sha256": digest.hexdigest()}


def snapshot(workspace):
    dependencies = {}
    for package in ("openai", "anthropic", "hypothesis", "playwright", "psutil"):
        try:
            dependencies[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            dependencies[package] = None
    return {
        "schema_version": 1,
        "python": platform.python_version(),
        "os": sys.platform,
        "projects": [project_identity(workspace / name) for name in ("protoverta", "free-codex-cli", "free-claude-cli")],
        "test_dependencies": dependencies,
        "cli_version_matrix": CLI_VERSION_MATRIX,
        "pinned_sdk_baseline": PINNED_SDK_BASELINE,
        "provider_certification": False,
        "scope": "Source identity only. Attach named validation results; a snapshot is not a passed test or CLI/provider compatibility claim.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    raw = json.dumps(snapshot(Path(__file__).resolve().parents[2]), indent=2)
    if args.output:
        args.output.write_text(raw + "\n", encoding="utf-8")
    print(raw)


if __name__ == "__main__":
    main()
