"""Local three-repository source/package gate. Never publish or install globally."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def command(args, cwd, *, env=None):
    subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=True)


def validate(core, codex, claude, output):
    projects = [core.resolve(), codex.resolve(), claude.resolve()]
    wheels = []
    artifacts = []
    for project in projects:
        if not (project / "pyproject.toml").is_file():
            raise ValueError("Expected project checkout: " + str(project))
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env["PYTHONPATH"] = os.pathsep.join((str(project / "src"), str(core.resolve() / "src")))
        command([sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-o", "addopts=", "-q"], project, env=env)
        command([sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"], project)
        destination = output / "dist" / project.name
        command([sys.executable, "-m", "build", "--no-isolation", "--outdir", destination], project)
        built = list(destination.glob("*.whl"))
        if len(built) != 1:
            raise ValueError("Use an empty output directory for reproducible package validation.")
        wheels.extend(built)
        artifacts.extend(destination.glob("*"))
    command([sys.executable, "-m", "twine", "check", *artifacts], core)
    environment = output / "installed"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    command([python, "-m", "pip", "install", "--disable-pip-version-check", "--no-deps", *wheels], output)
    script = """
from pathlib import Path
from unittest.mock import patch
from protoverta.server import GatewayConfig
from codex_adapter.config import Config, Provider
from codex_adapter.launcher import _gateway
from claudapter.config import Config as ClaudeConfig, Provider as ClaudeProvider
from claudapter.launcher import _gateway as claude_gateway
from protoverta.provider_profiles import ProviderProfiles
GatewayConfig('chat','https://example.test/v1','fixture','local',client_tool_search='function',provider_profiles=ProviderProfiles(),retry_attempts=2)
p=Provider('demo','Demo','https://example.test/v1',protocol='chat_completions',execution_mode='gateway',client_tool_search='function')
p.gateway_runtime={'retry_attempts':2}
with patch('codex_adapter.launcher.resolve_token',return_value='fixture'):
    config,_=_gateway(Config(path=Path('fixture.toml')),p)
assert config.client_tool_search=='function' and config.retry_attempts==2
p=ClaudeProvider('demo','Demo','https://example.test/v1',protocol='messages',execution_mode='gateway')
p.gateway_runtime={'retry_attempts':2}
with patch('claudapter.launcher.get_token',return_value='fixture'):
    config,_=claude_gateway(ClaudeConfig(path=Path('fixture.toml')),p)
assert config.upstream_base_url=='https://example.test/v1' and config.retry_attempts==2
print('Three isolated wheels: runtime API compatibility PASS')
"""
    clean = dict(os.environ)
    clean.pop("PYTHONPATH", None)
    command([python, "-B", "-c", script], output, env=clean)
    return {"passed": True, "output": str(output), "local_validation_only": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-source", type=Path, required=True)
    parser.add_argument("--claude-source", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    core = Path(__file__).resolve().parents[1]
    output = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix="protoverta-ecosystem-"))
    output.mkdir(parents=True, exist_ok=True)
    print(json.dumps(validate(core, args.codex_source, args.claude_source, output)))


if __name__ == "__main__":
    main()
