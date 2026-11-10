"""Check a previous local wheel -> current build in a fresh temporary environment."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import tomllib
import venv
import zipfile
from email.parser import BytesParser
from pathlib import Path

FIXTURE = """
import hashlib,importlib.metadata,json,sys
from pathlib import Path
from unittest.mock import patch
from codexapter.config import load as codex_load
from codexapter.launcher import _gateway as codex_gateway
from claudapter.config import load as claude_load
from claudapter.launcher import _gateway as claude_gateway
root,stage,version=Path(sys.argv[1]),sys.argv[2],sys.argv[3]
assert importlib.metadata.version('protoverta')==version
codex=root/'codex.toml';claude=root/'claude.toml';key=root/'.env'
if stage=='legacy':
    codex.write_text('schema_version = 1\\n[providers.fixture]\\ndisplay_name = "Legacy Codex"\\nbase_url = "https://example.test/v1"\\nprotocol = "chat_completions"\\nexecution_mode = "gateway"\\nauth_secret_ref = "fixture-main"\\n[secrets.fixture-main]\\nbackend = "environment"\\nenv_var = "FIXTURE_TOKEN"\\n',encoding='utf-8')
    claude.write_text('schema_version = 1\\n[[providers]]\\nid = "fixture"\\ndisplay_name = "Legacy Claude"\\nbase_url = "https://example.test/v1"\\nprotocol = "chat"\\nexecution_mode = "gateway"\\nauth_mode = "bearer"\\n',encoding='utf-8')
    key.write_text('FIXTURE_TOKEN=synthetic-upgrade-key\\n',encoding='utf-8')
    (root/'fingerprints.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (codex,claude,key)}),encoding='utf-8')
for path,load,gateway,token_patch,label in [(codex,codex_load,codex_gateway,'codexapter.launcher.resolve_token','Legacy Codex'),(claude,claude_load,claude_gateway,'claudapter.launcher.get_token','Legacy Claude')]:
    config=load(path);provider=config.providers['fixture'];assert provider.display_name==label
    # Launchers pin the core exactly, so the gateway is only built after the upgrade.
    if stage=='upgraded':
        with patch(token_patch,return_value='synthetic-upgrade-key'):
            before,_=gateway(config,provider)
            assert before.upstream_base_url=='https://example.test/v1'
            provider.gateway_runtime={'retry_attempts':2}
            after,_=gateway(config,provider)
            assert after.retry_attempts==2
expected=json.loads((root/'fingerprints.json').read_text(encoding='utf-8'))
assert all(hashlib.sha256((root/name).read_bytes()).hexdigest()==digest for name,digest in expected.items())
print(stage+': legacy config/key bytes unchanged; runtime compatibility PASS')
"""


def wheel_version(path):
    with zipfile.ZipFile(path) as archive:
        names = [item for item in archive.infolist() if item.filename.endswith(".dist-info/METADATA")]
        if len(names) != 1 or names[0].file_size > 65536:
            raise ValueError("Wheel must have one bounded package METADATA file")
        metadata = BytesParser().parsebytes(archive.read(names[0]))
    version = metadata.get("Version")
    if metadata.get("Name") != "protoverta" or not version or any(char.isspace() for char in version):
        raise ValueError("Expected Protoverta wheel metadata with an explicit version")
    return version


def validate(previous, next_wheel, codex, claude):
    for wheel in (previous, next_wheel, codex, claude):
        if not wheel.is_file() or wheel.suffix != ".whl":
            raise ValueError("Expected local wheel: " + str(wheel))
    core = Path(__file__).resolve().parents[1]
    version = tomllib.loads((core / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    previous_version = wheel_version(previous)
    if wheel_version(next_wheel) != version:
        raise ValueError("Next wheel version does not match this checkout")
    env = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"}}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="protoverta-upgrade-check-") as directory:
        root = Path(directory)
        env.update(
            HOME=directory,
            USERPROFILE=directory,
            APPDATA=str(root / "roaming"),
            LOCALAPPDATA=str(root / "local"),
            CODEX_CA_ENV_FILE=str(root / ".env"),
            CLAUDE_CA_ENV_FILE=str(root / ".env"),
        )
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

        def command(*args):
            subprocess.run([str(python), *map(str, args)], cwd=root, env=env, check=True, timeout=180)

        command("-m", "pip", "install", "--no-deps", "--disable-pip-version-check", previous.resolve(), codex.resolve(), claude.resolve())
        command("-B", "-c", FIXTURE, root, "legacy", previous_version)
        command("-m", "pip", "install", "--no-deps", "--disable-pip-version-check", "--force-reinstall", next_wheel.resolve())
        command("-B", "-c", FIXTURE, root, "upgraded", version)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("previous-wheel", "next-wheel", "codex-wheel", "claude-wheel"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    validate(args.previous_wheel, args.next_wheel, args.codex_wheel, args.claude_wheel)


if __name__ == "__main__":
    main()
