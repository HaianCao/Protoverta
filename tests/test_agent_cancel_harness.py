import importlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from protoverta.core import Protocol


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("smoke_agent_cancel")


@pytest.mark.parametrize(
    "value", ['{"agent_id":"child-a"}', "agentId: child-a (internal metadata)", [{"type": "text", "text": "agentId: child-a\noutput_file: C:/synthetic"}]]
)
def test_cancel_target_comes_from_actual_spawn_metadata(harness, value):
    assert harness.task_identifier(value) == "child-a"


@pytest.mark.parametrize("value", [None, "no identifier", "output_file: C:/synthetic/child-a.output"])
def test_missing_task_identity_is_not_guessed(harness, value):
    with pytest.raises(ValueError, match="actual background"):
        harness.task_identifier(value)


@pytest.mark.parametrize("role", ["user", "system", "developer", "assistant", "tool"])
@pytest.mark.parametrize("content_type", ["text", "input_text"])
def test_background_notification_requires_non_assistant_message_with_matching_task(harness, role, content_type):
    text = "<task-notification><task-id>child-b</task-id><result>verified-b</result></task-notification>"
    body = {"messages": [{"role": role, "content": [{"type": content_type, "text": text}]}]}
    seen, _ = harness.notification_evidence(body, "child-b", "verified-b")
    assert seen is (role in {"user", "system", "developer"})
    assert not harness.notification_evidence(body, "other-child", "verified-b")[0]


@pytest.mark.parametrize("field", ["system", "instructions"])
def test_top_level_instruction_notification_is_recognized_without_rewriting_it(harness, field):
    body = {field: "<task-notification>child-b verified-b</task-notification>"}
    assert harness.notification_evidence(body, "child-b", "verified-b")[0]
    assert not harness.notification_evidence({field: "child-b verified-b"}, "child-b", "verified-b")[0]


def scenario():
    return SimpleNamespace(
        cancel_target="child-a",
        sibling_target="child-b",
        notification_seen=True,
        parent_results=[{"cancel_a": json.dumps({"task_id": "child-a", "task_type": "local_agent", "message": "Successfully stopped task: child-a"})}],
    )


def claude_result():
    return {
        "is_error": False,
        "permission_denials": [],
        "subagent_stats": {"spawned": 2, "completed": 1, "failed": 0, "started_in_background": 2, "killed": {"parent": 1, "user": 0, "system": 0}},
    }


@pytest.mark.parametrize("fault", [None, "missing_notification", "wrong_target", "no_stop", "failed_sibling", "boolean_kill"])
def test_claude_cancellation_gate_requires_real_stop_notification_and_sibling_completion(harness, fault):
    state, output = scenario(), claude_result()
    if fault == "missing_notification":
        state.notification_seen = False
    elif fault == "wrong_target":
        state.cancel_target = "child-b"
    elif fault == "no_stop":
        state.parent_results = []
    elif fault == "failed_sibling":
        output["subagent_stats"]["failed"] = 1
    elif fault == "boolean_kill":
        output["subagent_stats"]["killed"]["parent"] = True
    assert harness.verified_lifecycle("claude", json.dumps(output), state) is (fault is None)


@pytest.mark.parametrize("fault", [None, "wrong_stop", "wrong_wait", "extra_spawn"])
def test_codex_cancellation_gate_correlates_task_close_and_successful_sibling(harness, fault):
    tasks = harness.parallel.TASKS
    items = [{"tool": "spawn_agent", "prompt": tasks[index], "receiver_thread_ids": [f"child-{label}"]} for index, label in enumerate(("a", "b"))]
    items += [
        {"tool": "close_agent", "receiver_thread_ids": ["child-b" if fault == "wrong_stop" else "child-a"]},
        {"tool": "wait", "agents_states": {"child-a" if fault == "wrong_wait" else "child-b": {"status": "completed", "message": harness.parallel.FINALS[1]}}},
    ]
    if fault == "extra_spawn":
        items.append({"tool": "spawn_agent", "prompt": "unknown", "receiver_thread_ids": ["extra"]})
    raw = "\n".join(json.dumps({"type": "item.completed", "item": {"type": "collab_tool_call", "status": "completed", **item}}) for item in items)
    assert harness.verified_lifecycle("codex", raw, scenario()) is (fault is None)


def test_fixture_redirects_native_temp_artifacts_under_its_owned_root(harness, monkeypatch):
    captured = {}
    single = harness.single
    monkeypatch.setattr(single.shutil, "which", lambda _: "fixture-claude.exe")
    monkeypatch.setattr(single.smoke_clients, "run_process", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "2.1.308 (Claude Code)", ""))

    def client(*args, **kwargs):
        captured.update(args[5])
        return subprocess.CompletedProcess([], 1, "", "")

    monkeypatch.setattr(single.smoke_clients, "run_client", client)
    single.run("claude", Protocol.CHAT)
    assert Path(captured["TEMP"]).parent == Path(captured["HOME"]).parent
    assert captured["TMP"] == captured["TEMP"]
    assert not Path(captured["TEMP"]).exists()


@pytest.mark.parametrize("depth", [True, 0, 4, "2"])
def test_invalid_fixture_depth_fails_before_cli_discovery(harness, monkeypatch, depth):
    monkeypatch.setattr(harness.single.shutil, "which", lambda _: pytest.fail("must not probe invalid fixture"))
    with pytest.raises(ValueError, match="Fixture Claude depth"):
        harness.single.run("claude", Protocol.CHAT, claude_depth=depth)
