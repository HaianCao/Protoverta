import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("active", [False, True])
def test_coverage_guard_never_restores_scrubbed_credentials(monkeypatch, active):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("coverage_subprocess_guard")
    monkeypatch.setattr(module, "_patcher", None)
    current = SimpleNamespace(config=SimpleNamespace(serialize=lambda: "synthetic-measurement-config")) if active else None
    monkeypatch.setitem(sys.modules, "coverage", SimpleNamespace(Coverage=SimpleNamespace(current=lambda: current)))
    monkeypatch.setenv("UNRELATED_PRIVATE_TOKEN", "must-not-inherit")
    original = Mock()
    monkeypatch.setattr(module.subprocess, "Popen", original)
    if not active:
        with pytest.raises(pytest.UsageError, match="active coverage"):
            module.pytest_configure(None)
        original.assert_not_called()
        return
    environment = {"SAFE": "fixture"}
    try:
        module.pytest_configure(None)
        module.subprocess.Popen([sys.executable, "-m", "codexapter.cli", "--help"], env=environment)
        assert original.call_args.kwargs["env"] == {"SAFE": "fixture", "COVERAGE_PROCESS_CONFIG": "synthetic-measurement-config"}
        assert environment == {"SAFE": "fixture"}
        module.subprocess.Popen([sys.executable, "-c", "pass"], env=environment)
        assert original.call_args.kwargs["env"] == environment
    finally:
        module.pytest_unconfigure(None)
    assert module.subprocess.Popen is original
