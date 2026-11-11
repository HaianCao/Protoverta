"""Read pinned CLI catalog/help contracts in a disposable credential-free home."""

import argparse
import json
import re
import shutil
import tempfile
from pathlib import Path

from probe_process import run_process
from test_environment import sanitized_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="protoverta-model-contract-") as directory:
        env = sanitized_environment()
        for key in ("HOME", "USERPROFILE", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "APPDATA", "LOCALAPPDATA"):
            root = Path(directory) / key
            root.mkdir()
            env[key] = str(root)
        env.update(DISABLE_AUTOUPDATER="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
        version = run_process(
            [shutil.which("codex"), "--version"], env=env, cwd=directory, timeout=3, text=True, encoding="utf-8"
        )
        if version.returncode or version.stdout.strip() != "codex-cli 0.166.0":
            raise ValueError("Inspect a new native catalog version separately before changing the pinned contract")
        for args in (["codex", "debug", "models", "--help"], ["codex", "app-server", "--help"]):
            args[0] = shutil.which(args[0])
            result = run_process(args, env=env, cwd=directory, timeout=10, text=True, encoding="utf-8")
            print(result.stdout[:6000])
        result = run_process(
            [shutil.which("codex"), "debug", "models", "--bundled"],
            env=env,
            cwd=directory,
            timeout=15,
            text=True,
            encoding="utf-8",
        )
        data = json.loads(result.stdout)
        entry = data["models"][0]
        print(json.dumps({k: type(v).__name__ for k, v in entry.items()}))
        path = Path(directory) / "catalog.json"
        for name, model in (
            ("complete", {**entry, "slug": "vendor/model:free"}),
            ("minimal", {"slug": "vendor/model:free"}),
        ):
            path.write_text(json.dumps({"models": [model]}), encoding="utf-8")
            args = [
                shutil.which("codex"),
                "--no-daemon",
                "-c",
                "model_catalog_json=" + json.dumps(str(path)),
                "-c",
                'model_provider="fixture"',
                "-c",
                'model_providers.fixture.name="Fixture"',
                "-c",
                'model_providers.fixture.base_url="http://127.0.0.1:9/v1"',
                "-c",
                'model_providers.fixture.wire_api="responses"',
                "debug",
                "models",
            ]
            result = run_process(args, env=env, cwd=directory, timeout=12, text=True, encoding="utf-8")
            print(
                json.dumps(
                    {
                        "case": name,
                        "code": result.returncode,
                        "output": result.stdout[:150],
                        "error": result.stderr[-800:],
                    }
                )
            )
        required = []
        for key in entry:
            candidate = {**entry, "slug": "vendor/model:free"}
            candidate.pop(key)
            path.write_text(json.dumps({"models": [candidate]}), encoding="utf-8")
            result = run_process(args, env=env, cwd=directory, timeout=3, text=True, encoding="utf-8")
            if result.returncode and re.search(r"missing field `" + re.escape(key) + "`", result.stderr):
                required.append(key)
            elif result.returncode:
                raise RuntimeError("Unexpected native catalog probe failure")
        print(json.dumps({"native_required_fields": required}))
        expected = {
            "slug",
            "display_name",
            "supported_reasoning_levels",
            "shell_type",
            "visibility",
            "supported_in_api",
            "priority",
            "support_verbosity",
            "truncation_policy",
            "experimental_supported_tools",
        }
        if set(required) != expected:
            raise RuntimeError("Pinned native ModelInfo contract drift")
        if options.report:
            options.report.write_text(
                json.dumps(
                    {"passed": True, "cli_version": version.stdout.strip(), "required_fields": required}, indent=2
                ),
                encoding="utf-8",
            )


if __name__ == "__main__":
    main()
