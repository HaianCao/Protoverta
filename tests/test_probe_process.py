import importlib
import sys
from pathlib import Path


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
