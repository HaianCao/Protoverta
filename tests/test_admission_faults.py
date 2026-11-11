import io
import json
import sqlite3
from unittest.mock import patch
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post

from protoverta.admission import AdmissionDenied, AdmissionStorageError, SharedAdmission
from protoverta.resources import RequestBudget
from protoverta.retry import open_with_retry
from protoverta.server import GatewayConfig


@pytest.mark.parametrize(
    "error",
    [PermissionError("private path"), FileExistsError("private path"), sqlite3.OperationalError("database locked")],
)
def test_storage_errors_return_local_503_without_upstream_and_recover(tmp_path, error):
    calls = []

    def upstream(request, *, timeout):
        calls.append(1)
        return io.BytesIO(
            b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}'
        )

    config = GatewayConfig(
        "chat", "https://example.test/v1", "key", "local", shared_admission_db=str(tmp_path / "quota.sqlite")
    )
    with listener(config, upstream) as url:
        with patch.object(SharedAdmission, "_connect", side_effect=error), pytest.raises(HTTPError) as failure:
            post(url, {"model": "m", "input": "hi"})
        assert failure.value.code == 503
        assert failure.value.headers["x-should-retry"] == "false"
        assert "private path" not in failure.value.read().decode()
        failure.value.close()
        assert not calls
        with post(url, {"model": "m", "input": "hi"}) as response:
            assert response.status == 200
    assert calls == [1]


def test_parent_directory_failure_is_wrapped_at_storage_boundary(tmp_path):
    budget = SharedAdmission(tmp_path / "quota.sqlite", "fixture")
    with (
        patch("protoverta.admission.Path.mkdir", side_effect=PermissionError("private path")),
        pytest.raises(AdmissionStorageError),
    ):
        budget.acquire()


def test_failed_release_keeps_lease_until_expiry_without_refunding_rate(tmp_path):
    now = [100.0]
    budget = SharedAdmission(
        tmp_path / "quota.sqlite", "fixture", concurrency=1, requests=2, lease=5, clock=lambda: now[0]
    )
    lease = budget.acquire()
    with patch.object(budget, "_open", side_effect=PermissionError("fixture")), pytest.raises(AdmissionStorageError):
        budget.release(lease)
    with pytest.raises(AdmissionDenied):
        budget.acquire()
    now[0] = 106
    budget.release(budget.acquire())
    with pytest.raises(AdmissionDenied):
        budget.acquire()  # expired/released leases do not refund attempts


def test_http_release_failure_does_not_overwrite_completed_response(tmp_path):
    original = SharedAdmission._open
    connections = []

    def open_storage(self):
        connections.append(1)
        if len(connections) == 2:
            raise PermissionError("synthetic release failure")
        return original(self)

    config = GatewayConfig(
        "chat", "https://example.test/v1", "key", "local", shared_admission_db=str(tmp_path / "quota.sqlite")
    )
    with (
        patch.object(SharedAdmission, "_open", open_storage),
        listener(
            config,
            lambda *args, **kwargs: io.BytesIO(
                b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}'
            ),
        ) as url,
        post(url, {"model": "m", "input": "hi"}) as response,
    ):
        assert response.status == 200 and json.load(response)["output"]
    assert len(connections) == 2


def test_storage_failure_during_retry_closes_rejection_and_never_reissues(tmp_path):
    budget = SharedAdmission(tmp_path / "quota.sqlite", "fixture", requests=2)
    original, connections, calls = SharedAdmission._open, [], []
    rejected = io.BytesIO(b"{}")

    def open_storage(self):
        connections.append(1)
        if len(connections) == 2:  # acquire succeeds, publishing cooldown fails
            raise PermissionError("synthetic cooldown storage failure")
        return original(self)

    def opener(request, timeout):
        calls.append(1)
        raise HTTPError("https://example.test", 503, "fixture", {"Retry-After": "0"}, rejected)

    with patch.object(SharedAdmission, "_open", open_storage), pytest.raises(AdmissionStorageError):
        open_with_retry(opener, object(), RequestBudget(5, 5, 5), attempts=2, admission=budget)
    assert calls == [1] and rejected.closed
    budget.release(budget.acquire())
    with pytest.raises(AdmissionDenied):
        budget.acquire()
