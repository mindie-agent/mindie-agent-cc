"""Owned Windows process trees and the native entry lifetime.

[Role]
Creates the Windows Job Object used to own a child tree, assigns a suspended
child before it can spawn descendants, resumes it after assignment, and waits
for a native entry child with the same ownership.

[Invariants]
- A Windows child is created suspended; it is resumed only after Job
  assignment succeeds, so no descendant can escape tree termination.
- A Job is closed even when termination fails, and the original failure (plus
  a close failure note) is never replaced by cleanup success.
- POSIX keeps its existing process-group behavior in `bounded.py`; these
  helpers are imported there and re-exported for compatibility.

[Timeouts & Locks]
- Job ownership is established before the child runs. The native entry waits
  without a business deadline; the shared `bounded` runner supplies deadlines
  for ordinary commands.

[Side-Effect Policy]
- Mutates only the owned child process and its private Job Object. It never
  scans or terminates unrelated system processes.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

POSIX = os.name == "posix"


def _own_windows_tree(process, *, allow_service=False):
    """Keep descendant ownership after the process group leader exits.

    A taskkill PID traversal cannot find a tree whose root has already exited.
    A Windows Job retains that ownership, including inherited pipe holders.
    This launcher is stdlib-only because it also runs before runtime setup.
    """
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
    if allow_service:
        # Only explicit service launchers request CREATE_BREAKAWAY_FROM_JOB.
        # Ordinary children still inherit this Job and remain caller-owned.
        limits.BasicLimitInformation.LimitFlags |= 0x800  # BREAKAWAY_OK
    if (not job or not kernel.SetInformationJobObject(
            job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            or not kernel.AssignProcessToJobObject(job, int(process._handle))):
        error = ctypes.get_last_error()
        if job:
            kernel.CloseHandle(job)
        raise ctypes.WinError(error)
    process._mindie_windows_job = (kernel, job)


def _resume_windows_process(process):
    """Resume only the owned, suspended child after Job assignment.

    NtResumeProcess is also used by psutil's Windows resume implementation.
    The retained Popen handle avoids PID reuse and system-wide thread scans.
    NTSTATUS is converted explicitly; GetLastError is not its error channel.
    """
    import ctypes
    from ctypes import wintypes

    native = ctypes.WinDLL("ntdll")
    resume = native.NtResumeProcess
    resume.argtypes = [wintypes.HANDLE]
    resume.restype = wintypes.LONG
    status = resume(int(process._handle))
    if status < 0:
        convert = native.RtlNtStatusToDosError
        convert.argtypes = [wintypes.LONG]
        convert.restype = wintypes.ULONG
        raise ctypes.WinError(convert(status))


def spawn(command, stdin, env, cwd, *, allow_service=False, operation=None):
    """Start one child with Windows tree ownership established before resume."""
    inherited = (
        POSIX
        and os.environ.get("MINDIE_MAINTENANCE_GROUP") == "1"
        and os.getpgrp() == os.getpid()
    )
    kwargs = dict(
        args=list(command),
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
    )
    if POSIX:
        kwargs["start_new_session"] = not inherited
    else:
        # Assign ownership before the child can create descendants. Attaching
        # a Job to an already-running process races fast child startup.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x4  # CREATE_SUSPENDED
    owner_fds = None
    if POSIX and not inherited and operation is not None and operation[1]:
        owner_read, owner_write = os.pipe()
        owner_fds = owner_read, owner_write
        kwargs["args"] = [
            sys.executable, str(Path(__file__).with_name("owned_process.py")),
            str(os.getpid()), str(owner_read), *command,
        ]
        kwargs["pass_fds"] = (owner_read,)
    try:
        process = subprocess.Popen(**kwargs)
    except BaseException:
        if owner_fds is not None:
            os.close(owner_fds[1])
        raise
    finally:
        if owner_fds is not None:
            os.close(owner_fds[0])
    if owner_fds is not None:
        process._mindie_owner_fd = owner_fds[1]
    process._mindie_inherited_group = inherited
    if not POSIX:
        try:
            _own_windows_tree(process, allow_service=allow_service)
            _resume_windows_process(process)
        except TimeoutError:
            raise
        except OSError:
            # Do not continue an operation without the promised ownership.
            from bounded import _kill_tree
            _kill_tree(process)
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
            raise
    return process


def kill_windows_tree(process):
    """Terminate the owned Job, or fall back to a bounded taskkill traversal."""
    owned = getattr(process, "_mindie_windows_job", None)
    if owned is not None:
        process._mindie_windows_job = None
        kernel, job = owned
        import ctypes
        error = None
        if not kernel.TerminateJobObject(job, 1):
            error = ctypes.WinError(ctypes.get_last_error())
        if not kernel.CloseHandle(job):
            close_error = ctypes.WinError(ctypes.get_last_error())
            if error is None:
                error = close_error
            else:
                error.add_note("Closing the owned Windows Job also failed: " + str(close_error))
        if error is not None:
            raise error
        return
    # Fallback is used only when initial Job assignment failed.
    if process.poll() is None:
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        try:
            subprocess.run(
                [os.path.join(system_root, "System32", "taskkill.exe"),
                 "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            )
        except TimeoutError:
            raise
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()


def wait_windows_entry(command, env, *, own_windows_tree, resume_windows_process, kill_tree):
    """Keep a native entry alive and clean up its owned tree on every failure."""
    process = subprocess.Popen(
        command, env=env,
        stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x4,
    )
    failure = None
    returncode = None
    try:
        own_windows_tree(process, allow_service=True)
        resume_windows_process(process)
        returncode = process.wait()
    except BaseException as exc:
        failure = exc
    for stage, cleanup in (
        ("terminate_owned_tree", lambda: kill_tree(process)),
        ("reap_entry_child", lambda: process.wait(timeout=5)),
    ):
        try:
            cleanup()
        except BaseException as exc:
            if failure is None:
                failure = exc
                if returncode is not None:
                    failure.add_note(f"The entry child already exited with code {returncode}.")
            else:
                failure.add_note(f"Entry cleanup failed at {stage}: {type(exc).__name__}")
    if failure is not None:
        raise failure
    raise SystemExit(returncode)
