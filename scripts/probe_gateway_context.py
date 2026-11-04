"""Capture only synthetic gateway ownership for isolated launcher fixtures.

The temporary endpoint/token file is private to the probe and never reported.
Requests, tokens and gateway behavior are not rewritten by this observer.
"""

import importlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path

from protoverta import server


def main():
    module, destination = sys.argv[1:3]
    if module not in {"codex_adapter.cli", "claudapter.cli"}:
        raise ValueError("Synthetic launcher observation only")
    path = Path(destination).resolve()
    if path.parent != Path.cwd().resolve() or path.exists():
        raise ValueError("Gateway observation must use a new owned cwd file")
    original = server.gateway_session

    @contextmanager
    def capture(config, **kwargs):
        if not config.upstream_api_key.startswith("p05-synthetic-key-"):
            raise ValueError("Only synthetic credentials are permitted")
        with original(config, **kwargs) as origin:
            path.write_text(json.dumps({"origin": origin, "token": config.inbound_token, "cwd": str(Path.cwd().resolve())}), encoding="utf-8")
            yield origin
        path.with_suffix(".closed").write_text("closed", encoding="utf-8")

    sys.argv = [module, *sys.argv[3:]]
    server.gateway_session = capture
    try:
        return importlib.import_module(module).main()
    finally:
        server.gateway_session = original


if __name__ == "__main__":
    raise SystemExit(main())
