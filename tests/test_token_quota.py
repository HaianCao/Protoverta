import io
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post

from protoverta.admission import AdmissionDenied, AdmissionPolicyError
from protoverta.context import TokenCount
from protoverta.server import GatewayConfig
from protoverta.token_quota import SharedTokenQuota


def test_atomic_reservations_reconcile_unknown_usage_and_policy_conflicts(tmp_path):
    now = [100.0]
    quota = SharedTokenQuota(tmp_path / "quota.sqlite", "synthetic-key-scope", "m", limit=100, lease=10, clock=lambda: now[0])
    with ThreadPoolExecutor(max_workers=8) as pool:
        def reserve(_):
            try:
                return quota.reserve(30)
            except AdmissionDenied:
                return None
        identifiers = list(pool.map(reserve, range(8)))
    accepted = [value for value in identifiers if value]
    assert len(accepted) == 3
    quota.reconcile(accepted[0], 1, 1)
    assert quota.reserve(30)
    quota.reconcile(accepted[1])
    with pytest.raises(AdmissionDenied):
        quota.reserve(30)
    with pytest.raises(AdmissionPolicyError):
        SharedTokenQuota(tmp_path / "quota.sqlite", "synthetic-key-scope", "m", limit=101, clock=lambda: now[0]).reserve(1)
    now[0] += 61
    assert quota.reserve(100)


def test_active_lease_survives_rate_window_and_models_are_separate(tmp_path):
    now = [100.0]
    quota = SharedTokenQuota(tmp_path / "q.sqlite", "synthetic-private-key-value", "m", limit=10, lease=120, clock=lambda: now[0])
    quota.reserve(10)
    now[0] += 61
    with pytest.raises(AdmissionDenied):
        quota.reserve(1)
    assert SharedTokenQuota(tmp_path / "q.sqlite", "synthetic-private-key-value", "other", limit=10, clock=lambda: now[0]).reserve(10)
    assert b"synthetic-private-key-value" not in (tmp_path / "q.sqlite").read_bytes()


def test_extreme_reported_usage_exhausts_quota_without_sql_overflow(tmp_path):
    quota = SharedTokenQuota(tmp_path / "q.sqlite", "scope", "m", limit=100)
    identifier = quota.reserve(1)
    quota.reconcile(identifier, 2**100, 2**100)
    with pytest.raises(AdmissionDenied):
        quota.reserve(1)
    with pytest.raises(ValueError):
        SharedTokenQuota(tmp_path / "q.sqlite", "scope", "m", limit=2**63)


def config(tmp_path):
    return GatewayConfig("chat", "https://example.invalid/v1", "fixture", "local", shared_admission_db=str(tmp_path / "quota.sqlite"), shared_token_limit=100, token_counter=lambda body, protocol: TokenCount(10, exact=True))


def test_gateway_reconciles_reported_usage_without_modifying_request(tmp_path):
    requests = []
    def upstream(request, timeout):
        requests.append(json.loads(request.data))
        return io.BytesIO(b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":1}}')
    body = {"model": "m", "input": "synthetic", "max_output_tokens": 79}
    with listener(config(tmp_path), upstream) as url:
        for _ in range(2):
            with post(url, body) as response:
                assert response.status == 200
            # Reconciliation is handler cleanup, potentially after body delivery.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with sqlite3.connect(tmp_path / "quota.sqlite") as db:
                    if db.execute("SELECT COUNT(*) FROM token_reservations WHERE active_until>0").fetchone()[0] == 0:
                        break
                time.sleep(0.01)
    assert len(requests) == 2
    assert all(request.get("max_completion_tokens", request.get("max_tokens")) == 79 for request in requests)


def test_missing_usage_retains_reservation_and_missing_caps_or_estimates_reject_before_call(tmp_path):
    calls = []
    def upstream(request, timeout):
        calls.append(1)
        return io.BytesIO(b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}')
    body = {"model": "m", "input": "synthetic", "max_output_tokens": 80}
    item = config(tmp_path)
    with listener(item, upstream) as url:
        with post(url, body):
            pass
        with pytest.raises(HTTPError) as error:
            post(url, body)
        assert error.value.code == 429
        error.value.close()
    assert len(calls) == 1
    for invalid, request in [(item, {"model": "m", "input": "synthetic"}), (replace(item, token_counter=lambda *_: TokenCount(10)), body)]:
        with listener(invalid, lambda *_: pytest.fail("must not generate")) as url:
            with pytest.raises(HTTPError) as error:
                post(url, request)
            assert error.value.code == 422
            error.value.close()


def test_ambiguous_upstream_transport_never_refunds_tokens(tmp_path):
    calls = []
    def failed(request, timeout):
        calls.append(1)
        raise OSError("private")
    with listener(config(tmp_path), failed) as url:
        for expected in (502, 429):
            with pytest.raises(HTTPError) as error:
                post(url, {"model": "m", "input": "synthetic", "max_output_tokens": 80})
            assert error.value.code == expected
            error.value.close()
    assert len(calls) == 1
