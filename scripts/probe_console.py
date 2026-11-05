"""Owned hidden Windows console controller for synthetic lifecycle fixtures only.

Never attach to the user's console. Launched with CREATE_NEW_CONSOLE inside
an outer probe job; the wrapper tree has its own nested kill-on-close job.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from windows_probe_job import windows_probe_process


def console_members(kernel):
    from ctypes import wintypes

    buffer = (wintypes.DWORD * 128)()
    count = kernel.GetConsoleProcessList(buffer, 128)
    if not 0 < count <= 128:
        raise RuntimeError("Console membership unavailable or truncated")
    return set(buffer[:count])


def wait_file(path, process, seconds):
    deadline = time.monotonic() + seconds
    while not path.exists():
        if process.poll() is not None or time.monotonic() >= deadline:
            return False
        time.sleep(0.025)
    return True


def console_is_owned(members, job_members, controller, wrapper):
    return controller in members and wrapper in members and controller not in job_members and members <= job_members | {controller}


def run(bundle):
    from ctypes import wintypes

    root = Path(bundle["cwd"]).resolve()
    ready, disconnected = root / "parent-exit-ready", root / "child-disconnected"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    kernel.SetConsoleCtrlHandler.argtypes = [handler_type, wintypes.BOOL]
    kernel.SetConsoleCtrlHandler.restype = wintypes.BOOL
    kernel.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.GenerateConsoleCtrlEvent.restype = wintypes.BOOL
    kernel.GetConsoleProcessList.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
    kernel.GetConsoleProcessList.restype = wintypes.DWORD

    @handler_type
    def keep_controller_alive(event):
        return event == 0

    if not kernel.SetConsoleCtrlHandler(keep_controller_alive, True):
        raise ctypes.WinError(ctypes.get_last_error())
    report = {"event_sent": False, "console_owned": False, "timed_out": False, "upstream_closed_before_cleanup": False, "remaining_before_cleanup": None}
    with windows_probe_process(
        bundle["args"],
        cwd=str(root),
        env=bundle["env"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as (process, job):
        process.stdin.close()
        process.stdin = None
        if bundle["mode"] == "interrupt":
            if not wait_file(ready, process, 15):
                raise RuntimeError("Fixture did not reach the interrupt boundary")
            members = console_members(kernel)
            if not console_is_owned(members, job.process_ids(), os.getpid(), process.pid) or process.poll() is not None:
                raise RuntimeError("Refusing to signal a console containing unowned processes")
            report["console_owned"] = True
            if not kernel.GenerateConsoleCtrlEvent(0, 0):  # CTRL_C_EVENT, own NEW console only.
                raise ctypes.WinError(ctypes.get_last_error())
            report["event_sent"] = True
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            report["timed_out"] = True
            report["upstream_closed_before_cleanup"] = disconnected.exists()
            report["remaining_before_cleanup"] = len(job.process_ids())
            job.terminate()
            stdout, stderr = process.communicate(timeout=5)
        else:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and (job.process_ids() or not disconnected.exists()):
                time.sleep(0.025)
            report["upstream_closed_before_cleanup"] = disconnected.exists()
            report["remaining_before_cleanup"] = len(job.process_ids())
        report["returncode"] = process.returncode
        # Snapshot BEFORE context exit closes the protective job.
        (root / "console-result.json").write_text(json.dumps(report), encoding="utf-8")
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    return process.returncode if not report["timed_out"] else 124


def main():
    if os.name != "nt":
        raise RuntimeError("Windows console lifecycle fixture only")
    bundle = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    return run(bundle)


if __name__ == "__main__":
    raise SystemExit(main())
