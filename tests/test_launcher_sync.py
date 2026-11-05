import importlib
from pathlib import Path

import pytest


@pytest.fixture
def sync(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("check_launcher_sync")


def _write(root, package, name, text):
    folder = root / "src" / package
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


def test_package_names_are_normalized_and_identical_copies_pass(sync, tmp_path):
    _write(tmp_path / "codex", "codex_adapter", "m.py", "from codex_adapter.errors import E\n")
    _write(tmp_path / "claude", "claudapter", "m.py", "from claudapter.errors import E\n")
    assert sync.drift(tmp_path / "codex", tmp_path / "claude", ("m.py",)) == []


def test_any_other_difference_or_missing_copy_is_reported(sync, tmp_path):
    _write(tmp_path / "codex", "codex_adapter", "m.py", "LIMIT = 1\n")
    _write(tmp_path / "claude", "claudapter", "m.py", "LIMIT = 2\n")
    _write(tmp_path / "codex", "codex_adapter", "only.py", "x = 1\n")
    reports = sync.drift(tmp_path / "codex", tmp_path / "claude", ("m.py", "only.py"))
    assert len(reports) == 2
    assert "copies differ" in reports[0] and "-LIMIT = 1" in reports[0] and "+LIMIT = 2" in reports[0]
    assert reports[1].startswith("only.py: missing")
