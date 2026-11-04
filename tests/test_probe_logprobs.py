import importlib
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest
from test_text_logprobs import response, stream


@pytest.fixture
def probe_module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("probe_logprobs")


@pytest.mark.parametrize("streaming", [False, True])
def test_probe_checks_both_conversions_without_exposing_observations(probe_module, streaming):
    calls = []

    def upstream(request, *, timeout):
        calls.append(json.loads(request.data))
        return io.BytesIO(stream("chat")[0] if streaming else json.dumps(response("chat")).encode())

    result = probe_module.probe("https://example.invalid/v1", "m", "private-key", streaming=streaming, upstream_open=upstream)
    assert result["passed"] and result["token_records"] == 1 and len(calls) == 1, result
    assert calls[0]["max_completion_tokens"] == 64 and calls[0]["logprobs"] is True
    assert "private-key" not in json.dumps(result) and "hello" not in json.dumps(result)
    assert result["upstream_requests"] == 1
    if not streaming:
        assert result["upstream_shape"]["has_logprob_records"]


@pytest.mark.parametrize("status", [400, 401, 404, 429, 503])
def test_probe_never_retries_or_copies_provider_errors(probe_module, status):
    calls = []

    def upstream(request, *, timeout):
        calls.append(1)
        raise HTTPError(request.full_url, status, "private-key raw provider text", {}, io.BytesIO(b'{"error":{"message":"private-key"}}'))

    result = probe_module.probe("https://example.invalid/v1", "m", "private-key", upstream_open=upstream)
    assert result["http_status"] == status and result["passed"] is False and calls == [1]
    assert "private-key" not in json.dumps(result)


def test_shape_observations_do_not_copy_reasoning_text_or_error_values(probe_module):
    raw = json.dumps({"choices": [{"message": {"content": "private answer", "reasoning_content": "private reasoning"},
                                  "logprobs": None, "finish_reason": "length"}]}).encode()
    result = probe_module.shape(raw)
    assert result["has_reasoning"] and result["finish_reason"] == "length"
    assert result["has_logprob_records"] is False
    assert "private" not in json.dumps(result)
