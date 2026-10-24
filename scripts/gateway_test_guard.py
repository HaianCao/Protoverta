"""Pytest integration plugin: missing/obsolete core or skipped gateway tests fail."""

import dataclasses

import pytest


def pytest_sessionstart(session):
    try:
        from protoverta.server import GatewayConfig
    except ImportError as exc:
        raise pytest.UsageError("Integration tests require Protoverta; skipping is not allowed.") from exc
    required = {"client_tool_search", "provider_profiles", "shared_admission_db", "retry_attempts"}
    if not required <= {field.name for field in dataclasses.fields(GatewayConfig)}:
        raise pytest.UsageError("Installed Protoverta lacks the required integration runtime API.")


def pytest_runtest_logreport(report):
    if report.skipped and report.nodeid.split("::", 1)[0].replace("\\", "/").rsplit("/", 1)[-1] in {"test_gateway_limits.py", "test_gateway_runtime.py", "test_auth_mode.py"}:
        pytest.exit("Integration gateway tests must not skip: " + report.nodeid, returncode=1)
