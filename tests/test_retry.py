import io
import json
from urllib.error import HTTPError, URLError

import pytest
from test_gateway_capabilities import listener, post

from protoverta.resources import RequestBudget
from protoverta.retry import open_with_retry, retry_after
from protoverta.server import GatewayConfig


def test_retry_after_parsing():
    assert retry_after({"Retry-After": "2"}) == 2
    assert retry_after({"Retry-After": "Thu, 01 Jan 1970 00:00:10 GMT"}, now=5) == 5
    assert retry_after({"Retry-After": "inf"}) is None
    assert retry_after({"Retry-After": "invalid"}) is None


def test_explicit_rejection_can_retry_before_response_delivery():
    calls, sleeps = [], []
    response = io.BytesIO(b"ok")

    def opener(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise HTTPError("https://example.test", 503, "fixture", {"Retry-After": "1"}, io.BytesIO(b"{}"))
        return response

    assert open_with_retry(opener, object(), RequestBudget(10, 10, 10), attempts=2, sleep=sleeps.append) == (response, None)
    assert len(calls) == 2 and sleeps == [1]


@pytest.mark.parametrize("status,headers", [(400, {}), (401, {}), (403, {}), (422, {}), (503, {"x-should-retry": "false"}), (429, {"Retry-After": "60"})])
def test_permanent_or_long_cooldown_errors_are_not_retried(status, headers):
    calls = []

    def opener(request, timeout):
        calls.append(request)
        raise HTTPError("https://example.test", status, "fixture", headers, io.BytesIO(b"{}"))

    with pytest.raises(HTTPError) as error:
        open_with_retry(opener, object(), RequestBudget(10, 10, 10), attempts=3)
    error.value.close()
    assert len(calls) == 1


def test_transport_failure_is_ambiguous_and_not_retried():
    calls = []

    def opener(request, timeout):
        calls.append(1)
        raise URLError("fixture")

    with pytest.raises(URLError):
        open_with_retry(opener, object(), RequestBudget(1, 1, 1), attempts=3)
    assert len(calls) == 1


def test_gateway_retries_only_open_not_read_or_conversion():
    calls = []

    def upstream(request, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 503, "busy", {"Retry-After": "0"}, io.BytesIO(b"{}"))
        return io.BytesIO(b'{"model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}')

    config = GatewayConfig("chat", "https://example.test/v1", "key", "local", retry_attempts=2)
    with listener(config, upstream) as url, post(url, {"model": "m", "input": "Hi"}) as response:
        assert json.load(response)["output"][0]["content"][0]["text"] == "ok"
    assert len(calls) == 2


def test_deadline_during_retry_decision_closes_rejected_response():
    class Budget:
        def remaining(self):
            raise TimeoutError("fixture")

    rejected = io.BytesIO(b"{}")

    def opener(request, timeout):
        raise AssertionError("not used")

    # The opener raises after the initial budget check, then the deadline expires.
    class Expiring(Budget):
        count = 0

        def remaining(self):
            self.count += 1
            if self.count == 1:
                return 1
            return super().remaining()

    def reject(request, timeout):
        raise HTTPError("https://example.test", 503, "fixture", {}, rejected)

    with pytest.raises(TimeoutError):
        open_with_retry(reject, object(), Expiring(), attempts=2)
    assert rejected.closed
