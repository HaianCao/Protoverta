from soak_local import run_soak


def test_bounded_local_churn_closes_resources_and_keeps_gateways_isolated():
    result = run_soak(seconds=4, requests=16, concurrency=2, interval=0.01)
    assert result["passed"]
    assert result["opened"] == result["closed"]
    assert result["active_after"] == result["lifetime_threads_after"] == 0
    assert result["local_fixtures_only"]
