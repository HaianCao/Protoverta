"""Shared handcrafted fixtures only; never replace the selected core import."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from test_environment import sanitized_environment


def pytest_configure(config):
    safe = sanitized_environment()
    os.environ.clear()
    os.environ.update(safe)
