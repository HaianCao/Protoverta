"""Keyless real-CLI keepalive parsing check with a short synthetic EOF pause."""

import argparse
import json
import math
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch
from urllib.request import Request

import smoke_clients

from protoverta import server
from protoverta.core import Protocol


def run(client, *, pause_seconds=0.3, heartbeat_interval=0.03):
    if not (
        math.isfinite(pause_seconds)
        and math.isfinite(heartbeat_interval)
        and 0 < heartbeat_interval < pause_seconds <= 30
    ):
        raise ValueError("Require 0 < heartbeat interval < fixture pause <= 30 seconds")
    keepalives = []
    original_writer = server.StreamHeartbeatWriter

    @contextmanager
    def paused_fixture(open_upstream):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                request = Request("http://127.0.0.1" + self.path, data=body)
                with open_upstream(request, timeout=10) as upstream:
                    wire = upstream.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(wire)
                self.wfile.flush()
                time.sleep(pause_seconds)  # Real HTTP EOF delayed, never a provider call.

        listener = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        listener.daemon_threads = True
        thread = Thread(target=listener.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{listener.server_port}"
        finally:
            listener.shutdown()
            listener.server_close()
            thread.join(timeout=5)

    def writer(send, *args, **kwargs):
        def record(raw, timeout):
            if raw == b": protoverta keepalive\n\n" or raw == b'event: ping\ndata: {"type":"ping"}\n\n':
                keepalives.append(True)
            send(raw, timeout)

        return original_writer(record, *args, **kwargs)

    with (
        patch.object(smoke_clients, "fixture_server", paused_fixture),
        patch.object(server, "StreamHeartbeatWriter", writer),
        patch.object(server, "STREAM_HEARTBEAT_INTERVAL", heartbeat_interval),
    ):
        result = smoke_clients.run_smoke(client, Protocol.CHAT)
    return {
        "client": client,
        "upstream": "chat",
        "passed": result["passed"] and bool(keepalives),
        "keepalives": len(keepalives),
        "requests": result["rounds"],
        "fixture_pause_seconds": pause_seconds,
        "test_heartbeat_interval_seconds": heartbeat_interval,
        "scope": "Bare CLI, isolated state and local fixtures. Parser acceptance only; no real provider or production-duration idle-timeout certification.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex", "claude"], required=True)
    parser.add_argument("--pause-seconds", type=float, default=0.3)
    parser.add_argument("--heartbeat-interval", type=float, default=0.03)
    args = parser.parse_args()
    result = run(args.client, pause_seconds=args.pause_seconds, heartbeat_interval=args.heartbeat_interval)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
