import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_integration_guard_rejects_skipped_gateway_tests(tmp_path):
    fixture = tmp_path / "test_gateway_runtime.py"
    fixture.write_text(
        "import pytest\ndef test_required():\n    pytest.skip('synthetic missing integration')\n", encoding="utf-8"
    )
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(root / "src"), str(root / "scripts"))))
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-p",
            "gateway_test_guard",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            "-q",
            str(fixture),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode != 0
    assert "Integration gateway tests must not skip" in result.stdout + result.stderr


def test_integration_guard_rejects_missing_core(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root / "scripts"))
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import sys;sys.modules['protoverta']=None;import pytest;sys.exit(pytest.main(['-p','gateway_test_guard','--collect-only']))",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode != 0
    assert "Integration tests require Protoverta" in result.stdout + result.stderr


@pytest.mark.parametrize("module_scope", [False, True])
def test_integration_guard_catches_missing_api_in_new_modules_at_collection_or_runtime(tmp_path, module_scope):
    fixture = tmp_path / "test_future_feature.py"
    body = "import pytest\n"
    if module_scope:
        body += "pytest.importorskip('protoverta.not_a_real_api')\ndef test_missing(): pass\n"
    else:
        body += "def test_missing():\n    pytest.importorskip('protoverta.not_a_real_api')\n"
    fixture.write_text(body)
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(root / "src"), str(root / "scripts"))))
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-p",
            "gateway_test_guard",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            "-q",
            str(fixture),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode != 0
    assert "Integration gateway tests must not skip" in result.stdout + result.stderr
