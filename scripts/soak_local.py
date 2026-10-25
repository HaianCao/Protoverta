"""Bounded local-only gateway soak. No provider keys, installation or session IO."""

import argparse
import io
import json
import socket
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_environment import sanitized_environment


def run_soak(*, seconds=60, requests=5000, concurrency=4, interval=0.02, measure_resources=False):
    from protoverta.server import GatewayConfig, GatewayHTTPServer, make_handler
    from protoverta.sse import SSEParser, encode_sse

    if not 1 <= seconds <= 600 or type(requests) is not int or not 1 <= requests <= 15000 or type(concurrency) is not int or not 1 <= concurrency <= 16 or not 0.01 <= interval <= 1:
        raise ValueError("soak limits: 1-600 seconds, 1-15000 requests, 1-16 workers, 0.01-1 second interval")
    from resource_monitor import ResourceMonitor

    warmup = None
    cold_resources = None
    if measure_resources:
        import psutil

        process = psutil.Process()
        cold_resources = {"rss_bytes": process.memory_info().rss, "threads": process.num_threads(),
                          "handles_or_fds": process.num_handles() if hasattr(process, "num_handles") else process.num_fds()}
        # The first network/thread workload initializes process-wide native
        # caches. Report its cost separately, and compare retained resources to
        # an explicitly warmed baseline, never silently widen the thresholds.
        warmup = run_soak(seconds=5, requests=min(requests, 1000), concurrency=concurrency, interval=interval)
    monitor = ResourceMonitor() if measure_resources else None
    baseline = {thread.ident for thread in threading.enumerate() if thread.name == "protoverta-client-lifetime"}
    lock = threading.Lock()
    counts = Counter()
    failures = []
    opened = closed = active = peak = issued = 0
    started = time.monotonic()
    deadline = started + seconds

    def upstream(request, timeout):
        nonlocal opened, closed, active, peak
        payload = json.loads(request.data)
        index = int(payload["model"].removeprefix("model-"))
        assert request.get_header("Authorization") == "Bearer synthetic-key-" + str(index % 2)
        text = "reply_" + str(index)
        with lock:
            opened += 1
            active += 1
            peak = max(peak, active)
        if payload.get("stream"):
            def chunk(delta, reason=None, usage=None):
                value = {"object": "chat.completion.chunk", "model": payload["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
                if usage:
                    value.update(choices=[], usage=usage)
                return encode_sse("message", json.dumps(value))

            raw = chunk({"role": "assistant", "content": text}) + chunk({}, "stop")
            raw += chunk({}, usage={"prompt_tokens": 1, "completion_tokens": 1})
            if index % 7:
                raw += encode_sse("message", "[DONE]")
        else:
            raw = json.dumps({"model": payload["model"], "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode()

        class Response(io.BytesIO):
            def close(self):
                nonlocal closed, active
                if not self.closed:
                    with lock:
                        closed += 1
                        active -= 1
                super().close()

        return Response(raw)

    def observe(record):
        with lock:
            counts["outcome_" + record.outcome] += 1

    servers, threads = [], []
    with tempfile.TemporaryDirectory(prefix="protoverta-local-soak-") as folder:
        try:
            for provider in range(2):
                config = GatewayConfig("chat", "https://example.invalid/v1", "synthetic-key-" + str(provider), "local-" + str(provider), messages_usage_policy="deferred", timeout=5, total_timeout=10, progress_timeout=5,
                                       shared_admission_db=str(Path(folder) / "quota.sqlite"), shared_requests=requests * 2, shared_concurrency=32)
                server = GatewayHTTPServer(("127.0.0.1", 0), make_handler(config, upstream_open=upstream, observer=observe))
                server.daemon_threads = True
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                servers.append(server)
                threads.append(thread)

            def worker():
                nonlocal issued
                while time.monotonic() < deadline:
                    with lock:
                        if issued >= requests:
                            return
                        index = issued
                        issued += 1
                    provider = index % 2
                    protocol = "responses" if provider == 0 else "messages"
                    path = "/v1/" + protocol
                    body = {"model": "model-" + str(index), "stream": bool(index % 3)}
                    if protocol == "responses":
                        body.update(input="synthetic", max_output_tokens=64)
                    else:
                        body.update(messages=[{"role": "user", "content": "synthetic"}], max_tokens=64)
                    data = json.dumps(body).encode()
                    url = f"http://127.0.0.1:{servers[provider].server_port}"
                    try:
                        if index % 11 == 0:
                            with socket.create_connection(("127.0.0.1", servers[provider].server_port), timeout=5) as sock:
                                sock.sendall(f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer local-{provider}\r\nContent-Length: {len(data)}\r\n\r\n".encode() + data)
                            with lock:
                                counts["cancelled_clients"] += 1
                        else:
                            with urlopen(Request(url + path, data=data, headers={"Authorization": "Bearer local-" + str(provider)}), timeout=10) as response:
                                raw = response.read()
                            if body["stream"]:
                                events = SSEParser().feed(raw)
                                if index % 7 == 0:
                                    assert events[-1].event == "error"
                                    assert all(item.event not in ("response.completed", "message_stop") for item in events)
                                else:
                                    assert events[-1].event == ("response.completed" if protocol == "responses" else "message_stop")
                                    assert ("reply_" + str(index)) in raw.decode()
                            else:
                                assert ("reply_" + str(index)) in raw.decode()
                            with lock:
                                counts["verified_clients"] += 1
                    except Exception as exc:  # noqa: BLE001 -- bounded, shape-only failure report
                        if isinstance(exc, HTTPError):
                            exc.close()  # The load client owns rejected responses too.
                        with lock:
                            if len(failures) < 10:
                                failures.append(type(exc).__name__)
                    time.sleep(interval)

            with ThreadPoolExecutor(concurrency) as pool:
                list(pool.map(lambda _: worker(), range(concurrency)))
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=3)
            until = time.monotonic() + 3
            while time.monotonic() < until and (active or any(thread.name == "protoverta-client-lifetime" and thread.ident not in baseline for thread in threading.enumerate())):
                time.sleep(0.01)
    leaked = sum(thread.name == "protoverta-client-lifetime" and thread.ident not in baseline for thread in threading.enumerate())
    resources = monitor.finish() if monitor is not None else None
    if resources is not None:
        resources["cold_baseline"] = cold_resources
        resources["baseline_kind"] = "after explicit 5-second same-workload warmup"
    return {"passed": not failures and active == 0 and opened == closed and leaked == 0 and issued > 0 and (warmup is None or warmup["passed"]) and (resources is None or resources.get("available") and resources.get("policy_passed")),
            "seconds": round(time.monotonic() - started, 3), "issued": issued, "opened": opened, "closed": closed,
            "active_after": active, "peak_active": peak, "lifetime_threads_after": leaked,
            "counts": dict(counts), "failure_types": failures, "local_fixtures_only": True, "resources": resources, "warmup": warmup}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--requests", type=int, default=5000)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--interval", type=float, default=0.02)
    parser.add_argument("--installed", action="store_true")
    parser.add_argument("--measure-resources", action="store_true", help="Requires soak-test extra; fail if process metrics are unavailable or bounded resource policy fails")
    args = parser.parse_args()
    import os

    safe = sanitized_environment()
    os.environ.clear()
    os.environ.update(safe)
    if not args.installed:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    result = run_soak(seconds=args.seconds, requests=args.requests, concurrency=args.concurrency, interval=args.interval, measure_resources=args.measure_resources)
    print(json.dumps(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
