"""Contain only newly created Windows probe processes and their descendants.

Assign the suspended child to a kill-on-close job before it can spawn anything.
No breakaway flags, privilege changes, process discovery or name-based kills.
"""

from __future__ import annotations

import ctypes
import subprocess
from contextlib import contextmanager
from unittest.mock import patch


class WindowsProbeJob:
    def __init__(self):
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong), ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t), ("maximum_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD), ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

        class IOCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_operations", "write_operations", "other_operations", "read_bytes", "write_bytes", "other_bytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IOCounters), ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, args, result in (
            ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            ("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
            ("ResumeThread", [wintypes.HANDLE], wintypes.DWORD),
        ):
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def assign(self, process_handle):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process_handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def resume(self, thread):
        if self.kernel.ResumeThread(thread) == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self):
        if not self.kernel.TerminateJobObject(self.handle, 124):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            if not self.kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def windows_probe_process(args, **kwargs):
    import _winapi

    flags = kwargs.pop("creationflags", 0)
    if flags & 0x01000000:  # CREATE_BREAKAWAY_FROM_JOB
        raise ValueError("isolated probes cannot break away from an existing job")
    job = WindowsProbeJob()
    thread = None
    process = None
    original_create = _winapi.CreateProcess

    def create(*args, **kwargs):
        nonlocal thread
        handles = original_create(*args, **kwargs)
        process_handle, thread_handle, _pid, _tid = handles
        try:
            job.assign(process_handle)
            current = _winapi.GetCurrentProcess()
            thread = _winapi.DuplicateHandle(current, thread_handle, current, 0, False, _winapi.DUPLICATE_SAME_ACCESS)
        except BaseException:
            _winapi.TerminateProcess(process_handle, 124)
            _winapi.CloseHandle(thread_handle)
            _winapi.CloseHandle(process_handle)
            raise
        return handles

    try:
        with patch.object(_winapi, "CreateProcess", create):
            process = subprocess.Popen(args, creationflags=flags | 0x00000004, **kwargs)  # CREATE_SUSPENDED
        with process:
            try:
                job.resume(thread)
                _winapi.CloseHandle(thread)
                thread = None
                yield process, job
            finally:
                job.close()  # Before closing pipes/waiting on the parent.
    finally:
        if thread is not None:
            _winapi.CloseHandle(thread)
        job.close()
