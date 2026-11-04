"""Pinned independent SDK conformance, loopback only; missing deps/skips fail."""

import argparse
import importlib.metadata
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

from test_environment import sanitized_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true", help="Test installed core, disabling pytest's source path")
    parser.add_argument("--allow-candidate-sdk", action="store_true", help="Evaluate candidate SDK versions in a separate evaluation job without replacing the pinned baseline gate")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    requirements = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]["sdk-test"]
    for requirement in requirements:
        name, expected = requirement.split("==")
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            parser.error("SDK gate requires the pinned sdk-test extra; missing " + name)
        if actual != expected and not args.allow_candidate_sdk:
            parser.error("SDK gate requires exact pinned version for " + name)
    env = sanitized_environment()
    if not args.installed:
        env["PYTHONPATH"] = str(root / "src")
    with tempfile.TemporaryDirectory(prefix="protoverta-sdk-report-") as folder:
        report = Path(folder) / "report.xml"
        if args.installed:
            verify = [sys.executable, "-B", "-c", "import pathlib,sys,protoverta; p=pathlib.Path(protoverta.__file__).resolve(); assert p.is_relative_to(pathlib.Path(sys.prefix).resolve()) and 'site-packages' in p.parts, 'Installed SDK gate requires a wheel in this environment, not checkout/editable code'; print('SDK core import:',p)"]
            imported = subprocess.run(verify, cwd=folder, env=env, check=False)
            if imported.returncode:
                return imported.returncode
        command = [sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-o", "addopts=", "-o", "pythonpath=", "--tb=short", "--show-capture=no", "-q", str(root / "sdk_tests"), "--junitxml", str(report)]
        result = subprocess.run(command, cwd=root, env=env, check=False)
        if result.returncode:
            return result.returncode
        cases = ET.parse(report).getroot().findall(".//testcase")
        if not cases or any(case.find("skipped") is not None for case in cases):
            print("SDK conformance may not be empty or skip tests.", file=sys.stderr)
            return 1
    print("Independent SDK gate PASS (installed=" + str(args.installed) + "): inference routes/truncation/usage policies and strict model-list parsing/pagination verified. See docs/development/conformance.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
