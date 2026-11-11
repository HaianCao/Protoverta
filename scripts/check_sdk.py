"""Pinned independent SDK conformance, loopback only; missing deps/skips fail."""

import argparse
import importlib.metadata
import json
import platform
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from contextlib import nullcontext
from pathlib import Path

from test_environment import sanitized_environment

EXPECTED_TESTCASES = 29


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true", help="Test installed core, disabling pytest's source path")
    parser.add_argument(
        "--allow-candidate-sdk",
        action="store_true",
        help="Evaluate candidate SDK versions in a separate evaluation job without replacing the pinned baseline gate",
    )
    parser.add_argument(
        "--report-dir", type=Path, help="New directory for persistent JUnit and SDK version/verdict evidence"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    requirements = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ]["sdk-test"]
    baseline = dict(requirement.split("==") for requirement in requirements)
    versions = {}
    for name in baseline:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    env = sanitized_environment()
    if not args.installed:
        env["PYTHONPATH"] = str(root / "src")
    evidence = {
        "python": platform.python_version(),
        "platform": sys.platform,
        "baseline_sdk": baseline,
        "actual_sdk": versions,
        "candidate_mode": args.allow_candidate_sdk,
        "sdk_version_drift": versions != baseline,
        "installed": args.installed,
        "outcome": "FAILED",
        "testcases": 0,
        "expected_testcases": EXPECTED_TESTCASES,
        "provider_requests": 0,
    }
    if args.report_dir:
        args.report_dir = args.report_dir.resolve()
        args.report_dir.mkdir(parents=True, exist_ok=False)
    context = (
        nullcontext(args.report_dir)
        if args.report_dir
        else tempfile.TemporaryDirectory(prefix="protoverta-sdk-report-")
    )
    with context as folder:
        report = Path(folder) / "report.xml"
        try:
            for name, expected in baseline.items():
                if versions[name] is None:
                    parser.error("SDK gate requires the pinned sdk-test extra; missing " + name)
                if versions[name] != expected and not args.allow_candidate_sdk:
                    parser.error("SDK gate requires exact pinned version for " + name)
            return run_tests(root, args.installed, env, report, evidence)
        finally:
            if args.report_dir:
                (args.report_dir / "sdk-evidence.json").write_text(
                    json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
                )


def run_tests(root, installed, env, report, evidence):
    if installed:
        verify = [
            sys.executable,
            "-B",
            "-c",
            "import pathlib,sys,protoverta; p=pathlib.Path(protoverta.__file__).resolve(); assert p.is_relative_to(pathlib.Path(sys.prefix).resolve()) and 'site-packages' in p.parts, 'Installed SDK gate requires a wheel in this environment, not checkout/editable code'; print('SDK core import:',p)",
        ]
        imported = subprocess.run(verify, cwd=report.parent, env=env, check=False)
        if imported.returncode:
            return imported.returncode
    command = [
        sys.executable,
        "-B",
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-o",
        "addopts=",
        "-o",
        "pythonpath=",
        "--tb=short",
        "--show-capture=no",
        "-q",
        str(root / "sdk_tests"),
        "--junitxml",
        str(report),
    ]
    result = subprocess.run(command, cwd=root, env=env, check=False)
    if result.returncode:
        return result.returncode
    cases = ET.parse(report).getroot().findall(".//testcase")
    evidence["testcases"] = len(cases)
    if len(cases) != EXPECTED_TESTCASES or any(
        any(case.find(tag) is not None for tag in ("skipped", "failure", "error")) for case in cases
    ):
        print(
            f"SDK conformance requires all {EXPECTED_TESTCASES} mandatory cases, without skips or failures.",
            file=sys.stderr,
        )
        return 1
    evidence["outcome"] = "PASSED"
    print(
        "Independent SDK gate PASS (installed="
        + str(installed)
        + "): inference routes/truncation/usage policies and strict model-list parsing/pagination verified. See docs/development/conformance.md."
    )
    if evidence["candidate_mode"] and not evidence["sdk_version_drift"]:
        print("Candidate mode used the baseline SDK versions; this is not evidence for newer SDKs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
