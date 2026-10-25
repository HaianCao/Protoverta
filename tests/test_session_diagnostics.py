import io
import threading
import time

from protoverta.observability import RequestObservation
from protoverta.session_diagnostics import _journals, _JournalSink, latest_diagnostics


def test_blocked_filesystem_cannot_block_producers_or_shutdown():
    entered, release = threading.Event(), threading.Event()

    class SlowStream(io.StringIO):
        def write(self, value):
            entered.set()
            release.wait(3)
            return super().write(value)

    stream = SlowStream()
    sink = _JournalSink(stream)
    record = RequestObservation("a" * 32, "responses", "chat", 200, "success", 1, False)
    try:
        sink(record)
        assert entered.wait(1)
        started = time.monotonic()
        for _ in range(1000):
            sink(record)
        sink.close()
        assert time.monotonic() - started < 1
        assert sink.pending.qsize() <= 128 and sink.dropped > 0
        assert not stream.closed
    finally:
        release.set()
        sink.thread.join(2)
    assert stream.closed and not sink.thread.is_alive()


def test_scan_cost_bounded_even_for_unrelated_files(tmp_path):
    for index in range(600):
        (tmp_path / str(index)).touch()
    paths, limited = _journals(tmp_path)
    assert paths == [] and limited
    assert latest_diagnostics(tmp_path) == {"records": [], "limited": True}
