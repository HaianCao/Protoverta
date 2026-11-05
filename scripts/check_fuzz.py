"""Pinned, keyless property-based stream gate. Missing dependencies/skips fail."""

import argparse
import importlib.metadata
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from test_environment import sanitized_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true")
    args = parser.parse_args()
    if importlib.metadata.version("hypothesis") != "6.170.9":
        parser.error("install the pinned fuzz-test extra")
    root = Path(__file__).resolve().parents[1]
    env = sanitized_environment()
    paths = [str(root / "tests")]
    if not args.installed:
        paths.append(str(root / "src"))
    env["PYTHONPATH"] = os.pathsep.join(paths)
    with tempfile.TemporaryDirectory(prefix="protoverta-fuzz-") as folder:
        report = Path(folder) / "report.xml"
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "pythonpath=",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "-q",
            "--tb=short",
            str(root / "fuzz_tests"),
            "--junitxml",
            str(report),
        ]
        result = subprocess.run(command, env=env, cwd=folder, check=False)
        if result.returncode:
            return result.returncode
        cases = ET.parse(report).getroot().findall(".//testcase")
        if len(cases) != 24 or any(case.find("skipped") is not None for case in cases):
            return 1
    print("Property stream gate PASS: 24 route properties, 50 generated examples each; no provider calls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
