"""Remaining P05 gates: real cancellation accounting and fixture verifiers."""

import importlib
import io
import json
import socket
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_request_isolation import (
    assert_complete,
    collect,
    connect,
    through_marker,
    upstream,
)

from protoverta.admission import AdmissionDenied
from protoverta.capabilities import _prefix
from protoverta.context import TokenCount
from protoverta.core import Protocol
from protoverta.server import GatewayConfig, gateway_session
from protoverta.token_quota import SharedTokenQuota


@pytest.mark.parametrize("source", list(Protocol))
@pytest.mark.parametrize("target", [Protocol.RESPONSES, Protocol.MESSAGES])
def test_cancel_retains_unknown_token_charge_releases_only_its_concurrency_and_keeps_rate_usage(
    tmp_path, source, target
):
    database = tmp_path / "quota.sqlite"
    observations = []
    clients = []
    with (
        upstream(source, failure="cancel") as (base, release, cancelled, records, active, _),
        gateway_session(
            GatewayConfig(
                source,
                base,
                "synthetic",
                "local",
                messages_usage_policy="deferred",
                shared_admission_db=str(database),
                shared_requests=4,
                shared_concurrency=3,
                shared_token_limit=100,
                token_counter=lambda *_: TokenCount(10, exact=True),
            ),
            observer=observations.append,
        ) as origin,
        ThreadPoolExecutor(3) as pool,
    ):
        try:
            clients = list(pool.map(lambda model: connect(origin, target, model), ["a", "b", "parent"]))
            for _, response, _ in clients:
                through_marker(response)
            peer = clients[0][2]
            peer.shutdown(socket.SHUT_RDWR)
            clients[0][1].close()
            clients[0][0].close()
            assert cancelled.wait(1)
            quota = SharedTokenQuota(database, _prefix(base) + "\0synthetic", "a", limit=100)
            deadline = time.monotonic() + 2
            while True:
                with sqlite3.connect(database) as db:
                    cancelled_token = db.execute(
                        "SELECT amount,active_until FROM token_reservations WHERE scope=?", (quota.storage.scope,)
                    ).fetchall()
                    active_leases = db.execute(
                        "SELECT COUNT(*) FROM attempts WHERE active_until>?", (time.time(),)
                    ).fetchone()[0]
                if cancelled_token == [(42, 0.0)] and active_leases == 2:
                    break
                assert time.monotonic() < deadline, (cancelled_token, active_leases)
                time.sleep(0.01)
            assert {"b", "parent"} <= active
            with pytest.raises(AdmissionDenied):
                quota.reserve(59)  # Unknown cancelled usage remains 10 input + 32 output.
            assert collect(origin, target, "probe")[0] == 200  # One freed concurrency slot.
            release.set()
            for _, response, _ in clients[1:]:
                assert_complete(response.read(), target)
            assert collect(origin, target, "over-quota")[0] == 429
            assert len(records) == 4  # Cancellation never refunds a dispatched rate request.
            assert any(item.outcome == "client_disconnected" for item in observations)
        finally:
            release.set()
            for connection, response, _ in clients:
                response.close()
                connection.close()


@pytest.fixture
def scripts(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("smoke_three_streams"), importlib.import_module("smoke_two_launchers")


@pytest.mark.parametrize("fault", [None, "missing_parent", "duplicate_lane", "only_one_frame", "serial_lanes"])
def test_three_stream_gate_requires_all_three_lanes_in_every_frame_round(scripts, fault):
    three, _ = scripts
    frames = [{"frame": index, "lane": role} for index in range(3) for role in ("parent", "a", "b")]
    if fault == "missing_parent":
        frames = [frame for frame in frames if frame["lane"] != "parent"]
    elif fault == "duplicate_lane":
        frames[0]["lane"] = "a"
    elif fault == "only_one_frame":
        frames = frames[:3]
    elif fault == "serial_lanes":
        frames.sort(key=lambda frame: frame["lane"])
    assert three.verified_frames(frames) is (fault is None)


@pytest.mark.parametrize("fault", [None, "wrong_cwd", "wrong_read", "main_modified", "one_request"])
def test_worktree_gate_requires_relative_read_correct_cwd_and_unchanged_main(scripts, fault):
    _, two = scripts
    flags = [
        "distinct_gateways",
        "cross_tokens_rejected",
        "first_completed",
        "first_gateway_closed",
        "second_survived",
        "second_completed",
        "second_gateway_closed",
        "diagnostics_isolated",
        "worktree_reads_verified",
        "worktree_cwd_verified",
        "main_checkout_unchanged",
    ]
    result = {
        **dict.fromkeys(flags, True),
        "worktrees": True,
        "upstream_counts": [2, 2],
        "fixture_errors": [[], []],
        "errors": [],
    }
    if fault == "wrong_cwd":
        result["worktree_cwd_verified"] = False
    elif fault == "wrong_read":
        result["worktree_reads_verified"] = False
    elif fault == "main_modified":
        result["main_checkout_unchanged"] = False
    elif fault == "one_request":
        result["upstream_counts"] = [1, 1]
    assert two.accepted(result) is (fault is None)


@pytest.mark.parametrize("lane,expected", [("parent", "own-parent"), ("a", "own-a"), ("b", "own-b"), ("unknown", None)])
def test_readonly_mcp_values_correlate_lane_arguments_without_tool_id_global_state(
    scripts, monkeypatch, lane, expected
):
    module = importlib.import_module("smoke_tool_search")
    request = {"id": 1, "method": "tools/call", "params": {"name": module.LOOKUP, "arguments": {"lane": lane}}}
    output = io.StringIO()
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(json.dumps(request) + "\n"))
    monkeypatch.setattr(module.sys, "stdout", output)
    module.serve_mcp(lane_values={key: "own-" + key for key in ("parent", "a", "b")})
    result = json.loads(output.getvalue())["result"]
    assert result["isError"] is (expected is None)
    if expected:
        assert result["content"][0]["text"] == expected


@pytest.mark.parametrize("fault", [None, "boolean_count", "missing_stats", "denied"])
def test_native_worktree_lifecycle_requires_real_completed_child(scripts, fault):
    module = importlib.import_module("smoke_agent_worktree")
    value = {"is_error": False, "permission_denials": [], "subagent_stats": {"spawned": 1, "completed": 1, "failed": 0}}
    if fault == "boolean_count":
        value["subagent_stats"]["completed"] = True
    elif fault == "missing_stats":
        value["subagent_stats"] = {}
    elif fault == "denied":
        value["permission_denials"] = ["denied"]
    assert module.verified_lifecycle(json.dumps(value)) is (fault is None)


def test_unknown_agent_isolation_fails_before_native_cli_discovery(scripts, monkeypatch):
    module = importlib.import_module("smoke_subagents")
    monkeypatch.setattr(module.shutil, "which", lambda _: pytest.fail("must reject invalid fixture before probing CLI"))
    with pytest.raises(ValueError, match="Fixture isolation"):
        module.run("claude", Protocol.CHAT, claude_isolation="unknown")
