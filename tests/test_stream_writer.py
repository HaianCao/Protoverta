import threading

import pytest

from protoverta.resources import ClientDisconnected, DeadlineExceeded, RequestBudget
from protoverta.stream_writer import StreamHeartbeatWriter


@pytest.mark.parametrize("interval", [0, -1, True, float("nan"), float("inf"), 10**1000])
def test_interval_requires_finite_positive_number(interval):
    with pytest.raises(ValueError):
        StreamHeartbeatWriter(lambda *_: None, RequestBudget(1, 2, 1), b": ping\n\n", interval=interval)


def test_large_finite_interval_does_not_overflow_os_wait():
    writer = StreamHeartbeatWriter(lambda *_: None, RequestBudget(1, 2, 1), b": ping\n\n", interval=1e308)
    writer.start()
    writer.close()
    assert not writer.thread.is_alive()


def test_no_monitor_without_payload_or_for_native_stream():
    writer = StreamHeartbeatWriter(lambda *_: None, RequestBudget(1, 2, 1), b": ping\n\n", interval=0.01)
    assert writer.thread is None
    writer.close()
    writer.start()
    assert writer.thread is None
    native = StreamHeartbeatWriter(lambda *_: None, RequestBudget(1, 2, 1))
    native.write(b"payload")
    native.start()
    assert native.thread is None
    native.close()


def test_heartbeat_does_not_advance_content_progress_and_stops_before_terminal():
    frames, tick = [], threading.Event()
    budget = RequestBudget(1, 2, 1)
    progress = budget.last_progress

    def send(raw, timeout):
        assert 0 < timeout <= 1
        frames.append(raw)
        if raw == b": ping\n\n":
            tick.set()

    writer = StreamHeartbeatWriter(send, budget, b": ping\n\n", interval=0.01)
    writer.write(b"payload")
    writer.start()
    assert tick.wait(1)
    writer.close()
    assert not writer.thread.is_alive()
    writer.write(b"terminal")
    writer.start()
    assert frames[-1] == b"terminal"
    assert budget.last_progress == progress


def test_payload_and_timer_socket_writes_cannot_overlap():
    entered, release, tick = threading.Event(), threading.Event(), threading.Event()
    active, peak = 0, 0

    def send(raw, timeout):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            if raw == b"payload-frame":
                entered.set()
                assert release.wait(1)
            else:
                tick.set()
        finally:
            active -= 1

    writer = StreamHeartbeatWriter(send, RequestBudget(1, 3, 2), b": ping\n\n", interval=0.01)
    payload = threading.Thread(target=writer.write, args=(b"payload-frame",))
    payload.start()
    assert entered.wait(1)
    writer.start()
    timer = threading.Timer(0.05, release.set)
    timer.start()
    try:
        assert tick.wait(1)
        assert peak == 1
    finally:
        release.set()
        payload.join(1)
        timer.join()
        writer.close()
    assert not writer.thread.is_alive()


@pytest.mark.parametrize("failure", [BrokenPipeError, TimeoutError])
def test_failed_heartbeat_cancels_upstream_without_echoing_error(failure):
    failed = threading.Event()
    budget = RequestBudget(1, 3, 2)

    def send(raw, timeout):
        failed.set()
        raise failure("synthetic-private-detail")

    writer = StreamHeartbeatWriter(send, budget, b": ping\n\n", interval=0.01)
    writer.start()
    assert failed.wait(1)
    writer.close()
    assert not writer.thread.is_alive()
    with pytest.raises(ClientDisconnected):
        budget.remaining()
    with pytest.raises(ClientDisconnected) as error:
        writer.write(b"error", final_error=True)
    assert "synthetic-private" not in str(error.value)


def test_monitor_observing_deadline_does_not_turn_it_into_client_disconnect():
    now = [0.0]
    budget = RequestBudget(1, 0.1, 1, clock=lambda: now[0])
    writer = StreamHeartbeatWriter(
        lambda *_: pytest.fail("deadline must not send bytes"), budget, b": ping\n\n", interval=0.01
    )
    now[0] = 0.2
    writer.start()
    writer.thread.join(1)
    assert not writer.thread.is_alive()
    assert not budget._cancelled.is_set()
    with pytest.raises(DeadlineExceeded) as error:
        budget.remaining()
    assert error.value.kind == "request_deadline"
    writer.close()
