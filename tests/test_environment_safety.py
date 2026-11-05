from test_environment import sanitized_environment


def test_validation_env_retains_os_basics_and_never_inherits_provider_credentials():
    source = {
        "PATH": "fixture-bin",
        "SystemRoot": "C:/Windows",
        "TEMP": "fixture-temp",
        "AGENT_ROUTER_TOKEN": "synthetic-secret",
        "OPENAI_API_KEY": "synthetic-secret",
        "ANTHROPIC_AUTH_TOKEN": "synthetic-secret",
        "UNUSUAL_CREDENTIAL_NAME": "synthetic-secret",
        "HTTPS_PROXY": "http://synthetic-user:synthetic-pass@proxy.test",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://telemetry.test",
        "PYTHONPATH": "old-checkout",
        "PYTEST_ADDOPTS": "--showlocals",
    }
    result = sanitized_environment(source)
    assert set(result) == {"PATH", "SystemRoot", "TEMP", "PYTHONDONTWRITEBYTECODE"}
    assert source["AGENT_ROUTER_TOKEN"] == "synthetic-secret"  # Input is not mutated.
    assert "synthetic-secret" not in repr(result) and "synthetic-pass" not in repr(result)
