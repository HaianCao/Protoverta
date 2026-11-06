"""Duration gates and bounded resource sampling; synthetic clocks/loopback only."""

import sys
from types import SimpleNamespace

import pytest
import resource_monitor
from soak_local import run_soak


class FakeThread:
    def __init__(self, **kwargs):
        self.alive = False

    def start(self):
        pass

    def join(self, timeout):
        pass

    def is_alive(self):
        return self.alive


def monitor(monkeypatch, clock, rss, handles=lambda second: 10):
    process = SimpleNamespace(
        memory_info=lambda: SimpleNamespace(rss=rss(clock[0])),
        num_threads=lambda: 3,
        num_handles=lambda: handles(clock[0]),
        net_connections=lambda **kwargs: [],
    )
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=lambda: process))
    monkeypatch.setattr(resource_monitor.threading, "Thread", FakeThread)
    monkeypatch.setattr(resource_monitor.time, "monotonic", lambda: clock[0])
    return resource_monitor.ResourceMonitor()


def test_long_sampling_keeps_true_initial_terminal_warm_and_peak_with_bounded_storage(monkeypatch):
    clock = [0]
    sampler = monitor(monkeypatch, clock, lambda second: 1024 * 1024 * (100 if second == 10 else 1))
    for second in range(1, 1001):
        clock[0] = second
        sampler.sample()
    clock[0] = 1001
    result = sampler.finish()
    assert result["available"] and result["policy_passed"]
    assert result["baseline"]["seconds"] == 0
    assert result["after"]["seconds"] == 1001
    assert result["warm_baseline"]["seconds"] == 30
    assert result["peak"]["rss_bytes"] == 100 * 1024 * 1024
    assert result["sample_count"] == 1002
    assert result["retained_sample_count"] == result["sample_capacity"] == 605
    assert result["warm_rss_growth_bytes"] == 0


def test_short_sampling_preserves_previous_warm_baseline_rule(monkeypatch):
    clock = [0]
    sampler = monitor(monkeypatch, clock, lambda second: 1024 * second)
    for second in (3, 6, 9):
        clock[0] = second
        sampler.sample()
    clock[0] = 12
    result = sampler.finish()
    assert result["warm_baseline"]["seconds"] == 6
    assert result["warm_rss_growth_bytes"] == 6 * 1024


@pytest.mark.parametrize("duration, expected", [(12, 4), (60, 20), (90, 30)])
def test_dense_sampling_preserves_duration_dependent_warm_baseline(monkeypatch, duration, expected):
    clock = [0]
    sampler = monitor(monkeypatch, clock, lambda second: second * 1024)
    for second in range(1, duration):
        clock[0] = second
        sampler.sample()
    clock[0] = duration
    assert sampler.finish()["warm_baseline"]["seconds"] == expected


def test_terminal_growth_after_old_sample_limit_fails_resource_policy(monkeypatch):
    clock = [0]
    sampler = monitor(monkeypatch, clock, lambda second: (100 if second > 605 else 1) * 1024 * 1024)
    for second in range(1, 1001):
        clock[0] = second
        sampler.sample()
    result = sampler.finish()
    assert result["available"] and not result["policy_passed"]
    assert result["after"]["rss_bytes"] == 100 * 1024 * 1024


def _run_handles(monkeypatch, handles, duration=300):
    clock = [0]
    sampler = monitor(monkeypatch, clock, lambda second: 1024 * 1024, handles)
    for second in range(1, duration):
        clock[0] = second
        sampler.sample()
    clock[0] = duration
    return sampler.finish()


def test_one_time_handle_allocation_during_warmup_passes(monkeypatch):
    # Observed on Windows: +16..20 handles in the first seconds, then flat.
    result = _run_handles(monkeypatch, lambda second: 10 if second == 0 else 30)
    assert result["policy_passed"] and result["retained_handle_delta"] == 20 and result["warm_handle_growth"] == 0


def test_handle_growth_after_warmup_still_fails(monkeypatch):
    result = _run_handles(monkeypatch, lambda second: 10 + second // 20)
    assert not result["policy_passed"] and result["warm_handle_growth"] > 4


def test_failed_sampler_shutdown_does_not_claim_resource_success_or_sample_concurrently(monkeypatch):
    sampler = monitor(monkeypatch, [0], lambda second: 1)
    sampler.thread.alive = True
    count = sampler.sample_count
    result = sampler.finish()
    assert result == {"available": False, "errors": [], "sampler_stopped": False}
    assert sampler.sample_count == count


def test_sampler_errors_are_not_accepted_as_resource_success(monkeypatch):
    sampler = monitor(monkeypatch, [0], lambda second: 1)
    sampler.process.memory_info = lambda: (_ for _ in ()).throw(RuntimeError("synthetic OS details must not leak"))
    result = sampler.finish()
    assert result == {"available": False, "errors": ["RuntimeError"], "sampler_stopped": True}


def test_sustained_run_cannot_pass_when_request_budget_ends_early():
    result = run_soak(seconds=4, requests=1, concurrency=1, interval=0.01, sustained=True)
    assert result["mode"] == "sustained"
    assert not result["passed"] and not result["duration_reached"]
    assert result["workload_seconds"] < result["duration_requested_seconds"]
    assert result["opened"] == result["closed"]
    assert result["active_after"] == result["lifetime_threads_after"] == result["heartbeat_threads_after"] == 0


def test_sustained_loopback_run_reaches_duration_and_reports_progress():
    progress = []
    result = run_soak(seconds=1, requests=100, concurrency=2, interval=0.1, sustained=True, progress=progress.append)
    assert result["passed"] and result["duration_reached"]
    assert result["workload_seconds"] >= 1
    assert progress and progress[0]["event"] == "soak_progress"
    assert set(progress[0]) == {"event", "elapsed_seconds", "issued", "active", "failures"}


@pytest.mark.parametrize("kwargs", [{"seconds": 601}, {"seconds": 14401, "sustained": True}, {"requests": 100001, "sustained": True}, {"requests": 15001}])
def test_extended_limits_are_opt_in_and_still_bounded(kwargs):
    with pytest.raises(ValueError, match="soak limits"):
        run_soak(**kwargs)
