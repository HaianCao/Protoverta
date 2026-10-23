"""Opt-in real-provider text probe using isolated consumer and CLI state.

Sends synthetic prompts only. Requires a key already in the named environment
variable. No credentials, client state, or provider responses are saved.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from smoke_clients import MARKER, run_client

from protoverta.core import Protocol
from protoverta.server import GatewayConfig


def probe(client: str, upstream: Protocol, base_url: str, key: str, model: str, adapter_source: Path, *, timeout: int = 120) -> dict:
    executable = shutil.which(client)
    if not executable:
        raise ValueError(f"{client} is not on PATH")
    config = GatewayConfig(upstream, base_url, key, "isolated-probe", upstream_reasoning="openrouter_disabled")
    # run_client receives the prefix before /v1; validate rather than guess it.
    if not base_url.endswith("/v1"):
        raise ValueError("This OpenRouter probe requires an explicit /v1 API prefix")
    with tempfile.TemporaryDirectory(prefix="protoverta-client-probe-") as directory:
        root = Path(directory)
        env = {name: value for name, value in os.environ.items() if name.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"}}
        env.update({"HOME": directory, "USERPROFILE": directory, "APPDATA": str(root / "roaming"), "LOCALAPPDATA": str(root / "local"), "CODEX_HOME": str(root / "codex"), "CLAUDE_CONFIG_DIR": str(root / "claude"), "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_SIMPLE": "1"})
        for name in ("roaming", "local", "codex", "claude"):
            (root / name).mkdir()
        result = run_client(client, executable, "http://127.0.0.1:0", base_url.removesuffix("/v1"), config, env, directory, False, adapter_source, False, upstream, timeout, model)
        # Never print raw request/response bodies or credentials. Retain only
        # status and a bounded, key-redacted error tail on failure.
        passed = result.returncode == 0 and MARKER in result.stdout
        return {"client": client, "upstream": upstream.value, "model": model, "passed": passed, "returncode": result.returncode, "error": "" if passed else (result.stderr + result.stdout)[-2500:].replace(key, "[REDACTED]")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex", "claude"], required=True)
    parser.add_argument("--upstream", type=Protocol, choices=list(Protocol), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--key-env", required=True)
    parser.add_argument("--base-url", default="https://openrouter.ai/api/v1")
    parser.add_argument("--adapter-source", type=Path, required=True)
    parser.add_argument("--allow-live", action="store_true", help="Acknowledge real API calls and possible charges")
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("Live API calls require --allow-live")
    key = os.environ.get(args.key_env)
    if not key:
        parser.error("The configured key environment variable is empty")
    result = probe(args.client, args.upstream, args.base_url, key, args.model, args.adapter_source)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
