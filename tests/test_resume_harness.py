import importlib
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from protoverta.server import GatewayConfig
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("smoke_clients")


@pytest.mark.parametrize("client", ["codex", "claude"])
@pytest.mark.parametrize("persist", [False, True])
@pytest.mark.parametrize("agent_policy", [None, "inherit", "selected"])
def test_harness_preserves_opt_in_session_and_resume_arguments(
    harness, monkeypatch, tmp_path, client, persist, agent_policy
):
    # This test supplies a fake checkout and executable to inspect argv only.
    # Version discovery/source policy loading has its own real-fixture tests.
    monkeypatch.setattr(harness, "codex_settings", lambda *_: {"features.multi_agent": False})
    monkeypatch.setattr(harness, "claude_environment", lambda *_: {"ENABLE_TOOL_SEARCH": "false"})
    captured = {}

    def run(args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(harness, "run_process", run)
    config = GatewayConfig("chat", "http://127.0.0.1:1234/v1", "fake-upstream-key", "local")
    identifier = "00000000-0000-4000-8000-000000000001"
    harness.run_client(
        client,
        client + ".exe",
        "http://127.0.0.1:4321",
        "http://127.0.0.1:1234",
        config,
        {},
        str(tmp_path),
        False,
        tmp_path,
        False,
        harness.Protocol.CHAT,
        10,
        "test-model",
        persist_session=persist,
        resume_id=identifier if persist else None,
        provider_id="alternate",
        agent_model_policy=agent_policy,
    )
    args = captured["args"]
    raw = tomllib.loads((tmp_path / "adapter.toml").read_text(encoding="utf-8"))
    provider = raw["providers"]["alternate"] if client == "codex" else raw["providers"][0]
    assert provider.get("agent_models") == ({"policy": agent_policy} if agent_policy else None)
    assert args[args.index("-p") + 1] == "alternate"
    if client == "codex":
        assert "alternate" in raw["providers"]
        root = args[args.index("--") + 1 :]
        assert root.pop(0) == "--no-daemon"
        assert root[:4] == [
            "-c",
            "model_providers.alternate.request_max_retries=0",
            "-c",
            "model_providers.alternate.stream_max_retries=0",
        ]
        assert root[4:6] == ["-c", "features.daemon_auto_start=false"]
        if persist:
            at = root.index("--sandbox")
            assert root[at : at + 5] == ["--sandbox", "read-only", "exec", "resume", identifier]
            assert "run" in args
        assert ("--ephemeral" not in args) == persist
    else:
        assert raw["providers"][0]["id"] == "alternate"
        assert captured["kwargs"]["env"]["ALTERNATE_TOKEN"] == "fake-upstream-key"
        assert "SMOKE_TOKEN" not in captured["kwargs"]["env"]
        assert ("--no-session-persistence" not in args) == persist
        if persist:
            assert args[args.index("--resume") + 1] == identifier


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_resume_id_is_extracted_from_cli_output(monkeypatch, client):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("smoke_resume")
    output = {"type": "thread.started", "thread_id": "session-1"} if client == "codex" else {"session_id": "session-1"}
    assert module.session_id(client, json.dumps(output)) == "session-1"


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
def test_resume_tool_fixture_is_a_valid_stream(monkeypatch, protocol):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("smoke_resume")
    encoder = StreamEncoder(protocol)
    events = []
    for delta in [
        Delta("start", model="synthetic", input_tokens=1, output_tokens=0),
        *module.tool_deltas("codex", "fixture.txt"),
    ]:
        events.extend(encoder.feed(delta))
    encoder.finish()
    assert "resume_call" in str(events)


def test_trace_records_only_shapes_not_instruction_contents(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("trace_adapter")
    result = module.summarize(
        {
            "model": "synthetic",
            "input": [{"role": "developer", "content": "<model_switch>private sentinel</model_switch>"}],
            "tools": [
                {
                    "type": "namespace",
                    "name": "multi_agent_v1",
                    "tools": [{"type": "function", "parameters": {"secret": "private schema"}}],
                }
            ],
        }
    )
    assert result["developer_messages"] == [{"index": 0, "tags": ["model_switch"]}]
    assert result["tools"][0]["child_types"] == ["function"]
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize(
    "content", [None, "opaque", {"unexpected": "private"}, [{"type": "private-type", "text": "private"}]]
)
def test_trace_handles_opaque_or_malformed_shapes_without_content_leak(monkeypatch, content):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("trace_adapter")
    result = module.summarize(
        {
            "input": [
                {"type": "reasoning", "content": content, "encrypted_content": "private"},
                {"role": "developer", "content": None},
            ],
            "tools": None,
        }
    )
    assert result["public_reasoning_items"] == 0
    assert result["reasoning_shapes"][0]["opaque_present"] is True
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_resume_probe_uses_portable_initial_reasoning_policy(monkeypatch, tmp_path, client):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("smoke_resume")
    monkeypatch.setattr(module.shutil, "which", lambda _: "synthetic.exe")
    captured = []

    def fail_initial(*args, **kwargs):
        captured.append(kwargs)
        return SimpleNamespace(returncode=1, stdout="", stderr="synthetic failure")

    monkeypatch.setattr(module, "run_client", fail_initial)
    result = module.run_resume(client, module.Protocol.RESPONSES, module.Protocol.CHAT, tmp_path)
    assert result["stage"] == "initial" and not result["passed"]
    assert len(captured) == 1 and captured[0]["persist_session"]
    if client == "codex":
        assert captured[0]["codex_config"] == {"model_reasoning_summary": "none"}
    else:
        assert "codex_config" not in captured[0]
