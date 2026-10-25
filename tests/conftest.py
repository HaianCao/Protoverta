"""Core tests are keyless: scrub inherited credentials in this pytest process."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from test_environment import sanitized_environment


def pytest_configure(config):
    # Validation is intentionally not a live-provider harness. Mutating this
    # child process cannot edit the launching shell or persistent environment.
    safe = sanitized_environment()
    os.environ.clear()
    os.environ.update(safe)
