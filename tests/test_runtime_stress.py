import io
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post

from protoverta.admission import AdmissionDenied, AdmissionStorageError, SharedAdmission
from protoverta.resources import ClientDisconnected, RequestBudget
from protoverta.retry import open_with_retry
from protoverta.server import GatewayConfig
from protoverta.sse import SSEParser, encode_sse
from protoverta.stream_encoder import StreamEncoder
from protoverta.streaming import Delta


def test_busy_sqlite_is_bounded_and_recovers_without_reset(tmp_path):
    budget = SharedAdmission(tmp_path / "quota.sqlite", "fixture")
    connection = budget._connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(AdmissionStorageError) as error:
            budget.acquire()
        assert isinstance(error.value.__cause__, sqlite3.Error)
    finally:
        connection.close()
    budget.release(budget.acquire())


def test_disconnect_during_retry_wait_does_not_reissue_or_refund(tmp_path):
    budget = RequestBudget(10, 10, 10)
    shared = SharedAdmission(tmp_path / "quota.sqlite", "fixture", requests=2, concurrency=1)
    entered, calls, rejected = threading.Event(), [], io.BytesIO(b"{}")
    original_wait = budget.wait

    def wait(seconds):
        entered.set()
        original_wait(seconds)

    budget.wait = wait

    def opener(request, timeout):
        calls.append(1)
        # 502 is retryable without publishing a shared 503 cooldown in this test.
        raise HTTPError("https://example.test", 502, "fixture", {"Retry-After": "2"}, rejected)

    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(open_with_retry, opener, object(), budget, attempts=2, max_wait=3, admission=shared)
        try:
            assert entered.wait(2)
        finally:
            budget.cancel()
        with pytest.raises(ClientDisconnected):
            pending.result(timeout=2)
    assert calls == [1] and rejected.closed
    shared.release(shared.acquire())
    with pytest.raises(AdmissionDenied):
        shared.acquire()


def test_concurrent_truncated_stream_churn_keeps_isolation_and_closes_resources(tmp_path):
    lock = threading.Lock()
    opened, closed, active = [], [], []
    monitor_names = {"protoverta-client-lifetime", "protoverta-sse-heartbeat"}
    baseline = {(thread.name, thread.ident) for thread in threading.enumerate() if thread.name in monitor_names}

    class Response(io.BytesIO):
        def close(self):
            if not self.closed:
                with lock:
                    closed.append(1)
                    active.pop()
            super().close()

    def upstream(request, *, timeout):
        body = json.loads(request.data)
        index = int(body["model"].split("-")[-1])
        encoder = StreamEncoder("chat")
        deltas = [Delta("start", model=body["model"], input_tokens=1, output_tokens=0), Delta("text", text="reply-" + str(index)), Delta("finish", reason="stop", input_tokens=1, output_tokens=1)]
        wire = b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta))
        if index % 5 == 0:
            wire = wire[:wire.index(b'[DONE]')]  # no successful upstream EOF marker
        with lock:
            opened.append(1)
            active.append(1)
        return Response(wire)

    config = GatewayConfig("chat", "https://example.test/v1", "key", "local", shared_admission_db=str(tmp_path / "quota.sqlite"), shared_requests=128, shared_concurrency=16)
    with listener(config, upstream) as url:
        def run(index):
            with post(url, {"model": "m-" + str(index), "input": "hi", "stream": True}) as response:
                events = [json.loads(event.data) for event in SSEParser().feed(response.read()) if event.data != "[DONE]"]
            completed = [event for event in events if event.get("type") == "response.completed"]
            assert bool(completed) is (index % 5 != 0)
            if completed:
                assert completed[0]["response"]["model"] == "m-" + str(index)
                assert completed[0]["response"]["output"][0]["content"][0]["text"] == "reply-" + str(index)

        with ThreadPoolExecutor(6) as pool:
            list(pool.map(run, range(48)))
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and any(thread.name in monitor_names and (thread.name, thread.ident) not in baseline for thread in threading.enumerate()):
        time.sleep(0.01)
    assert len(opened) == len(closed) == 48 and not active
    assert not any(thread.name in monitor_names and (thread.name, thread.ident) not in baseline for thread in threading.enumerate())
