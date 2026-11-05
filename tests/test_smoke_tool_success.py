import importlib
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "client,stdout,stderr,failed",
    [
        ("codex", "", "ERROR codex_core::tools::router: error=Goal tools require a persistent thread.", True),
        ("codex", "", "WARNING: metadata unavailable", False),
        ("claude", '{"is_error":false,"permission_denials":["Read"]}', "", True),
        ("claude", '{"is_error":false,"permission_denials":[]}', "", False),
    ],
)
def test_tool_error_results_are_not_successful_round_trips(monkeypatch, client, stdout, stderr, failed):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    harness = importlib.import_module("smoke_clients")
    assert harness.tool_execution_failed(client, stdout, stderr) is failed


@pytest.mark.parametrize("policy", ["selected", "inherit", "invalid"])
def test_agent_policy_requires_adapter_before_starting_client(monkeypatch, policy):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    harness = importlib.import_module("smoke_clients")
    monkeypatch.setattr(harness.shutil, "which", lambda *_: pytest.fail("must reject before CLI discovery"))
    with pytest.raises(ValueError, match="requires an adapter"):
        harness.run_smoke("codex", harness.Protocol.CHAT, agent_model_policy=policy)
