import importlib
import os
import sys
import time
from pathlib import Path

import pytest


def test_isolated_probe_process_returns_success_and_stops_on_timeout(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("probe_process")
    options = {"cwd": str(tmp_path), "text": True, "encoding": "utf-8", "errors": "replace", "timeout": 5, "check": False, "capture_output": True}
    success = module.run_process([sys.executable, "-c", "print('synthetic-ok')"], **options)
    assert success.returncode == 0 and "synthetic-ok" in success.stdout
    # The descendant inherits the stdout pipe. communicate() cannot finish
    # promptly unless process-tree cleanup also terminates that child.
    code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"
    result = module.run_process([sys.executable, "-c", code], **{**options, "timeout": 0.5})
    assert result.returncode == 124
    assert "process tree was terminated" in result.stderr


def test_timeout_cleans_descendant_even_after_its_parent_exits(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("probe_process")
    code = "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print('parent-exited',flush=True)"
    started = time.monotonic()
    result = module.run_process([sys.executable, "-c", code], cwd=str(tmp_path), text=True, encoding="utf-8", errors="replace", timeout=0.5)
    assert result.returncode == 124 and "parent-exited" in result.stdout
    assert time.monotonic() - started < 10


@pytest.mark.skipif(os.name != "nt", reason="Windows process containment")
def test_windows_containment_failure_never_runs_the_suspended_child(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("windows_probe_job")
    calls = []

    class DeniedJob:
        def assign(self, handle):
            calls.append("assign")
            raise OSError("synthetic containment denial")

        def resume(self, thread):
            raise AssertionError("a denied child must never resume")

        def close(self):
            calls.append("close")

    monkeypatch.setattr(module, "WindowsProbeJob", DeniedJob)
    marker = tmp_path / "must-not-exist"
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"
    with pytest.raises(OSError, match="containment denial"), module.windows_probe_process([sys.executable, "-c", code]):
        raise AssertionError("containment must fail before yielding")
    assert calls == ["assign", "close"]
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows process containment")
def test_windows_probe_never_requests_job_breakaway(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("windows_probe_job")
    with pytest.raises(ValueError, match="cannot break away"), module.windows_probe_process([sys.executable], creationflags=0x01000000):
        raise AssertionError("breakaway must be rejected before spawning")
