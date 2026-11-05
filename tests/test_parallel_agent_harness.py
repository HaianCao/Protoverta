import importlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from protoverta.core import Protocol
from protoverta.sse import SSEParser
from protoverta.streaming import StreamDecoder


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("smoke_parallel_agents")


def lifecycle(harness, *, error=False, swapped=False):
    items = []
    for index, task in enumerate(harness.TASKS):
        items.append({"tool": "spawn_agent", "status": "completed", "prompt": task, "receiver_thread_ids": [f"child-{index}"]})
    states = {f"child-{index}": {"status": "completed", "message": harness.FINALS[1 - index if swapped else index]} for index in range(2)}
    if error:
        states["child-0"] = {"status": "errored", "message": "exceeded retry limit, last status: 429 Too Many Requests"}
    items.append({"tool": "wait", "status": "failed" if error else "completed", "agents_states": states})
    return "\n".join(json.dumps({"type": "item.completed", "item": {"type": "collab_tool_call", **item}}) for item in items)


@pytest.mark.parametrize("failure", [False, True])
def test_codex_acceptance_requires_correlated_lifecycle_with_expected_failure(harness, failure):
    assert harness.completed_lifecycle("codex", lifecycle(harness, error=failure), failure=failure)
    assert not harness.completed_lifecycle("codex", lifecycle(harness, error=not failure), failure=failure)
    assert not harness.completed_lifecycle("codex", lifecycle(harness, swapped=True), failure=failure)


@pytest.mark.parametrize("client", ["codex", "claude"])
@pytest.mark.parametrize("raw", ["P05_PARALLEL_PARENT_VERIFIED", "[]", "null", '{"item":null}', '{"is_error":false,"subagent_stats":null}'])
def test_text_or_malformed_output_is_not_parallel_agent_evidence(harness, client, raw):
    assert not harness.completed_lifecycle(client, raw)


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("denied", [False, True])
def test_claude_requires_two_spawned_and_expected_success_failure_counts(harness, failure, denied):
    raw = {
        "is_error": False,
        "permission_denials": ["denied"] if denied else [],
        "subagent_stats": {"spawned": 2, "completed": 1 if failure else 2, "failed": 1 if failure else 0},
    }
    assert harness.completed_lifecycle("claude", json.dumps(raw), failure=failure) is not denied


@pytest.mark.parametrize("protocol", list(Protocol))
def test_batched_spawn_wire_keeps_two_distinct_call_ids_and_arguments(harness, protocol):
    calls = [({"name": "Agent"}, {"prompt": task}, f"spawn_{index}") for index, task in enumerate(harness.TASKS)]
    raw = harness.calls_wire(protocol, "synthetic", calls).read()
    decoder = StreamDecoder(protocol)
    deltas = [delta for event in SSEParser().feed(raw) for delta in decoder.feed(event)]
    decoder.finish()
    assert [delta.call_id for delta in deltas if delta.kind == "tool_start"] == ["spawn_0", "spawn_1"]
    arguments = {index: "".join(delta.text for delta in deltas if delta.kind == "tool_delta" and delta.index == index) for index in range(2)}
    assert [json.loads(arguments[index])["prompt"] for index in range(2)] == list(harness.TASKS)


def test_native_response_batch_preserves_each_tool_namespace(harness):
    calls = [({"name": "work", "namespace": f"space_{index}"}, {}, f"call_{index}") for index in range(2)]
    raw = harness.calls_wire(Protocol.RESPONSES, "synthetic", calls).read()
    events = [json.loads(event.data) for event in SSEParser().feed(raw)]
    items = [event["item"] for event in events if event.get("type") == "response.output_item.added"]
    assert [(item["call_id"], item["namespace"]) for item in items] == [("call_0", "space_0"), ("call_1", "space_1")]


def test_single_child_driver_flag_alone_cannot_pass_parallel_gate(harness, monkeypatch):
    monkeypatch.setattr(harness.single, "run", lambda *args, **kwargs: {"passed": True, "cli_version": "fixture"})
    result = harness.run("codex", Protocol.CHAT, Path("unused"))
    assert not result["passed"] and not result["lifecycle_verified"]


@pytest.mark.parametrize("field", ["completed", "failed"])
def test_boolean_counters_are_not_valid_lifecycle_evidence(harness, field):
    stats = {"spawned": 2, "completed": 1, "failed": 1, field: True}
    result = {"is_error": False, "permission_denials": [], "subagent_stats": stats}
    assert not harness.completed_lifecycle("claude", json.dumps(result), failure=True)


def test_duplicate_or_malformed_spawn_identity_never_passes(harness):
    raw = lifecycle(harness)
    assert not harness.completed_lifecycle("codex", raw.replace('"child-1"', '"child-0"'))
    assert not harness.completed_lifecycle("codex", raw.replace('["child-0"]', "null"))


@pytest.mark.parametrize("expected", [False, True])
def test_fixture_rejections_are_explicit_nonretryable_and_do_not_echo_exception_text(harness, expected):
    scenario = harness.Scenario("codex", Protocol.CHAT)

    def reject(*args, **kwargs):
        raise harness.ExpectedRejection if expected else ValueError("private synthetic detail")

    scenario.open = reject
    with harness.fixture_server(scenario) as origin:
        request = Request(origin + "/v1/chat/completions", data=b"{}", headers={"Authorization": "Bearer fake-upstream-key"})
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=2)
        with error.value as response:
            assert response.code == (429 if expected else 422)
            assert response.headers["x-should-retry"] == "false"
            assert b"private synthetic" not in response.read()
    assert scenario.rejections == int(expected)
    assert scenario.errors == ([] if expected else ["ValueError"])
