"""Truthful schema improvements and audit verdicts; no upstream provider calls."""

import copy
import importlib
import json
from types import SimpleNamespace

import pytest
from wire_cases import source_wire

from protoverta import (
    StreamTranscoder,
    bind_response_context,
    convert_request,
    convert_response,
)
from protoverta.response_context import restore_request_context
from protoverta.sse import SSEParser


def gate(monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("check_openresponses")


@pytest.mark.parametrize("source", ["chat", "messages"])
def test_stream_snapshots_include_only_known_nullable_envelope_facts(source):
    pipeline = StreamTranscoder(source, "responses")
    wire = pipeline.feed(source_wire(source, tools=False)) + pipeline.finish()
    snapshots = [json.loads(event.data)["response"] for event in SSEParser().feed(wire) if "response" in json.loads(event.data)]
    assert len(snapshots) >= 2
    for snapshot in snapshots:
        assert snapshot["max_tool_calls"] is None
        assert snapshot["safety_identifier"] is None
        assert snapshot["prompt_cache_key"] is None
        assert "service_tier" not in snapshot
        assert not {"presence_penalty", "frequency_penalty", "top_logprobs"} & snapshot.keys()


@pytest.mark.parametrize("strict", ["omitted", None, False, True])
def test_function_metadata_nulls_preserve_explicit_values_and_original_request(strict):
    tool = {"type": "function", "name": "lookup", "description": "", "parameters": {}}
    if strict != "omitted":
        tool["strict"] = strict
    request = {"tools": [tool]}
    before = copy.deepcopy(request)
    result = bind_response_context(request)({"output": []})
    assert result["tools"] == [{**tool, "strict": None if strict == "omitted" else strict}]
    assert request == before


def test_missing_function_metadata_is_unknown_not_an_invented_default():
    result = restore_request_context({"output": []}, {"tools": [{"type": "function", "name": "lookup"}]})
    assert result["tools"][0] == {"type": "function", "name": "lookup", "description": None, "parameters": None, "strict": None}


@pytest.mark.parametrize("tool", [{"type": "custom", "name": "fixture"}, {"type": "namespace", "name": "fixture", "tools": []}])
def test_function_null_projection_does_not_rewrite_other_tool_kinds(tool):
    assert restore_request_context({"output": []}, {"tools": [tool]})["tools"] == [tool]


@pytest.mark.parametrize("source", ["chat", "messages"])
def test_audit_json_and_sse_share_the_same_declared_fixture_measurements(monkeypatch, source):
    module = gate(monkeypatch)
    body = convert_request({"model": "m", "max_tokens": 20, "messages": [{"role": "user", "content": "synthetic"}]}, "chat", source)
    with module.fixture(source)(SimpleNamespace(data=json.dumps(body).encode()), timeout=1) as response:
        completed = convert_response(json.load(response), source, "responses")
    body["stream"] = True
    with module.fixture(source)(SimpleNamespace(data=json.dumps(body).encode()), timeout=1) as response:
        pipeline = StreamTranscoder(source, "responses")
        raw = pipeline.feed(response.read()) + pipeline.finish()
    terminal = json.loads(SSEParser().feed(raw)[-1].data)["response"]
    assert terminal["usage"] == completed["usage"]
    if source == "chat":
        assert terminal["usage"]["output_tokens_details"]["reasoning_tokens"] == 0
    else:
        assert "output_tokens_details" not in terminal["usage"]


def reports(module, *, passed=False):
    return [
        {
            "upstream": source,
            "exit_code": 0 if passed else 1,
            "results": [
                {"id": name, "status": "passed" if passed else "failed", "errors": [] if passed else ["temperature: Required", "usage: Invalid input"]}
                for name in module.CASES
            ],
        }
        for source in ("chat", "messages")
    ]


def test_audit_summary_never_calls_expected_schema_failure_a_pass(monkeypatch):
    module = gate(monkeypatch)
    result = module.summarize(reports(module))
    assert result["outcome"] == "FAILED_FULL_SCHEMA"
    assert (result["passed"], result["failed"], result["skipped"], result["total"]) == (0, 12, 0, 12)
    assert result["field_groups"]["request_metadata_or_nullable_tool_fields"]["temperature"] == 12
    assert result["field_groups"]["upstream_measurements_or_provider_facts"]["usage"] == 12


def test_audit_pass_requires_all_cases_both_upstreams_and_successful_processes(monkeypatch):
    module = gate(monkeypatch)
    good = reports(module, passed=True)
    assert module.summarize(good)["outcome"] == "PASSED_FULL_SCHEMA"
    for bad in ([], good[:1], [good[0], good[0]], [{**good[0], "exit_code": 1}, good[1]]):
        assert module.summarize(bad)["outcome"] == "FAILED_FULL_SCHEMA"


@pytest.mark.parametrize("bad", ["duplicate", "missing", "skipped", "unknown"])
def test_official_case_validation_rejects_incomplete_or_ambiguous_runs(monkeypatch, bad):
    module = gate(monkeypatch)
    output = {"results": reports(module, passed=True)[0]["results"], "summary": {"skipped": 0}}
    if bad == "duplicate":
        output["results"].append(copy.deepcopy(output["results"][0]))
    elif bad == "missing":
        output["results"].pop()
    else:
        output["results"][0]["status"] = bad
    with pytest.raises(ValueError):
        module.validate_cases(output)
