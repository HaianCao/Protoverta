import importlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def checker(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("check_coverage")


def test_failed_suite_names_are_reported_without_assertion_values(checker, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "coverage", SimpleNamespace(Coverage=None, CoverageData=None))
    report = b"FAILED tests/test_fixture.py::test_contract - AssertionError: private-fixture-value\n1 failed\n"
    monkeypatch.setattr(checker.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 1, report, b""))
    output = tmp_path / "report"
    with pytest.raises(RuntimeError) as caught:
        checker.measure(tmp_path, "protoverta", output, tmp_path)
    assert "tests/test_fixture.py::test_contract" in str(caught.value)
    assert "private-fixture-value" not in str(caught.value)
    assert "exit 1" in str(caught.value)
    assert (output / "pytest.log").read_bytes() == report


def test_failed_coverage_command_returns_nonzero_without_traceback(checker, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["check_coverage.py", "--codex-source", str(tmp_path), "--claude-source", str(tmp_path), "--output", str(tmp_path / "output")])
    monkeypatch.setattr(checker.importlib.metadata, "version", lambda _: "7.17.0")

    def failed(*args):
        raise RuntimeError("synthetic suite failed; inspect pytest.log")

    monkeypatch.setattr(checker, "measure", failed)
    assert checker.main() == 1
    output = capsys.readouterr()
    assert "synthetic suite failed" in output.err and "pytest.log" in output.err
    assert "Traceback" not in output.err
