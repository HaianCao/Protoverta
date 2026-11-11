import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from protoverta import (
    CapabilityRecord,
    CapabilityRegistry,
    ConversionError,
    Protocol,
    RouteCapabilityRecord,
    convert_response,
    route_policies,
)
from protoverta.response_context import restore_request_context
from protoverta.server import GatewayConfig


def load_script(monkeypatch, name: str):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module(name)


def test_p13_openresponses_missing_field_provenance_classification(monkeypatch):
    audit = load_script(monkeypatch, "check_openresponses")
    assert audit.classify_missing_field("truncation") == "gateway-owned"
    assert audit.classify_missing_field("store") == "gateway-owned"
    assert audit.classify_missing_field("background") == "gateway-owned"
    assert audit.classify_missing_field("previous_response_id") == "gateway-owned"
    assert audit.classify_missing_field("tools") == "request-declared"
    assert audit.classify_missing_field("tool_choice") == "request-declared"
    assert audit.classify_missing_field("parallel_tool_calls") == "request-declared"
    assert audit.classify_missing_field("temperature") == "request-declared"
    assert audit.classify_missing_field("top_p") == "request-declared"
    assert audit.classify_missing_field("text") == "request-declared"
    assert audit.classify_missing_field("usage") == "upstream-measured"
    assert audit.classify_missing_field("service_tier") == "upstream-measured"
    assert audit.classify_missing_field("presence_penalty") == "unavailable"
    assert audit.classify_missing_field("frequency_penalty") == "unavailable"
    assert audit.classify_missing_field("top_logprobs") == "unavailable"

    sample_reports = [
        {
            "upstream": source,
            "exit_code": 1,
            "results": [
                {
                    "id": case_id,
                    "status": "failed",
                    "errors": [
                        "truncation: Required",
                        "temperature: Required",
                        "service_tier: Required",
                        "presence_penalty: Required",
                        *(["usage: Invalid input"] if source == "messages" else []),
                    ],
                }
                for case_id in audit.CASES
            ],
        }
        for source in ("chat", "messages")
    ]
    summary = audit.summarize(sample_reports)
    assert summary["outcome"] == "FAILED_FULL_SCHEMA"
    assert summary["provenance_classes"] == {
        "gateway-owned": {"truncation": 12},
        "request-declared": {"temperature": 12},
        "upstream-measured": {"service_tier": 12, "usage": 6},
        "unavailable": {"presence_penalty": 12},
    }


def test_p13_request_context_restores_declared_controls_without_inventing_zero_or_defaults():
    # Omitted controls stay absent; no zero/default temperature, top_p, service_tier, or usage subtotal is fabricated
    chat_resp = {
        "id": "c1",
        "object": "chat.completion",
        "model": "m",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
    converted = convert_response(chat_resp, Protocol.CHAT, Protocol.RESPONSES)
    restored_minimal = restore_request_context(converted, {"model": "m", "input": "hi"})
    assert restored_minimal["store"] is False
    assert restored_minimal["background"] is False
    assert restored_minimal["previous_response_id"] is None
    for uninvented in (
        "temperature",
        "top_p",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "service_tier",
        "presence_penalty",
        "frequency_penalty",
        "top_logprobs",
    ):
        assert uninvented not in restored_minimal
    assert "output_tokens_details" not in restored_minimal["usage"]

    # Explicitly declared portable controls ARE restored
    explicit_req = {
        "model": "m",
        "input": "hi",
        "instructions": "Be concise",
        "max_output_tokens": 64,
        "temperature": 0.2,
        "top_p": 0.9,
        "parallel_tool_calls": False,
        "tool_choice": "none",
        "tools": [{"type": "function", "name": "f1"}],
    }
    restored_full = restore_request_context(converted, explicit_req)
    assert restored_full["instructions"] == "Be concise"
    assert restored_full["max_output_tokens"] == 64
    assert restored_full["temperature"] == 0.2
    assert restored_full["top_p"] == 0.9
    assert restored_full["parallel_tool_calls"] is False
    assert restored_full["tool_choice"] == "none"
    assert restored_full["tools"] == [
        {"type": "function", "name": "f1", "description": None, "parameters": None, "strict": None}
    ]


def test_p13_sdk_candidate_evaluation_mode_preserves_pinned_baseline_gate(monkeypatch):
    sdk_gate = load_script(monkeypatch, "check_sdk")
    monkeypatch.setattr(
        sdk_gate.importlib.metadata, "version", lambda name: {"openai": "99.0.0", "anthropic": "2.3.0"}[name]
    )

    # Default baseline gate fails closed (exit 2) when SDK version drifts from pinned baseline
    monkeypatch.setattr("sys.argv", ["check_sdk"])
    with pytest.raises(SystemExit) as exc_info:
        sdk_gate.main()
    assert exc_info.value.code == 2

    # Separate candidate evaluation job (--allow-candidate-sdk) can evaluate newer SDKs without losing baseline gate
    monkeypatch.setattr("sys.argv", ["check_sdk", "--allow-candidate-sdk"])

    def fake_run(command, *, cwd, env, check):
        Path(command[command.index("--junitxml") + 1]).write_text(
            "<testsuites><testsuite>"
            + "".join(f'<testcase name="candidate_{i}"/>' for i in range(29))
            + "</testsuite></testsuites>",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(sdk_gate.subprocess, "run", fake_run)
    assert sdk_gate.main() == 0


def test_p13_cli_matrix_and_keyless_snapshot_exclude_user_sessions(monkeypatch, tmp_path):
    snap_mod = load_script(monkeypatch, "compatibility_snapshot")
    # Standalone core CI has no sibling launcher checkouts. Use a synthetic
    # three-project fixture rather than depending on the developer workspace.
    for name in ("protoverta", "free-codex-cli", "free-claude-cli"):
        project = tmp_path / name
        (project / "src").mkdir(parents=True)
        (project / "pyproject.toml").write_text(f'[project]\nname="{name}"\nversion="0.0.0"\n', encoding="utf-8")
        (project / "src/fixture.py").write_text("SYNTHETIC = True\n", encoding="utf-8")
    monkeypatch.setattr(
        snap_mod.subprocess,
        "check_output",
        lambda command, **options: "0" * 40 if command[-2:] == ["rev-parse", "HEAD"] else "",
    )
    data = snap_mod.snapshot(tmp_path)
    assert data["provider_certification"] is False
    assert "cli_version_matrix" in data
    assert "0.164.0" in data["cli_version_matrix"]["codex"]["accepted_baselines"]
    assert "0.166.0" in data["cli_version_matrix"]["codex"]["evaluated_candidates"]
    assert "2.1.308" in data["cli_version_matrix"]["claude"]["accepted_baselines"]
    assert data["pinned_sdk_baseline"] == {"openai": "3.37.0", "anthropic": "2.3.0"}
    # Ensure no user session paths, API keys, or prompts appear in snapshot
    serialized = str(data)
    for forbidden in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", ".claude", ".codex"):
        assert forbidden not in serialized


def test_p13_feature_gating_by_route_not_coverage():
    # High code coverage or model-level text support never substitutes for route-level feature acceptance
    base_cfg = GatewayConfig("chat", "https://example.test/v1", "k", "t", client_profile="codex")
    policies = route_policies(base_cfg)
    reg = CapabilityRegistry(
        records=(
            CapabilityRecord(
                "https://example.test/v1",
                "chat",
                "m",
                {"text": True, "streaming": True, "tools": True},
                "2026-11-04",
                "model record",
            ),
        ),
        routes=(
            RouteCapabilityRecord(
                base_url="https://example.test/v1",
                protocol="chat",
                model="m",
                source_protocol="responses",
                streaming=True,
                client_profile="codex",
                policies=policies,
                features={"text": True, "streaming": True},
                checked_at="2026-11-04",
                evidence="text streaming route only",
                verified_with="codex-cli 0.166.0",
            ),
        ),
    )
    cfg = GatewayConfig(
        "chat",
        "https://example.test/v1",
        "k",
        "t",
        client_profile="codex",
        capabilities=reg,
        require_verified_routes=True,
    )
    assert route_policies(cfg) == policies
    # Text+streaming passes on this exact route
    reg.assess_route(
        "https://example.test/v1",
        "chat",
        "m",
        {"text", "streaming"},
        source_protocol="responses",
        streaming=True,
        client_profile="codex",
        policies=policies,
    ).require(verified=True)

    # Requesting tools on the same route fails preflight because route evidence only certified text+streaming
    with pytest.raises(ConversionError, match="unverified=tools"):
        reg.assess_route(
            "https://example.test/v1",
            "chat",
            "m",
            {"text", "streaming", "tools"},
            source_protocol="responses",
            streaming=True,
            client_profile="codex",
            policies=policies,
        ).require(verified=True)
