"""Intentional type errors: the type gate must reject every marked boundary."""

from protoverta.admission import SharedAdmission
from protoverta.failures import gateway_failure
from protoverta.observability import RequestTrace


def invalid_boundary() -> None:
    SharedAdmission("fixture.sqlite", "scope", requests="forty")
    gateway_failure(503, "unavailable", code=123)
    trace = RequestTrace("chat", "responses")
    trace.outcome = "arbitrary-provider-message"
