"""Bounded test-client execution with process-tree cleanup on timeout."""

from __future__ import annotations

import os
import signal
import subprocess
from contextlib import contextmanager


@contextmanager
def _start(args, **kwargs):
    if os.name == "nt":
        from windows_probe_job import windows_probe_process

        with windows_probe_process(args, **kwargs) as started:
            yield started
    else:
        with subprocess.Popen(args, start_new_session=True, **kwargs) as process:
            yield process, None


def run_process(args, **kwargs):
    timeout = kwargs.pop("timeout")
    input_data = kwargs.pop("input", "")
    kwargs.pop("capture_output", None)
    kwargs.pop("check", None)
    with _start(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs) as (process, job):
        try:
            stdout, stderr = process.communicate(input_data, timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill only the job/group created for this probe, before draining
            # inherited pipes. Never discover or kill unrelated CLI processes.
            if job is not None:
                job.terminate()
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
