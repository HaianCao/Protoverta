"""Static-check fixture, not runtime code or a pytest test module."""

from protoverta.admission import SharedAdmission
from protoverta.failures import gateway_failure
from protoverta.observability import Observer, RequestTrace
from protoverta.retry import Admission


def checked_boundary(path: str, observer: Observer) -> None:
    admission: Admission = SharedAdmission(path, "synthetic-scope", requests=2)
    identifier = admission.acquire()
    admission.release(identifier)
    payload: dict[str, object] = gateway_failure(503, "unavailable").payload("messages")
    assert payload
    trace = RequestTrace("chat", "responses")
    trace.outcome = "success"
    trace.emit(observer)
