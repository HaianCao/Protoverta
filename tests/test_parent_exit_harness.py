import ctypes
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("smoke_parent_exit")


@pytest.mark.parametrize("mode", ["normal", "interrupt"])
@pytest.mark.parametrize("fault", [None, "after_cleanup", "members_remain", "timeout", "wrong_exit", "boolean_count", "not_owned", "no_signal"])
def test_cleanup_gate_needs_pre_job_close_evidence_and_real_signal(harness, mode, fault):
    report = {"timed_out": False, "remaining_before_cleanup": 0, "upstream_closed_before_cleanup": True,
              "returncode": 130 if mode == "interrupt" else 0, "event_sent": True, "console_owned": True}
    if fault == "after_cleanup":
        report["upstream_closed_before_cleanup"] = False
    elif fault == "members_remain":
        report["remaining_before_cleanup"] = 1
    elif fault == "timeout":
        report["timed_out"] = True
    elif fault == "wrong_exit":
        report["returncode"] = 1
    elif fault == "boolean_count":
        report["remaining_before_cleanup"] = False
    elif fault == "not_owned":
        report["console_owned"] = False
    elif fault == "no_signal":
        report["event_sent"] = False
    assert harness.verified_cleanup(report, mode) is (fault is None or mode == "normal" and fault in {"not_owned", "no_signal"})


@pytest.mark.parametrize("members,owned,expected", [({10, 20}, {20}, True), ({10, 20, 21}, {20, 21}, True),
                                                  ({10, 20, 99}, {20}, False), ({20}, {20}, False),
                                                  ({10}, {20}, False), ({10, 20}, {10, 20}, False)])
def test_console_event_is_never_sent_into_a_console_with_unowned_processes(harness, members, owned, expected):
    controller = importlib.import_module("probe_console")
    assert controller.console_is_owned(members, owned, 10, 20) is expected


@pytest.mark.parametrize("mode", ["normal", "interrupt"])
@pytest.mark.parametrize("fault", [None, "wrong_complete", "boolean_count", "missing_spawn", "denied"])
def test_claude_lifecycle_distinguishes_background_drain_from_interrupt(harness, mode, fault):
    result = {"is_error": mode == "interrupt", "terminal_reason": "aborted_streaming" if mode == "interrupt" else "end_turn",
              "permission_denials": [], "subagent_stats": {"spawned": 1, "started_in_background": 1,
                                                           "completed": 0 if mode == "interrupt" else 1, "failed": 0}}
    if fault == "wrong_complete":
        result["subagent_stats"]["completed"] = 1 if mode == "interrupt" else 0
    elif fault == "boolean_count":
        result["subagent_stats"]["spawned"] = True
    elif fault == "missing_spawn":
        result["subagent_stats"] = {}
    elif fault == "denied":
        result["permission_denials"] = ["denied"]
    assert harness.verified_lifecycle("claude", json.dumps(result), "child", mode) is (fault is None)


@pytest.mark.parametrize("identity", ["child", "other"])
def test_codex_actual_spawn_event_must_match_returned_child_id(harness, identity):
    result = {"type": "item.completed", "item": {"type": "collab_tool_call", "tool": "spawn_agent", "status": "completed",
                                                "prompt": harness.TASK, "receiver_thread_ids": [identity]}}
    assert harness.verified_lifecycle("codex", json.dumps(result), "child", "normal") is (identity == "child")
    assert not harness.verified_lifecycle("codex", "parent says it spawned", "child", "normal")


@pytest.mark.parametrize("truncated", [False, True])
def test_job_membership_never_treats_truncated_snapshot_as_empty(harness, truncated):
    module = importlib.import_module("windows_probe_job")
    job = module.WindowsProbeJob.__new__(module.WindowsProbeJob)

    def query(_handle, info_class, buffer, *_):
        assert info_class == 3
        ctypes.c_uint32.from_buffer(buffer, 0).value = 2 if truncated else 1
        ctypes.c_uint32.from_buffer(buffer, 4).value = 1
        ctypes.c_size_t.from_buffer(buffer, 8).value = 123
        return True

    job.handle = 1
    job.kernel = SimpleNamespace(QueryInformationJobObject=query)
    if truncated:
        with pytest.raises(RuntimeError, match="truncated"):
            job.process_ids()
    else:
        assert job.process_ids() == {123}
