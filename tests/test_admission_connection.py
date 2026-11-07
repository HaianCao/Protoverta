import io
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from test_gateway_capabilities import listener, post

from protoverta.admission import AdmissionDenied, AdmissionStorageError, SharedAdmission
from protoverta.server import REQUEST_SLOT_GRACE_SECONDS, GatewayConfig, GatewayHTTPServer, make_handler


def counting_connect(budget):
    original, calls = budget._connect, []

    def connect():
        calls.append(1)
        return original()

    return connect, calls


def test_transactions_reuse_one_connection_in_wal_mode(tmp_path):
    budget = SharedAdmission(tmp_path / "quota.sqlite", "fixture", requests=100, concurrency=100)
    connect, calls = counting_connect(budget)
    with patch.object(budget, "_connect", connect):
        for _ in range(20):
            budget.release(budget.acquire())
    assert len(calls) == 1
    probe = sqlite3.connect(tmp_path / "quota.sqlite")
    try:
        assert probe.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        probe.close()
    budget.close()


def test_denied_admission_rolls_back_and_keeps_the_connection(tmp_path):
    budget = SharedAdmission(tmp_path / "quota.sqlite", "fixture", requests=1, concurrency=1)
    connect, calls = counting_connect(budget)
    with patch.object(budget, "_connect", connect):
        budget.acquire()
        for _ in range(3):
            with pytest.raises(AdmissionDenied):
                budget.acquire()
    assert len(calls) == 1
    budget.close()


def test_storage_failure_discards_the_connection_and_recovers(tmp_path):
    budget = SharedAdmission(tmp_path / "quota.sqlite", "fixture")
    budget.release(budget.acquire())
    holder = sqlite3.connect(tmp_path / "quota.sqlite", isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        with patch("protoverta.admission.STORAGE_WAIT_SECONDS", 0.1):
            stale = budget._connection
            stale.execute("PRAGMA busy_timeout=100")
            with pytest.raises(AdmissionStorageError):
                budget.acquire()
        assert budget._connection is None
    finally:
        holder.close()
    budget.release(budget.acquire())
    assert budget._connection is not None and budget._connection is not stale
    budget.close()


def test_request_released_within_the_grace_window_is_admitted():
    entered, calls = threading.Event(), []

    def upstream(request, *, timeout):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            time.sleep(REQUEST_SLOT_GRACE_SECONDS / 5)
        return io.BytesIO(b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}')

    config = GatewayConfig("chat", "https://example.test/v1", "key", "local", max_concurrent_requests=1)
    with listener(config, upstream) as url, ThreadPoolExecutor(max_workers=1) as pool:

        def run():
            with post(url, {"model": "m", "input": "Hi"}) as response:
                return response.status

        first = pool.submit(run)
        assert entered.wait(2)
        assert run() == 200
        assert first.result(timeout=2) == 200
    assert len(calls) == 2


def test_closed_gateway_releases_the_database_file(tmp_path):
    config = GatewayConfig("chat", "https://example.test/v1", "key", "local", shared_admission_db=str(tmp_path / "quota.sqlite"))

    def upstream(request, *, timeout):
        return io.BytesIO(b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}')

    server = GatewayHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=upstream))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with post(f"http://127.0.0.1:{server.server_port}/v1/responses", {"model": "m", "input": "hi"}) as response:
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    for path in tmp_path.iterdir():
        path.unlink()  # Windows refuses to delete a file that is still open.


def test_closed_admission_does_not_keep_new_connections(tmp_path):
    budget = SharedAdmission(tmp_path / "quota.sqlite", "fixture")
    budget.release(budget.acquire())
    budget.close()
    budget.release(budget.acquire())
    assert budget._connection is None
