import importlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from protoverta.core import Protocol
from protoverta.server import GatewayConfig, gateway_session


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("smoke_two_launchers")


def good_result():
    flags = ["distinct_gateways", "cross_tokens_rejected", "first_completed", "first_gateway_closed", "second_survived", "second_completed", "second_gateway_closed", "diagnostics_isolated"]
    return {**dict.fromkeys(flags, True), "upstream_counts": [1, 1], "fixture_errors": [[], []], "errors": []}


@pytest.mark.parametrize("fault", [None, "distinct_gateways", "cross_tokens_rejected", "first_completed", "first_gateway_closed", "second_survived", "second_completed", "second_gateway_closed", "diagnostics_isolated", "extra_dispatch", "boolean_count", "fixture_error", "controller_error", "truthy_flag"])
def test_two_launcher_gate_cannot_pass_from_output_markers_or_partial_isolation(harness, fault):
    result = good_result()
    if fault in result and isinstance(result[fault], bool):
        result[fault] = False
    elif fault == "extra_dispatch":
        result["upstream_counts"] = [1, 2]
    elif fault == "boolean_count":
        result["upstream_counts"] = [True, True]
    elif fault == "fixture_error":
        result["fixture_errors"] = [["ValueError"], []]
    elif fault == "controller_error":
        result["errors"] = ["TimeoutError"]
    elif fault == "truthy_flag":
        result["distinct_gateways"] = 1
    assert harness.accepted(result) is (fault is None)
    assert not harness.accepted({"passed": True})


def test_worker_homes_temp_and_secrets_are_process_local(harness, monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_SENTINEL", "must-not-be-inherited")
    first = harness.environment(tmp_path / "a")
    second = harness.environment(tmp_path / "b")
    for name in ("HOME", "APPDATA", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "TEMP", "TMP"):
        assert Path(first[name]).is_relative_to(tmp_path / "a")
        assert first[name] != second[name]
    assert "SECRET_SENTINEL" not in first and "SECRET_SENTINEL" not in second


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_cross_session_auth_is_rejected_before_invalid_json_or_upstream_dispatch(harness, client):
    config = GatewayConfig(Protocol.CHAT, "http://127.0.0.1:1/v1", "fake", "synthetic-own-token")
    with gateway_session(config) as origin:
        context = {"origin": origin}
        assert harness.auth_status(context, client, "synthetic-other-token") == 401
        assert harness.auth_status(context, client, "synthetic-own-token") == 422
        assert not harness.port_closed(origin)
    assert harness.port_closed(origin)


@pytest.mark.parametrize("fault", [None, "shared_id", "wrong_protocol", "no_success", "key_leak"])
def test_journal_gate_rejects_cross_provider_records_and_secret_leaks(harness, monkeypatch, tmp_path, fault):
    specs = [{"provider": name, "protocol": protocol, "model": "model-" + name, "key": "key-" + name, "answer": "answer-" + name}
             for name, protocol in [("alpha", "chat"), ("beta", "messages")]]
    records = {}
    for spec in specs:
        path = tmp_path / spec["provider"]
        path.mkdir()
        (path / ("session-" + "a" * 32 + ".jsonl")).write_text(spec["key"] if fault == "key_leak" else "synthetic", encoding="utf-8")
        records[spec["provider"]] = {"records": [{"request_id": "same" if fault == "shared_id" else spec["provider"],
                                                "upstream_protocol": "responses" if fault == "wrong_protocol" else spec["protocol"],
                                                "outcome": "authentication" if fault == "no_success" else "success"}]}
    monkeypatch.setattr(harness, "latest_diagnostics", lambda path: records[path.name])
    assert harness.diagnostics(tmp_path, specs) is (fault is None)


def test_gateway_observer_keeps_original_context_and_only_records_owned_synthetic_state(harness, monkeypatch, tmp_path):
    observer = importlib.import_module("probe_gateway_context")
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "gateway.json"
    config = SimpleNamespace(upstream_api_key="p05-synthetic-key-alpha", inbound_token="synthetic-private-token")
    observed = []

    @contextmanager
    def original(value, **kwargs):
        observed.append(value)
        yield "http://127.0.0.1:12345"

    def main():
        with observer.server.gateway_session(config) as origin:
            assert origin == "http://127.0.0.1:12345"
            assert json.loads(output.read_text())["token"] == config.inbound_token
        return 0

    monkeypatch.setattr(observer.server, "gateway_session", original)
    monkeypatch.setattr(observer.importlib, "import_module", lambda _: SimpleNamespace(main=main))
    monkeypatch.setattr(observer.sys, "argv", ["observer", "codex_adapter.cli", str(output)])
    assert observer.main() == 0
    assert observed == [config] and output.with_suffix(".closed").exists()
    assert observer.server.gateway_session is original


def test_gateway_observer_refuses_destination_outside_its_owned_working_directory(harness, monkeypatch, tmp_path):
    observer = importlib.import_module("probe_gateway_context")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(observer.sys, "argv", ["observer", "claudapter.cli", str(tmp_path.parent / "escape.json")])
    with pytest.raises(ValueError, match="owned cwd"):
        observer.main()
