import importlib
import zipfile
from pathlib import Path

import pytest


def test_upgrade_reads_real_metadata_version_not_filename_or_hardcoded_alpha(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("validate_upgrade")
    path = tmp_path / "arbitrary-file-name.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("protoverta-0.3.0a7.dist-info/METADATA", "Name: protoverta\nVersion: 0.3.0a7\n\n")
    assert module.wheel_version(path) == "0.3.0a7"


@pytest.mark.parametrize("name,version", [("other-package", "1"), ("protoverta", ""), ("protoverta", "bad version")])
def test_upgrade_rejects_wrong_or_missing_package_identity(monkeypatch, tmp_path, name, version):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("validate_upgrade")
    path = tmp_path / "core.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("core.dist-info/METADATA", f"Name: {name}\nVersion: {version}\n\n")
    with pytest.raises(ValueError, match="Protoverta wheel metadata"):
        module.wheel_version(path)
