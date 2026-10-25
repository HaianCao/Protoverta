"""Strict scoped type gate plus positive/negative boundary fixtures. No network."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from test_environment import sanitized_environment


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="protoverta-type-cache-") as cache:
        command = [sys.executable, "-m", "mypy", "--config-file", str(root / "pyproject.toml"), "--cache-dir", cache]
        env = sanitized_environment()
        baseline = subprocess.run(command, cwd=root, env=env, check=False)
        if baseline.returncode:
            return baseline.returncode
        good = subprocess.run([*command, "tests/typing/valid.py"], cwd=root, env=env, check=False)
        if good.returncode:
            return good.returncode
        bad = subprocess.run([*command, "--no-pretty", "--no-color-output", "tests/typing/invalid.py"], cwd=root, env=env, capture_output=True, text=True, check=False)
        # Exactly the two bad arguments and the out-of-vocabulary outcome.
        # A missing checker/import or a syntax/config error must not count.
        if bad.returncode != 1 or bad.stdout.count("[arg-type]") != 2 or bad.stdout.count("[assignment]") != 1 or bad.stdout.count(": error:") != 3:
            print("Type gate negative fixture did not fail for the expected three boundary errors.", file=sys.stderr)
            print(bad.stdout, file=sys.stderr)
            print(bad.stderr, file=sys.stderr)
            return 1
    print("Scoped type baseline and positive/negative boundary fixtures PASS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
