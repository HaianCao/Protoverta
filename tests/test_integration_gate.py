import os
import subprocess
import sys
from pathlib import Path


def test_integration_guard_rejects_skipped_gateway_tests(tmp_path):
    fixture = tmp_path / "test_gateway_runtime.py"
    fixture.write_text("import pytest\ndef test_required():\n    pytest.skip('synthetic missing integration')\n", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(root / "src"), str(root / "scripts"))))
    result = subprocess.run([sys.executable, "-B", "-m", "pytest", "-p", "gateway_test_guard", "-p", "no:cacheprovider", "-o", "addopts=", "-q", str(fixture)], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=15, check=False)
    assert result.returncode != 0
    assert "Integration gateway tests must not skip" in result.stdout + result.stderr


def test_integration_guard_rejects_missing_core(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root / "scripts"))
    result = subprocess.run([sys.executable, "-B", "-c", "import sys;sys.modules['protoverta']=None;import pytest;sys.exit(pytest.main(['-p','gateway_test_guard','--collect-only']))"], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=15, check=False)
    assert result.returncode != 0
    assert "Integration tests require Protoverta" in result.stdout + result.stderr
