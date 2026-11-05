"""Socket lifetime edges must cancel once without consuming pipelined bytes."""

import select
import socket
import threading
from unittest.mock import Mock

import pytest

from protoverta.lifetime import DisconnectWatch


@pytest.mark.parametrize("mode", ["exceptional", "select_error", "closed_socket"])
def test_unusable_client_cancels_once_and_watch_thread_exits(monkeypatch, mode):
    cancelled = threading.Event()
    cancel = Mock(side_effect=cancelled.set)
    one, other = socket.socketpair()
    with one, other:
        if mode == "exceptional":
            monkeypatch.setattr("protoverta.lifetime.select.select", lambda *_: ([], [], [one]))
        elif mode == "select_error":

            def failed_select(*_):
                raise OSError("synthetic socket failure")

            monkeypatch.setattr("protoverta.lifetime.select.select", failed_select)
        else:
            other.close()
        with DisconnectWatch(one, cancel) as watch:
            assert cancelled.wait(2)
            watch.thread.join(timeout=2)
            assert not watch.thread.is_alive()
        cancel.assert_called_once_with()


def test_pipelined_bytes_are_not_consumed_and_normal_exit_does_not_cancel(monkeypatch):
    cancel = Mock()
    observed = threading.Event()
    original_select = select.select

    def readable(*args):
        result = original_select(*args)
        if result[0]:
            observed.set()
        return result

    monkeypatch.setattr("protoverta.lifetime.select.select", readable)
    one, other = socket.socketpair()
    with one, other:
        other.sendall(b"NEXT REQUEST")
        with DisconnectWatch(one, cancel) as watch:
            assert observed.wait(2)
            # A readable live socket contains future HTTP bytes, not EOF.
            assert one.recv(1, socket.MSG_PEEK) == b"N"
        assert not watch.thread.is_alive()
        assert one.recv(64) == b"NEXT REQUEST"
        cancel.assert_not_called()
