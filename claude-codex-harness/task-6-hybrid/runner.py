"""Run one vetted script and capture its output, bounded in time and size.

The caller supplies the command (an isolated interpreter and the script), a minimal
environment and the working folder; stdin is closed. On Windows the script runs inside a
job object, and when it exits, times out or fails, the whole job is ended: nothing the
script started outlives it or holds its output pipes open. (subprocess.run cannot do
this: after a timeout it kills only the script, then waits with no time limit for pipes
that a leftover process may hold.) Output beyond the cap is read and counted, not kept.

Not closed: a process started in the moment before the script joins its job escapes it.
Windows has no public way to start a process suspended through subprocess.
"""

import os
import subprocess
import threading
import time

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
READ_CHUNK = 65536
JOIN_SECONDS = 5  # after the process tree has ended, how long the output readers may take


class _Capture:
    """Reads one pipe to its end on a thread, keeping at most `cap` bytes."""

    def __init__(self, stream, cap):
        self.data, self.total, self.cap = bytearray(), 0, cap
        self.thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self.thread.start()

    def _read(self, stream):
        try:
            while True:
                chunk = stream.read1(READ_CHUNK)
                if not chunk:
                    return
                self.total += len(chunk)
                room = self.cap - len(self.data)
                if room > 0:
                    self.data += chunk[:room]
        except (OSError, ValueError):
            return

    @property
    def truncated(self):
        return self.total > len(self.data)


def run(command, *, cwd, env, timeout, max_bytes):
    """Run `command` and return a dict:
      exit_code        None if it was ended on timeout
      timed_out, duration_ms
      stdout, stderr   bytes, at most max_bytes each; *_truncated when there was more
      job              a job object held the process tree (Windows)
      left_running     names of processes the script started that were still running when
                       it ended; the job ended them ([] none; None without a job)
      pipes_held       an output pipe was still open at the end: something outside the job
                       holds it, so the output may be incomplete
    """
    started = time.monotonic()
    proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=CREATE_NO_WINDOW)
    job = _job_for(proc.pid)
    out, err = _Capture(proc.stdout, max_bytes), _Capture(proc.stderr, max_bytes)
    timed_out, code = False, None
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        left_running = _end(proc, job)
    duration = round((time.monotonic() - started) * 1000)
    for capture in (out, err):
        capture.thread.join(JOIN_SECONDS)
    held = any(capture.thread.is_alive() for capture in (out, err))
    if not held:  # closing a pipe a reader still blocks on would wait for that reader
        proc.stdout.close()
        proc.stderr.close()
    return {"exit_code": None if timed_out else code, "timed_out": timed_out,
            "duration_ms": duration, "stdout": bytes(out.data), "stderr": bytes(err.data),
            "stdout_truncated": out.truncated, "stderr_truncated": err.truncated,
            "job": job is not None, "left_running": left_running, "pipes_held": held}


def _end(proc, job):
    """End the script and everything it started. Returns the names of the processes the
    script left running, which the job then ended (None without a job)."""
    if job is None:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return None
    try:
        left = _leftovers(job, proc.pid)
        _k32.TerminateJobObject(job, 1)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return left
    finally:
        _k32.CloseHandle(job)


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD]
    _k32.QueryInformationJobObject.restype = wintypes.BOOL
    _k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                               wintypes.DWORD, ctypes.c_void_p]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.TerminateJobObject.restype = wintypes.BOOL
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]

    PROCESS_SET_QUOTA, PROCESS_TERMINATE, PROCESS_QUERY_LIMITED = 0x0100, 0x0001, 0x1000
    JOB_PROCESS_IDS, JOB_EXTENDED_LIMITS = 3, 9
    KILL_ON_JOB_CLOSE = 0x2000  # the harness dying also ends the job
    MAX_LISTED = 64
    # Windows gives a console program started without a window its own console host,
    # which joins the job. It is not something the script started.
    CONSOLE_HOST = "conhost.exe"

    class _BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimits),
                    ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _ProcessIds(ctypes.Structure):
        _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD),
                    ("NumberOfProcessIdsInList", wintypes.DWORD),
                    ("ProcessIdList", ctypes.c_size_t * MAX_LISTED)]

    def _job_for(pid):
        """A kill-on-close job object holding the process, or None if that failed."""
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            return None
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE
        process = _k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        ok = bool(process) \
            and _k32.SetInformationJobObject(job, JOB_EXTENDED_LIMITS, ctypes.byref(limits),
                                             ctypes.sizeof(limits)) \
            and _k32.AssignProcessToJobObject(job, process)
        if process:
            _k32.CloseHandle(process)
        if not ok:
            _k32.CloseHandle(job)
            return None
        return job

    def _leftovers(job, script_pid):
        """Image names of the job's running processes other than the script and its
        console host; None if the job could not be queried."""
        ids = _ProcessIds()
        if not _k32.QueryInformationJobObject(job, JOB_PROCESS_IDS, ctypes.byref(ids),
                                              ctypes.sizeof(ids), None):
            return None
        names = []
        for pid in list(ids.ProcessIdList)[:ids.NumberOfProcessIdsInList]:
            if pid == script_pid:
                continue
            name = "?"
            handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
            if handle:
                buffer, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
                if _k32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    name = os.path.basename(buffer.value)
                _k32.CloseHandle(handle)
            if name.lower() != CONSOLE_HOST:
                names.append(name)
        return names
else:
    def _job_for(pid):
        return None
