"""Measure source suites and Python CLI children, without credentials or installation changes."""

import argparse
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from test_environment import sanitized_environment


def measure(project, package, output, core):
    from coverage import Coverage, CoverageData

    output.mkdir()
    data_file = output / ".coverage"
    config_file = output / "coverage.ini"
    config_file.write_text(
        "[run]\nbranch = true\nparallel = true\npatch = subprocess\nsource = " + str(project / "src" / package) + "\ndata_file = " + str(data_file) + "\n",
        encoding="utf-8",
    )
    env = sanitized_environment()
    env["PYTHONPATH"] = os.pathsep.join([str(core / "scripts"), str(core / "src"), str(project / "src")])
    canary = "protoverta" if package == "protoverta" else package + ".cli"
    pid_file = output / "canary.pid"
    command = [
        sys.executable,
        "-m",
        "coverage",
        "run",
        "--rcfile",
        str(config_file),
        "-m",
        "pytest",
        "-p",
        "coverage_subprocess_guard",
        "-p",
        "no:cacheprovider",
        "-o",
        "addopts=",
        "-q",
        "--tb=short",
        "--show-capture=no",
        "--coverage-canary-package",
        canary,
        "--coverage-canary-pid",
        str(pid_file),
        str(project / "tests"),
    ]
    result = subprocess.run(command, cwd=project, env=env, capture_output=True, timeout=600, check=False)
    (output / "pytest.log").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        # Show identities, not captured assertion values or arbitrary stderr.
        # The complete, short-trace report remains in the isolated log file.
        text = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        failures = [line.partition(" - ")[0] for line in text.splitlines() if line.startswith(("FAILED ", "ERROR "))]
        detail = "\n" + "\n".join(failures[:8]) if failures else ""
        raise RuntimeError(f"{project.name} pytest failed (exit {result.returncode}); inspect {output / 'pytest.log'}{detail}")
    int(pid_file.read_text(encoding="ascii"))  # Keep the root PID for diagnostics.
    # Windows venv python.exe may redirect to another OS process. Its measured
    # PID need not equal Popen.pid, so verify a CLI-only coverage context instead.
    child_data = list(output.glob(".coverage.*"))
    if not child_data:
        raise RuntimeError("CLI canary child did not write coverage data")
    measured = set()
    for path in child_data:
        data = CoverageData(basename=str(path))
        data.read()
        data.set_query_context("python-cli:" + canary)
        measured.update(name for name in data.measured_files() if data.lines(name))
    expected = project / "src" / package / ("__main__.py" if package == "protoverta" else "cli.py")
    if not any(Path(name).resolve() == expected.resolve() for name in measured):
        raise RuntimeError("CLI canary coverage is empty or measured the wrong checkout")
    coverage = Coverage(config_file=str(config_file))
    coverage.combine(keep=True)
    coverage.save()
    coverage.json_report(outfile=str(output / "coverage.json"))
    report = json.loads((output / "coverage.json").read_text(encoding="utf-8"))
    return {"project": project.name, "passed": True, "python_child_verified": True, "totals": report["totals"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-source", type=Path, required=True)
    parser.add_argument("--claude-source", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if importlib.metadata.version("coverage") != "7.17.0":
        parser.error("Install the pinned coverage-test extra in a disposable test venv")
    core = Path(__file__).resolve().parents[1]
    output = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix="protoverta-coverage-"))
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error("Use an empty output directory")
    results = []
    for project, package in [(core, "protoverta"), (args.codex_source.resolve(), "codex_adapter"), (args.claude_source.resolve(), "claudapter")]:
        print(f"Running source tests: {project.name} (may take several minutes)", flush=True)
        try:
            record = measure(project, package, output / package, core)
        except RuntimeError as exc:
            print("Coverage check failed: " + str(exc), file=sys.stderr)
            return 1
        results.append(record)
        print(json.dumps(record), flush=True)
    (output / "summary.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
