"""Explicit pytest-only coverage propagation across sanitized child environments.

Never widen test_environment's credential allowlist. This plugin is loaded only
by check_coverage.py. Native executables and arbitrary grandchildren are outside
the Python CLI subprocess coverage claim.
"""

import copy
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_patcher = None
_source_path = os.environ.get("PYTHONPATH", "")


def pytest_addoption(parser):
    parser.addoption("--coverage-canary-package", required=True)
    parser.addoption("--coverage-canary-pid", required=True)


def pytest_configure(config):
    from coverage import Coverage

    active = Coverage.current()
    if active is None:
        raise pytest.UsageError("Coverage subprocess guard requires an active coverage run")
    # Pinned coverage-test version: serialized configuration is understood by
    # coverage.py's own startup hook. No arbitrary startup code is injected.
    original = subprocess.Popen

    def measured_child(*args, **kwargs):
        child_env = dict(kwargs.get("env") if kwargs.get("env") is not None else os.environ)
        # Instrument only project CLI entry points. Measuring unrelated Python
        # sleep/process-containment fixtures changes their startup deadlines.
        # Explicit env mappings remain scrubbed; only this test configuration
        # is added to the opted-in CLI child, never inherited credentials.
        command = args[0] if args else kwargs.get("args")
        module = None
        if isinstance(command, (list, tuple)) and "-m" in command:
            at = command.index("-m") + 1
            module = command[at] if at < len(command) else None
        child_env.pop("COVERAGE_PROCESS_CONFIG", None)
        child_env.pop("COVERAGE_PROCESS_START", None)
        if module in {"protoverta", "codex_adapter.cli", "claudapter.cli"}:
            child_config = copy.copy(active.config)
            child_config.context = "python-cli:" + module
            child_env["COVERAGE_PROCESS_CONFIG"] = child_config.serialize()
        kwargs["env"] = child_env
        return original(*args, **kwargs)

    global _patcher
    _patcher = patch.object(subprocess, "Popen", measured_child)
    _patcher.start()


def pytest_sessionstart(session):
    from test_environment import sanitized_environment

    package = session.config.getoption("--coverage-canary-package")
    if package not in {"protoverta", "codex_adapter.cli", "claudapter.cli"}:
        raise pytest.UsageError("Unsupported coverage canary")
    env = sanitized_environment()
    env["PYTHONPATH"] = _source_path
    with subprocess.Popen([sys.executable, "-m", package, "--help"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            stdout, stderr = process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            raise pytest.UsageError("Keyless CLI coverage canary timed out") from None
        if process.returncode or b"usage:" not in stdout.lower():
            raise pytest.UsageError("Keyless CLI coverage canary failed: " + stderr.decode(errors="replace"))
        Path(session.config.getoption("--coverage-canary-pid")).write_text(str(process.pid), encoding="ascii")


def pytest_unconfigure(config):
    if _patcher is not None:
        _patcher.stop()
