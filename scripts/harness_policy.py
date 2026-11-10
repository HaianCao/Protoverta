"""Use launcher compatibility decisions in local CLI acceptance fixtures."""

import importlib
import importlib.util
import json
import warnings
from pathlib import Path


def _policy(package, source):
    if source is not None:
        path = Path(source) / "src" / package / "harness.py"
        spec = importlib.util.spec_from_file_location("fixture_harness_policy", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    try:
        return importlib.import_module(package + ".harness")
    except ModuleNotFoundError as exc:
        if exc.name not in {package, package + ".harness"}:
            raise
        warnings.warn(
            "Launcher version policy unavailable: bare CLI fixture uses legacy conservative settings, not version-verified acceptance.",
            RuntimeWarning,
            stacklevel=2,
        )
        return None


def _prefix(package, name, prefix, source):
    if Path(prefix[0]).suffix.lower() in {".cmd", ".bat"}:
        if source is not None:
            path = Path(source) / "src" / package / "command.py"
            spec = importlib.util.spec_from_file_location("fixture_command_prefix", path)
            command = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(command)
        else:
            command = importlib.import_module(package + ".command")
        return command.command_prefix(prefix[0], name, "@openai/codex" if name == "codex" else "@anthropic-ai/claude-code")
    return prefix


def codex_settings(executable, source=None):
    policy = _policy("codexapter", source)
    settings = (
        policy.cross_protocol_overrides(_prefix("codexapter", "codex", [executable], source))
        if policy
        else [
            'model_reasoning_summary="none"',
            "features.multi_agent=false",
            'web_search="disabled"',
        ]
    )
    return {key: json.loads(value) for key, value in (item.split("=", 1) for item in settings)}


def claude_environment(prefix, source=None):
    policy = _policy("claudapter", source)
    return (
        policy.cross_protocol_environment(_prefix("claudapter", "claude", prefix, source))
        if policy
        else {
            "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
            "CLAUDE_CODE_DISABLE_STRUCTURED_OUTPUTS": "1",
            "DISABLE_PROMPT_CACHING": "1",
            "ENABLE_TOOL_SEARCH": "false",
        }
    )
