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
    # Import paths are not credentials. Source-tree validation (for example the
    # ecosystem workflow) relies on PYTHONPATH instead of an installed package,
    # and test subprocesses must import the same checkout.
    if os.environ.get("PYTHONPATH"):
        safe["PYTHONPATH"] = os.environ["PYTHONPATH"]
    os.environ.clear()
    os.environ.update(safe)
