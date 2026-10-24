import io
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

import pytest
from test_gateway_capabilities import listener, post

from protoverta.server import GatewayConfig


@pytest.mark.parametrize("options", [{"timeout": float("nan")}, {"timeout": float("inf")}, {"timeout": True}, {"max_body_bytes": True}, {"max_concurrent_requests": True}, {"max_concurrent_requests": 0}, {"max_concurrent_requests": 1.5}])
def test_invalid_io_limits_are_rejected(options):
    with pytest.raises(ValueError):
        GatewayConfig("chat", "https://example.test/v1", "key", "local", **options)


def test_capacity_is_bounded_and_slot_is_released_after_upstream_failure():
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def upstream(request, *, timeout):
        calls.append(request)
        entered.set()
        assert release.wait(2)
        raise HTTPError(request.full_url, 503, "busy", {}, io.BytesIO(b"{}"))

    config = GatewayConfig("chat", "https://example.test/v1", "key", "local", max_concurrent_requests=1)
    with listener(config, upstream) as url, ThreadPoolExecutor(max_workers=1) as pool:
        def run():
            with pytest.raises(HTTPError) as caught:
                post(url, {"model": "m", "input": "Hi"})
            status = caught.value.code
            caught.value.close()
            return status
        first = pool.submit(run)
        try:
            assert entered.wait(2)
            with pytest.raises(HTTPError) as caught:
                post(url, {"model": "m", "input": "Hi"})
            assert caught.value.code == 503 and caught.value.headers["Retry-After"] == "1"
            caught.value.close()
            assert len(calls) == 1
        finally:
            release.set()
        assert first.result(timeout=2) == 503
        assert run() == 503
        assert len(calls) == 2
