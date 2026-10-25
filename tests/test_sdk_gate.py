import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


def gate_module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("check_sdk")


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize(("returncode", "xml", "expected"), [
    (0, '<testsuites><testsuite><testcase name="valid"/></testsuite></testsuites>', 0),
    (0, '<testsuites><testsuite><testcase><skipped/></testcase></testsuite></testsuites>', 1),
    (0, '<testsuites><testsuite/></testsuites>', 1),
    (1, '', 1),
])
def test_sdk_gate_rejects_skips_empty_suites_and_cleans_inherited_pythonpath(monkeypatch, installed, returncode, xml, expected):
    gate = gate_module(monkeypatch)
    monkeypatch.setattr(gate.importlib.metadata, "version", lambda name: {"openai": "3.37.0", "anthropic": "2.3.0"}[name])
    monkeypatch.setattr("sys.argv", ["check_sdk", *(["--installed"] if installed else [])])
    monkeypatch.setenv("PYTHONPATH", "unrelated-old-checkout")

    def run(command, *, cwd, env, check):
        assert "unrelated-old-checkout" not in env.get("PYTHONPATH", "")
        assert ("PYTHONPATH" in env) is not installed
        if "-c" in command:
            assert installed and "site-packages" in command[-1]
            return SimpleNamespace(returncode=0)
        assert "pythonpath=" in command
        if not installed:
            assert Path(env["PYTHONPATH"]).resolve() == Path(__file__).resolve().parents[1] / "src"
        if xml:
            Path(command[command.index("--junitxml") + 1]).write_text(xml, encoding="utf-8")
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(gate.subprocess, "run", run)
    assert gate.main() == expected


@pytest.mark.parametrize("missing", [False, True])
def test_sdk_gate_fails_before_pytest_when_pinned_parser_is_missing_or_wrong(monkeypatch, missing):
    gate = gate_module(monkeypatch)
    monkeypatch.setattr("sys.argv", ["check_sdk"])

    def version(name):
        if missing:
            raise gate.importlib.metadata.PackageNotFoundError(name)
        return "0.0.0"

    monkeypatch.setattr(gate.importlib.metadata, "version", version)
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **kw: pytest.fail("must fail before spawning tests"))
    with pytest.raises(SystemExit) as caught:
        gate.main()
    assert caught.value.code == 2
