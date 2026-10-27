import pytest

from protoverta.capabilities import _prefix
from protoverta.server import GatewayConfig


@pytest.mark.parametrize("value", [
    "https://example.test/with space", "https://exa mple.test/v1",
    " https://example.test/v1", "https://example.test/v1\n",
    "\0https://example.test/v1", "https://example.test/v1\0",
    "https://example.test/\tpath", "https://example.test/\rpath",
    "https://example.test/\x1fpath", "https://example.test/\x7fpath",
    "https://example.test/\u00a0path", "https://example.test:0/v1",
    "http://127.0.0.1:0/v1", "https://example.test:65536/v1",
    "https://example.test:-1/v1",
])
def test_invalid_url_is_rejected_by_gateway_and_capability_identity(value):
    with pytest.raises(ValueError):
        _prefix(value)
    with pytest.raises(ValueError):
        GatewayConfig("chat", value, "synthetic-key", "synthetic-local")


@pytest.mark.parametrize("value", ["https://example.test:1/v1", "https://example.test:65535/api/v1", "https://example.test/encoded%20path/v1", "http://[::1]:12345/v1"])
def test_valid_ports_encoded_paths_and_ipv6_are_preserved(value):
    assert _prefix(value) == value
    assert GatewayConfig("chat", value, "synthetic-key", "synthetic-local").upstream_url == value + "/chat/completions"
