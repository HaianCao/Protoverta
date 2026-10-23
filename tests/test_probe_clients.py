import importlib
import json
from pathlib import Path
from types import SimpleNamespace

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


@pytest.mark.parametrize("client", ["codex", "claude"])
@pytest.mark.parametrize("mode", ["success", "wrong_nonce", "wrong_session"])
def test_live_resume_probe_requires_old_nonce_and_same_session(probe_module, monkeypatch, tmp_path, client, mode):
    calls = []
    monkeypatch.setattr(probe_module.shutil, "which", lambda name: name + ".exe")
    monkeypatch.setattr(probe_module.secrets, "token_hex", lambda size: "synthetic")

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        first = len(calls) == 1
        identifier = "session-1" if first or mode != "wrong_session" else "session-2"
        answer = "ACK" if first else "wrong" if mode == "wrong_nonce" else "RESUME_NONCE_synthetic"
        stdout = json.dumps({"session_id": identifier, "result": answer, "is_error": False, "num_turns": 1}) if client == "claude" else "\n".join(json.dumps(event) for event in [{"type": "thread.started", "thread_id": identifier}, {"type": "item.completed", "item": {"type": "agent_message", "text": answer}}])
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(probe_module, "run_client", run)
    result = probe_module.probe_resume(client, probe_module.Protocol.CHAT, probe_module.Protocol.MESSAGES, "https://example.test/v1", "fake-key", "selected-model", tmp_path)
    assert result["passed"] == (mode == "success")
    assert len(calls) == 2
    assert "RESUME_NONCE_synthetic" in calls[0][1]["prompt"]
    assert "RESUME_NONCE_synthetic" not in calls[1][1]["prompt"]
    assert calls[1][1]["resume_id"] == "session-1"
    assert calls[1][1]["provider_id"] == "alternate"
    assert "fake-key" not in json.dumps(result)
