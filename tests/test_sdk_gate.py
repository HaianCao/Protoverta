import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

VALID_XML = (
    "<testsuites><testsuite>" + "".join(f'<testcase name="sdk_{i}"/>' for i in range(29)) + "</testsuite></testsuites>"
)


def gate_module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("check_sdk")


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize(
    ("returncode", "xml", "expected"),
    [
        (0, VALID_XML, 0),
        (0, '<testsuites><testsuite><testcase name="incomplete"/></testsuite></testsuites>', 1),
        (0, "<testsuites><testsuite><testcase><skipped/></testcase></testsuite></testsuites>", 1),
        (0, "<testsuites><testsuite/></testsuites>", 1),
        (0, "<testsuites><testsuite><testcase><failure/></testcase></testsuite></testsuites>", 1),
        (0, "<testsuites><testsuite><testcase><error/></testcase></testsuite></testsuites>", 1),
        (1, "", 1),
    ],
)
def test_sdk_gate_rejects_skips_empty_suites_and_cleans_inherited_pythonpath(
    monkeypatch, installed, returncode, xml, expected
):
    gate = gate_module(monkeypatch)
    monkeypatch.setattr(
        gate.importlib.metadata, "version", lambda name: {"openai": "3.37.0", "anthropic": "2.3.0"}[name]
    )
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


@pytest.mark.parametrize("drift", [False, True])
@pytest.mark.parametrize("returncode", [0, 1])
def test_persistent_candidate_evidence_records_actual_versions_and_verdict(monkeypatch, tmp_path, drift, returncode):
    gate = gate_module(monkeypatch)
    versions = {"openai": "99.0.0" if drift else "3.37.0", "anthropic": "2.3.0"}
    monkeypatch.setattr(gate.importlib.metadata, "version", versions.__getitem__)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_sdk", "--allow-candidate-sdk", "--report-dir", "candidate"])

    def run(command, *, cwd, env, check):
        report = Path(command[command.index("--junitxml") + 1])
        assert report.is_absolute()
        report.write_text(VALID_XML)
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(gate.subprocess, "run", run)
    assert gate.main() == returncode
    evidence = json.loads((tmp_path / "candidate/sdk-evidence.json").read_text())
    assert evidence["actual_sdk"] == versions
    assert evidence["baseline_sdk"] == {"openai": "3.37.0", "anthropic": "2.3.0"}
    assert evidence["sdk_version_drift"] is drift
    assert evidence["outcome"] == ("PASSED" if returncode == 0 else "FAILED")
    assert evidence["provider_requests"] == 0


def test_baseline_mismatch_keeps_failure_evidence_and_never_spawns_tests(monkeypatch, tmp_path):
    gate = gate_module(monkeypatch)
    report = tmp_path / "baseline"
    monkeypatch.setattr(gate.importlib.metadata, "version", lambda name: "99.0.0")
    monkeypatch.setattr("sys.argv", ["check_sdk", "--report-dir", str(report)])
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **kw: pytest.fail("baseline drift cannot run tests"))
    with pytest.raises(SystemExit) as caught:
        gate.main()
    assert caught.value.code == 2
    evidence = json.loads((report / "sdk-evidence.json").read_text())
    assert evidence["outcome"] == "FAILED" and evidence["sdk_version_drift"]


def test_report_directory_cannot_reuse_stale_success(monkeypatch, tmp_path):
    gate = gate_module(monkeypatch)
    monkeypatch.setattr(gate.importlib.metadata, "version", {"openai": "3.37.0", "anthropic": "2.3.0"}.__getitem__)
    monkeypatch.setattr("sys.argv", ["check_sdk", "--report-dir", str(tmp_path)])
    sentinel = tmp_path / "sdk-evidence.json"
    sentinel.write_text('"old evidence"')
    with pytest.raises(FileExistsError):
        gate.main()
    assert sentinel.read_text() == '"old evidence"'
