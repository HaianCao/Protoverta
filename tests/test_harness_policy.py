import importlib
import json
import types
from pathlib import Path

import harness_policy
import pytest


def test_fixture_codex_settings_are_launcher_decisions(monkeypatch):
    decisions = ['features.multi_agent=false', 'agents.enabled=false', 'web_search="disabled"']
    policy = types.SimpleNamespace(cross_protocol_overrides=lambda prefix: decisions if prefix == ["fixture"] else [])
    monkeypatch.setattr(harness_policy, "_policy", lambda package, source: policy)
    assert harness_policy.codex_settings("fixture") == {key: json.loads(value) for key, value in (item.split("=", 1) for item in decisions)}


def test_fixture_claude_environment_uses_launcher_decisions(monkeypatch):
    policy = types.SimpleNamespace(cross_protocol_environment=lambda prefix: {"verified": prefix[0]})
    monkeypatch.setattr(harness_policy, "_policy", lambda package, source: policy)
    assert harness_policy.claude_environment(["fixture.exe"]) == {"verified": "fixture.exe"}


def test_source_policy_is_loaded_from_selected_checkout(tmp_path):
    path = tmp_path / "src" / "codex_adapter" / "harness.py"
    path.parent.mkdir(parents=True)
    path.write_text('def cross_protocol_overrides(prefix):\n    return [\'agents.enabled=false\']\n', encoding="utf-8")
    assert harness_policy.codex_settings("fixture", tmp_path) == {"agents.enabled": False}


def test_missing_launcher_explicitly_marks_bare_fixture_unverified(monkeypatch):
    def missing(name):
        raise ModuleNotFoundError(name=name)
    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.warns(RuntimeWarning, match="not version-verified"):
        assert harness_policy.codex_settings("fixture")["features.multi_agent"] is False


def test_broken_launcher_dependency_is_not_silently_ignored(monkeypatch):
    def broken(name):
        raise ModuleNotFoundError(name="unexpected_dependency")
    monkeypatch.setattr(importlib, "import_module", broken)
    with pytest.raises(ModuleNotFoundError):
        harness_policy.codex_settings("fixture")


def test_claude_batch_probe_resolves_npm_prefix(tmp_path):
    directory = tmp_path / "src" / "claudapter"
    directory.mkdir(parents=True)
    (directory / "harness.py").write_text('def cross_protocol_environment(prefix):\n    assert prefix == ["node.exe", "cli.js"]\n    return {"fixture": "ok"}\n', encoding="utf-8")
    (directory / "command.py").write_text('def command_prefix(*args):\n    return ["node.exe", "cli.js"]\n', encoding="utf-8")
    assert harness_policy.claude_environment([str(Path("claude.cmd"))], tmp_path) == {"fixture": "ok"}
