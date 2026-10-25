import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(("baseline", "good", "bad_code", "output", "expected"), [
    (0, 0, 1, "x: error: a [arg-type]\nx: error: b [arg-type]\nx: error: c [assignment]\n", 0),
    (2, 0, 1, "", 2),
    (0, 2, 1, "", 2),
    (0, 0, 0, "", 1),
    (0, 0, 1, "x: error: missing import [import-not-found]", 1),
    (0, 0, 2, "x: error: syntax error", 1),
    (0, 0, 1, "x: error: a [arg-type]\nx: error: b [arg-type]\nx: error: c [assignment]\nx: error: unexpected [misc]\n", 1),
])
def test_type_gate_does_not_accept_missing_checker_or_wrong_failure(monkeypatch, baseline, good, bad_code, output, expected):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    gate = importlib.import_module("check_types")
    results = iter([SimpleNamespace(returncode=baseline), SimpleNamespace(returncode=good), SimpleNamespace(returncode=bad_code, stdout=output, stderr="")])
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return next(results)

    monkeypatch.setattr(gate.subprocess, "run", run)
    assert gate.main() == expected
    assert len(calls) == (1 if baseline else 2 if good else 3)
