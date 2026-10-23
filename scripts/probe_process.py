"""Bounded test-client execution with process-tree cleanup on timeout."""

from __future__ import annotations

import os
import signal
import subprocess


def run_process(args, **kwargs):
    timeout = kwargs.pop("timeout")
    input_data = kwargs.pop("input", "")
    kwargs.pop("capture_output", None)
    kwargs.pop("check", None)
    with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=os.name != "nt", **kwargs) as process:
        try:
            stdout, stderr = process.communicate(input_data, timeout=timeout)
        except subprocess.TimeoutExpired:
            # PID is the exact process we just created, never a discovered or
            # name-based target. Kill descendants before the wrapper disappears.
            if os.name == "nt":
                with subprocess.Popen(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as killer:
                    killer.wait(timeout=10)
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
            stdout, stderr = process.communicate(timeout=10)
            return subprocess.CompletedProcess(args, 124, stdout, stderr + "\nIsolated client timed out; its process tree was terminated.\n")
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
