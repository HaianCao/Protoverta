import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from protoverta.core import Protocol
from protoverta.namespaces import NamespaceMap


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("smoke_subagents")


@pytest.mark.parametrize("protocol", list(Protocol))
def test_child_role_marker_is_not_inherited_from_tool_result_text(harness, protocol):
    user = {"role": "user", "content": [{"type": "text", "text": "parent task"}]}
    if protocol == Protocol.RESPONSES:
        body = {"input": [user, {"type": "function_call_output", "call_id": "spawn", "output": harness.TASK}]}
    elif protocol == Protocol.CHAT:
        body = {"messages": [user, {"role": "tool", "content": harness.TASK}]}
    else:
        body = {
            "messages": [
                user,
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "spawn", "content": harness.TASK}]},
            ]
        }
    assert harness.TASK not in harness.user_text(body, protocol)


def test_namespaced_spawn_alias_matches_only_the_advertised_tool(harness):
    alias = NamespaceMap().alias("multi_agent_v1", "spawn_agent")
    tool = {"name": alias}
    assert harness.find_tool([{"name": "evil_spawn_agent"}, tool], "spawn_agent") is tool
    with pytest.raises(StopIteration):
        harness.find_tool([{"name": "evil_spawn_agent"}], "spawn_agent")


@pytest.mark.parametrize("protocol", list(Protocol))
def test_tool_result_keeps_its_call_id_and_messages_errors_are_not_success(harness, protocol):
    if protocol == Protocol.RESPONSES:
        body = {"input": [{"type": "function_call_output", "call_id": "p04_read", "output": "value"}]}
    elif protocol == Protocol.CHAT:
        body = {"messages": [{"role": "tool", "tool_call_id": "p04_read", "content": "value"}]}
    else:
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "p04_read", "content": "value"},
                        {"type": "tool_result", "tool_use_id": "denied", "content": "error", "is_error": True},
                    ],
                }
            ]
        }
    assert harness.tool_records(body, protocol) == [("p04_read", "value")]


def test_mcp_fixture_reads_only_the_fixed_file_and_audits_only_real_calls(harness, tmp_path):
    fixture, audit = tmp_path / "fixture.txt", tmp_path / "audit.txt"
    fixture.write_text("fixed-test-value", encoding="utf-8")
    requests = [
        {"id": 1, "method": "tools/call", "params": {"name": "not-p04"}},
        {"id": 2, "method": "tools/call", "params": {"name": "p04_read_fixture", "arguments": {}}},
    ]
    result = subprocess.run(
        [sys.executable, harness.__file__, "--mcp", "--fixture-file", str(fixture), "--audit-file", str(audit)],
        input="\n".join(map(json.dumps, requests)) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    # Surface the fixture's own error output; CI hides captured streams.
    assert result.returncode == 0, result.stderr[-3000:]
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert responses[0]["error"]["code"] == -32601
    assert responses[1]["result"] == {"content": [{"type": "text", "text": "fixed-test-value"}], "isError": False}
    assert audit.read_text(encoding="utf-8") == "read\n"


@pytest.mark.parametrize(
    "stdout",
    ["not json", "[]", '{"item":null}', '{"result":"P04_PARENT_VERIFIED"}', '{"is_error":false,"subagent_stats":null}'],
)
@pytest.mark.parametrize("client", ["codex", "claude"])
def test_claimed_completion_without_real_lifecycle_never_passes(harness, client, stdout):
    assert not harness.lifecycle_verified(client, stdout)


@pytest.mark.parametrize("failed,denied", [(0, False), (1, False), (0, True)])
def test_claude_requires_exactly_one_completed_child_and_no_permission_denials(harness, failed, denied):
    result = {
        "is_error": False,
        "permission_denials": ["fixture denied"] if denied else [],
        "subagent_stats": {"spawned": 1, "completed": 1, "failed": failed},
    }
    assert harness.lifecycle_verified("claude", json.dumps(result)) is (failed == 0 and not denied)


@pytest.mark.parametrize("status,child", [("completed", "child"), ("errored", "child"), ("completed", "other")])
def test_codex_wait_must_confirm_the_actual_spawned_child(harness, status, child):
    events = [
        {
            "type": "item.completed",
            "item": {
                "type": "collab_tool_call",
                "status": "completed",
                "tool": "spawn_agent",
                "receiver_thread_ids": ["child"],
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "collab_tool_call",
                "status": "completed",
                "tool": "wait",
                "agents_states": {child: {"status": status, "message": harness.CHILD_DONE}},
            },
        },
    ]
    assert harness.lifecycle_verified("codex", "\n".join(map(json.dumps, events))) is (
        status == "completed" and child == "child"
    )
