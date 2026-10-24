import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from protoverta.resources import ClientDisconnected, RequestBudget
from protoverta.server import GatewayConfig, gateway_session


def test_cancel_interrupts_registered_sockets_and_late_registration():
    budget = RequestBudget(10, 10, 10)
    one, other = socket.socketpair()
    with one, other:
        budget.cancel()
        budget.register_socket(one)
    with pytest.raises(ClientDisconnected):
        budget.remaining()
    with pytest.raises(ClientDisconnected):
        budget.wait(10)


def test_disconnect_closes_default_upstream_before_io_timeout():
    entered, closed = threading.Event(), threading.Event()

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.flush()
            entered.set()
            self.connection.settimeout(3)
            try:
                if self.connection.recv(1) == b"":
                    closed.set()
            except ConnectionResetError:
                closed.set()

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    worker = threading.Thread(target=upstream.serve_forever, daemon=True)
    worker.start()
    config = GatewayConfig("chat", f"http://127.0.0.1:{upstream.server_port}/v1", "fixture", "local", timeout=10)
    try:
        with gateway_session(config) as origin:
            parsed = urlsplit(origin)
            client = socket.create_connection((parsed.hostname, parsed.port), timeout=3)
            try:
                body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode()
                client.sendall(b"POST /v1/chat/completions HTTP/1.0\r\nAuthorization: Bearer local\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                assert entered.wait(2)
                client.shutdown(socket.SHUT_RDWR)
                client.close()
                assert closed.wait(2)  # well before the configured 10-second I/O timeout
            finally:
                client.close()
    finally:
        upstream.shutdown()
        upstream.server_close()
        worker.join(timeout=2)
