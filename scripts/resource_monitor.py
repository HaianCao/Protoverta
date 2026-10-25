"""Optional process resource sampling for local-only soak diagnostics."""

import threading
import time


class ResourceMonitor:
    def __init__(self):
        import psutil

        self.process = psutil.Process()
        self.started = time.monotonic()
        self.samples = []
        self.errors = []
        self.stop = threading.Event()
        self.sample()
        self.thread = threading.Thread(target=self._run, name="protoverta-resource-sampler", daemon=True)
        self.thread.start()

    def sample(self):
        try:
            process = self.process
            count = process.num_handles() if hasattr(process, "num_handles") else process.num_fds()
            value = {"seconds": round(time.monotonic() - self.started, 3), "rss_bytes": process.memory_info().rss,
                     "threads": process.num_threads(), "handles_or_fds": count,
                     "tcp_connections": len(process.net_connections(kind="tcp"))}
            if len(self.samples) < 605:
                self.samples.append(value)
        except Exception as exc:  # noqa: BLE001 -- fixed shape only, never OS detail/path
            if type(exc).__name__ not in self.errors:
                self.errors.append(type(exc).__name__)

    def _run(self):
        while not self.stop.wait(1):
            self.sample()

    def finish(self):
        self.stop.set()
        self.thread.join(5)
        self.sample()
        if self.errors or len(self.samples) < 2 or self.thread.is_alive():
            return {"available": False, "errors": self.errors, "sampler_stopped": not self.thread.is_alive()}
        first, last = self.samples[0], self.samples[-1]
        warm = next((item for item in self.samples if item["seconds"] >= min(30, last["seconds"] / 3)), first)
        return {"available": True, "baseline": first, "after": last,
                "peak": {key: max(item[key] for item in self.samples) for key in ("rss_bytes", "threads", "handles_or_fds", "tcp_connections")},
                "warm_rss_growth_bytes": last["rss_bytes"] - warm["rss_bytes"], "sample_count": len(self.samples),
                "retained_thread_delta": last["threads"] - first["threads"], "retained_handle_delta": last["handles_or_fds"] - first["handles_or_fds"],
                "retained_tcp_delta": last["tcp_connections"] - first["tcp_connections"],
                "policy_passed": last["rss_bytes"] - warm["rss_bytes"] <= 64 * 1024 * 1024 and last["threads"] - first["threads"] <= 2 and last["handles_or_fds"] - first["handles_or_fds"] <= 4 and last["tcp_connections"] <= first["tcp_connections"],
                "policy": "warm RSS growth <=64 MiB; retained threads <=2, handles/fds <=4, TCP <=baseline; bounded-run observation only"}
