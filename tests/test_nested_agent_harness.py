import importlib
from pathlib import Path

import pytest


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("smoke_nested_agents")


@pytest.mark.parametrize("depth", [1, 2])
@pytest.mark.parametrize("fault", [None, "wrong_depth", "wrong_spawn_origin", "failed", "boolean_count", "denied"])
def test_nested_lifecycle_requires_depth_origin_and_completion_evidence(harness, depth, fault):
    output = {"is_error": False, "permission_denials": [], "subagent_stats": {
        "spawned": depth, "completed": depth, "failed": 0, "max_depth": depth, "spawned_by_subagents": depth - 1}}
    stats = output["subagent_stats"]
    if fault == "wrong_depth":
        stats["max_depth"] = depth + 1
    elif fault == "wrong_spawn_origin":
        stats["spawned_by_subagents"] = depth
    elif fault == "failed":
        stats["failed"] = 1
    elif fault == "boolean_count":
        stats["failed"] = False
    elif fault == "denied":
        output["permission_denials"] = ["denied"]
    assert harness.verified_lifecycle(output, depth) is (fault is None)


@pytest.mark.parametrize("output", [None, [], {"subagent_stats": None}, {"result": "nested succeeded"}])
def test_nested_text_claim_is_not_harness_evidence(harness, output):
    assert not harness.verified_lifecycle(output, 2)
