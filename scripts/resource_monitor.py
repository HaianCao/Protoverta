"""Optional process resource sampling for local-only soak diagnostics."""

import threading
import time
from collections import deque


class ResourceMonitor:
    def __init__(self):
        import psutil

        self.process = psutil.Process()
        self.started = time.monotonic()
        self.samples = deque(maxlen=605)
        self.first = self.last = self.warm = None
        self.peaks = {}
        self.sample_count = 0
        self.errors = []
        self.stop = threading.Event()
        self.sample()
        self.thread = threading.Thread(target=self._run, name="protoverta-resource-sampler", daemon=True)
        self.thread.start()

    def sample(self):
        try:
            process = self.process
            count = process.num_handles() if hasattr(process, "num_handles") else process.num_fds()
            value = {
                "seconds": round(time.monotonic() - self.started, 3),
                "rss_bytes": process.memory_info().rss,
                "threads": process.num_threads(),
                "handles_or_fds": count,
                "tcp_connections": len(process.net_connections(kind="tcp")),
            }
            self.samples.append(value)
            self.sample_count += 1
            if self.first is None:
                self.first = value
            self.last = value
            if self.warm is None and value["seconds"] >= 30:
                self.warm = value
            for key in ("rss_bytes", "threads", "handles_or_fds", "tcp_connections"):
                self.peaks[key] = max(value[key], self.peaks.get(key, value[key]))
        except Exception as exc:  # noqa: BLE001 -- fixed shape only, never OS detail/path
            if type(exc).__name__ not in self.errors:
                self.errors.append(type(exc).__name__)

    def _run(self):
        while not self.stop.wait(1):
            self.sample()

    def finish(self):
        self.stop.set()
        self.thread.join(5)
        if self.thread.is_alive():
            return {"available": False, "errors": self.errors, "sampler_stopped": False}
        self.sample()
        if self.errors or self.sample_count < 2:
            return {"available": False, "errors": self.errors, "sampler_stopped": not self.thread.is_alive()}
        first, last = self.first, self.last
        warm = self.warm if last["seconds"] >= 90 else None
        warm = warm or next((item for item in self.samples if item["seconds"] >= min(30, last["seconds"] / 3)), first)
        return {
            "available": True,
            "baseline": first,
            "after": last,
            "peak": self.peaks.copy(),
            "warm_baseline": warm,
            "warm_rss_growth_bytes": last["rss_bytes"] - warm["rss_bytes"],
            "sample_count": self.sample_count,
            "retained_sample_count": len(self.samples),
            "sample_capacity": self.samples.maxlen,
            "retained_thread_delta": last["threads"] - first["threads"],
            "retained_handle_delta": last["handles_or_fds"] - first["handles_or_fds"],
            "retained_tcp_delta": last["tcp_connections"] - first["tcp_connections"],
            "policy_passed": last["rss_bytes"] - warm["rss_bytes"] <= 64 * 1024 * 1024
            and last["threads"] - first["threads"] <= 2
            and last["handles_or_fds"] - first["handles_or_fds"] <= 4
            and last["tcp_connections"] <= first["tcp_connections"],
            "policy": "warm RSS growth <=64 MiB; retained threads <=2, handles/fds <=4, TCP <=baseline; bounded-run observation only",
        }
