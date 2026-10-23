import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def probe_module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("probe_clients")


def test_codex_probe_requires_assistant_answer_not_tool_output(probe_module):
    stdout = "\n".join(json.dumps(item) for item in [
        {"type": "item.completed", "item": {"type": "command_execution", "exit_code": 0, "aggregated_output": "secret-nonce"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "actual-answer"}},
    ])
    assert probe_module._completed_answer("codex", stdout) == ("actual-answer", True)


@pytest.mark.parametrize("extra", [{"num_turns": 1}, {"is_error": True}, {"permission_denials": ["Read"]}])
def test_claude_probe_does_not_count_failed_or_absent_tool_execution(probe_module, extra):
    payload = {"result": "marker", "num_turns": 3, "is_error": False, "permission_denials": [], **extra}
    assert probe_module._completed_answer("claude", json.dumps(payload)) == ("marker", False)
