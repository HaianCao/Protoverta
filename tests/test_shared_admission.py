import io
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post

from protoverta.admission import AdmissionDenied, AdmissionPolicyError, SharedAdmission
from protoverta.server import GatewayConfig


def test_shared_concurrency_rate_cooldown_and_expired_crash_lease(tmp_path):
    now = [100.0]
    first = SharedAdmission(
        tmp_path / "budget.sqlite", "fixture-key", requests=2, window=60, concurrency=1, lease=10, clock=lambda: now[0]
    )
    second = SharedAdmission(
        first.path, "fixture-key", requests=2, window=60, concurrency=1, lease=10, clock=lambda: now[0]
    )
    lease = first.acquire()
    with pytest.raises(AdmissionDenied):
        second.acquire()
    first.release(lease)
    lease = second.acquire()
    second.release(lease)
    with pytest.raises(AdmissionDenied):
        first.acquire()  # releases do not refund request-rate quota
    now[0] = 161
    first.cooldown(5)
    with pytest.raises(AdmissionDenied):
        second.acquire()
    now[0] = 167
    first.acquire()  # simulate a crashed owner: deliberately do not release
    now[0] = 178
    second.release(second.acquire())
    assert b"fixture-key" not in first.path.read_bytes()


def test_different_accounts_do_not_share_and_policy_conflicts_fail_closed(tmp_path):
    path = tmp_path / "quota.sqlite"
    a = SharedAdmission(path, "a", concurrency=1)
    b = SharedAdmission(path, "b", concurrency=1)
    first, second = a.acquire(), b.acquire()
    with pytest.raises(AdmissionPolicyError):
        SharedAdmission(path, "a", concurrency=2).acquire()
    a.release(first)
    b.release(second)


def test_atomic_admission_under_parallel_requests(tmp_path):
    path = tmp_path / "quota.sqlite"

    def attempt(_):
        budget = SharedAdmission(path, "same", concurrency=1)
        try:
            return budget.acquire()
        except AdmissionDenied:
            return None

    with ThreadPoolExecutor(8) as pool:
        leases = list(pool.map(attempt, range(8)))
    assert sum(lease is not None for lease in leases) == 1


def test_separate_processes_share_a_budget_without_a_daemon(tmp_path):
    script = """
import json,sys
sys.path.insert(0,sys.argv[1])
from protoverta.admission import SharedAdmission,AdmissionDenied
budget=SharedAdmission(sys.argv[2],'synthetic-account',concurrency=1)
try:
    budget.acquire()
    print(json.dumps({'admitted':True}))
except AdmissionDenied:
    print(json.dumps({'admitted':False}))
"""
    source = str(Path(__file__).resolve().parents[1] / "src")
    command = [sys.executable, "-B", "-c", script, source, str(tmp_path / "quota.sqlite")]
    results = [subprocess.run(command, capture_output=True, text=True, check=True, timeout=10) for _ in range(2)]
    assert [json.loads(result.stdout)["admitted"] for result in results] == [True, False]


def test_distinct_gateways_cannot_bypass_shared_concurrency(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def upstream(request, timeout):
        entered.set()
        assert release.wait(3)
        return io.BytesIO(
            b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}'
        )

    config = GatewayConfig(
        "chat",
        "https://example.test/v1",
        "key",
        "local",
        shared_admission_db=str(tmp_path / "budget.sqlite"),
        shared_concurrency=1,
    )
    with listener(config, upstream) as first, listener(config, upstream) as second, ThreadPoolExecutor(1) as pool:

        def run():
            with post(first, {"model": "m", "input": "hi"}) as response:
                return response.status

        task = pool.submit(run)
        try:
            assert entered.wait(2)
            with pytest.raises(HTTPError) as error:
                post(second, {"model": "other-model", "input": "hi"})
            assert error.value.code == 429
            error.value.close()
        finally:
            release.set()
        assert task.result(timeout=3) == 200
        with post(second, {"model": "other-model", "input": "hi"}) as response:
            assert response.status == 200
