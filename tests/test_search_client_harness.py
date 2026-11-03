import importlib
import io
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from protoverta.core import Protocol
from protoverta.server import GatewayConfig


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("smoke_tool_search")


@pytest.mark.parametrize("adapter", [False, True])
def test_codex_fixture_settings_and_search_policy_reach_client(harness, monkeypatch, tmp_path, adapter):
    smoke = importlib.import_module("smoke_clients")
    captured = {}

    def run(args, **kwargs):
        captured.update(args=args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(smoke, "run_process", run)
    # This test supplies a synthetic adapter root, not a real source checkout.
    # Version-policy loading has its own tests. Bare mode forwards its result;
    # adapter mode lets the launcher apply the policy itself.
    def settings(executable, source=None):
        captured["policy"] = (executable, source)
        return {"features.multi_agent": False, "model_reasoning_summary": "none", "web_search": "disabled"}

    monkeypatch.setattr(smoke, "codex_settings", settings)
    config = GatewayConfig("chat", "http://127.0.0.1:1234/v1", "fake-upstream-key", "local", client_tool_search="function")
    smoke.run_client("codex", "codex.exe", "http://127.0.0.1:4321", config.upstream_base_url.removesuffix("/v1"), config, {}, str(tmp_path), False, tmp_path if adapter else None, False, Protocol.CHAT, 10, "fixture", codex_config={"model_catalog_json": "fixture-models.json"})
    args = captured["args"]
    assert captured["policy"] == ("codex.exe", tmp_path if adapter else None)
    if not adapter:
        assert "features.multi_agent=false" in args
    assert "features.daemon_auto_start=false" in args
    for setting in ("features.plugins=false", "features.apps=false", "features.remote_plugin=false", "check_for_update_on_startup=false"):
        assert setting in args
    assert 'model_catalog_json="fixture-models.json"' in args
    if adapter:
        raw = tomllib.loads((tmp_path / "adapter.toml").read_text(encoding="utf-8"))
        assert raw["providers"]["smoke"]["client_tool_search"] == "function"
        assert args.index("features.daemon_auto_start=false") < args.index("exec")


def test_local_smoke_blocks_remote_git_transport(harness, monkeypatch):
    smoke = importlib.import_module("smoke_clients")
    captured = {}

    def run(*args, **kwargs):
        captured.update(env=args[5])
        return SimpleNamespace(returncode=0, stdout=smoke.MARKER, stderr="")

    monkeypatch.setattr(smoke.shutil, "which", lambda _: "fixture-codex.exe")
    monkeypatch.setattr(smoke, "run_client", run)
    smoke.run_smoke("codex", Protocol.CHAT)
    assert captured["env"]["GIT_ALLOW_PROTOCOL"] == "file"
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_search_fixture_uses_only_advertised_query_schema(harness):
    schema = {"properties": {"query": {"type": "string"}, "limit": {"type": "number"}}, "required": ["query"]}
    assert harness.search_arguments(schema) == {"query": harness.LOOKUP, "limit": 1}
    with pytest.raises(ValueError, match="Unsupported required"):
        harness.search_arguments({"properties": {"unknown": {"type": "object"}}, "required": ["unknown"]})


def test_native_loaded_namespace_is_read_from_search_history(harness):
    body = {"input": [{"type": "tool_search_output", "tools": [{"type": "namespace", "name": "mcp__fixture", "tools": [{"type": "function", "name": "fixture_lookup"}]}]}]}
    assert list(harness.functions(body, Protocol.RESPONSES)) == [{"type": "function", "name": "fixture_lookup", "namespace": "mcp__fixture"}]


@pytest.mark.parametrize("error", [None, {"message": "fixture denied"}])
def test_actual_mcp_completion_required_for_search_pass(harness, error):
    event = {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "fixture", "tool": harness.LOOKUP, "status": "completed", "error": error, "result": {"content": [{"type": "text", "text": harness.VALUE}]}}}
    assert harness.lookup_completed(json.dumps(event)) is (error is None)
    assert not harness.lookup_completed(harness.MARKER)
    assert not harness.lookup_completed('[]\n{"type":"item.completed","item":null}')
    event["item"]["result"] = None
    assert not harness.lookup_completed(json.dumps(event))


def test_fixture_mcp_lists_and_calls_only_fixed_read_only_tool(harness):
    messages = [{"id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}, {"method": "notifications/initialized"}, {"id": 2, "method": "tools/list"}, {"id": 3, "method": "tools/call", "params": {"name": harness.LOOKUP, "arguments": {}}}, {"id": 4, "method": "tools/call", "params": {"name": "not_allowed"}}]
    result = subprocess.run([sys.executable, str(Path(harness.__file__)), "--mcp"], input="\n".join(json.dumps(item) for item in messages) + "\n", capture_output=True, text=True, encoding="utf-8", timeout=10, check=True)
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(responses) == 4
    assert responses[1]["result"]["tools"][0]["annotations"]["readOnlyHint"] is True
    assert responses[2]["result"] == {"content": [{"type": "text", "text": harness.VALUE}], "isError": False}
    assert responses[3]["error"]["code"] == -32601


@pytest.mark.parametrize("configured", [False, True])
def test_mcp_fixture_can_require_real_environment_expansion(harness, monkeypatch, configured):
    if configured:
        monkeypatch.setenv("PROTOVERTA_MCP_FIXTURE_TOKEN", "local-mcp-env")
    else:
        monkeypatch.delenv("PROTOVERTA_MCP_FIXTURE_TOKEN", raising=False)
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"id": 1, "method": "tools/call", "params": {"name": harness.LOOKUP}}) + "\n"))
    monkeypatch.setattr(sys, "stdout", output)
    harness.serve_mcp(verify_env=True)
    result = json.loads(output.getvalue())["result"]
    assert result["isError"] is (not configured)
    assert (result["content"][0]["text"] == harness.VALUE) is configured
