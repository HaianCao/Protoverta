import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("smoke_aux_models")


@pytest.mark.parametrize("fault", [None, "wrong_spawn", "wrong_send", "missing_resume", "wrong_history_result"])
def test_codex_resume_gate_requires_same_child_and_both_verified_turns(harness, fault):
    items = [
        {"tool": "spawn_agent", "receiver_thread_ids": ["other" if fault == "wrong_spawn" else "child"], "prompt": harness.TASK},
        {"tool": "send_input", "receiver_thread_ids": ["other" if fault == "wrong_send" else "child"], "prompt": harness.RESUME_TASK},
    ]
    for marker in (harness.FIRST, harness.RESUMED):
        if marker == harness.RESUMED and fault == "missing_resume":
            continue
        items.append({"tool": "wait", "agents_states": {"child": {"status": "completed", "message": "fake" if fault == "wrong_history_result" else marker}}})
    raw = "\n".join(json.dumps({"type": "item.completed", "item": {"type": "collab_tool_call", "status": "completed", **item}}) for item in items)
    assert harness.verified_lifecycle("codex", raw, "child") is (fault is None)


@pytest.mark.parametrize("fault", [None, "new_child", "boolean_count", "failed", "denied"])
def test_claude_continuation_reuses_one_background_agent(harness, fault):
    stats = {"spawned": 1, "completed": 1, "failed": 0, "started_in_background": 1}
    result = {"is_error": False, "permission_denials": [], "subagent_stats": stats}
    if fault == "new_child":
        stats["spawned"] = 2
    elif fault == "boolean_count":
        stats["completed"] = True
    elif fault == "failed":
        stats["failed"] = 1
    elif fault == "denied":
        result["permission_denials"] = ["denied"]
    assert harness.verified_lifecycle("claude", json.dumps(result), "child") is (fault is None)
