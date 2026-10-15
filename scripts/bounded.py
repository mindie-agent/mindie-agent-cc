"""One owned attempt with bounded output; a deadline only when the caller asks.

POSIX reads pipes with a selector and kills the owned process group. Windows
cannot select() process pipes, so it uses two daemon reader threads and an
owned Windows Job. The child starts suspended until Job assignment completes;
cleanup still owns descendants after their leader exits.

When this process is already the leader of an owned process group
(MINDIE_MAINTENANCE_GROUP=1 set by the outer core), POSIX spawns stay in that
group instead of a new session: the outer core owns whole-group terminal
cleanup, so this module only kills the direct child, never the group. The
owner handshake (MINDIE_MAINTENANCE_OWNER=<core pid>) is checked before spawn
and during the wait; a dead owner ends the attempt instead of leaving an
unobserved paid model call running. Owner checks are the lifecycle contract's
substitute for an arbitrary total deadline.

Final cleanup closes a standalone owned tree; managed descendants are closed
by the outer core when the worker returns. Cleanup failures use the caller's
receipt, or an exception preserving completed stdout when no receipt exists.
Stdin is a temporary file, never a blocking pipe write: a child that does not
read cannot hang the parent past the deadline.

An optional cancel Event is checked before spawn, while draining/waiting
(including after the child has closed its pipes), and before returning a
result. An optional terminal predicate over accumulated stdout bytes ends the
read early when the caller's protocol has terminal evidence; the owned tree
is still reaped, and a late nonzero exit is cleanup evidence, never a second
business result. A child that has exited is evidence; silence from an
inherited pipe holder is not. Stderr bytes count toward the output bound and
are then discarded. Updater transport commands may attach a static failure
category; raw stderr, argv and URLs are not retained. This call does not retry.
"""

from __future__ import annotations

import atexit
import math
import os
import re
import subprocess
import tempfile
import threading
import time

POSIX = os.name == "posix"

if POSIX:
    import selectors
    import signal

MAX_OUTPUT = 256 * 1024
_CHUNK = 8192


class CommandCancelled(RuntimeError):
    """The caller cancelled this attempt; owned children are reaped."""


class OwnerExited(RuntimeError):
    """The owning maintenance core exited; its work is not continued."""


class CommandTimedOut(RuntimeError):
    """The command did not finish before the absolute deadline."""

    def __init__(self, message, *args, category=None, retry_after=None):
        super().__init__(message, *args)
        self.category = category
        self.retry_after = retry_after


class OutputLimitExceeded(ValueError, RuntimeError):
    """Captured output grew past the configured byte bound."""


class CommandCleanupError(RuntimeError):
    """Execution ended; cleanup failed without erasing the observed output."""

    def __init__(self, stdout, receipt):
        self.stdout = stdout
        self.operation_completed = True
        self.cleanup_failed = True
        self.cleanup = receipt.get("cleanup", [])
        stages = ", ".join(item["stage"] for item in self.cleanup)
        super().__init__("command execution completed but owned-process cleanup failed"
                         + (f" ({stages})" if stages else "") + "; do not repeat the operation")


def _maintenance_owner():
    """The owning core's pid when this process is an owned maintenance worker.

    Present only under the outer core's maintenance-group contract; a malformed
    owner value fails closed rather than disabling the liveness check.
    """
    if os.environ.get("MINDIE_MAINTENANCE_GROUP") != "1":
        return None
    owner = os.environ.get("MINDIE_MAINTENANCE_OWNER")
    if owner is None:
        return None
    if not owner.isdecimal():
        raise OwnerExited("maintenance owner identity is invalid")
    return int(owner)


def _owner_exited(owner) -> bool:
    # getppid changes once the owning core is gone (POSIX). On Windows the
    # value can go stale; the owning Job still bounds the tree there.
    return owner is not None and os.getppid() != owner


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


def _spawn(command, stdin, env, cwd, *, allow_service=False):
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
    process = subprocess.Popen(**kwargs)
    process._mindie_inherited_group = inherited
    if not POSIX:
        try:
            _own_windows_tree(process, allow_service=allow_service)
            _resume_windows_process(process)
        except OSError:
            # Do not continue an operation without the promised ownership.
            _kill_tree(process)
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
            raise
    return process


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


def _kill_tree(process, pgid=None):
    if POSIX:
        if getattr(process, "_mindie_tree_terminated", False):
            # A repeated signal can hit a reaped/reused group or macOS zombies
            # after the first successful kill; that repeat is not a failure.
            return
        if getattr(process, "_mindie_inherited_group", False):
            # Killing the shared group here would kill this worker before
            # it can report its result. Core owns the entire group on exit.
            if process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            process._mindie_tree_terminated = True
            return
        try:
            os.killpg(pgid if pgid is not None else process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # Other OSErrors (for example a stale macOS group PermissionError)
        # reach the caller's cleanup receipt; they are never retried here.
        process._mindie_tree_terminated = True
        return
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
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()


class _Cap:
    def __init__(self, max_output):
        self.max_output = max_output
        self.size = 0

    def add(self, chunk):
        self.size += len(chunk)
        if self.size > self.max_output:
            raise OutputLimitExceeded("output exceeds the bound")


def _cancelled(cancel):
    return cancel is not None and cancel.is_set()


_RETRY_AFTER = re.compile(r"retry-after\s*[:=]\s*(\d{1,6})\b", re.I)
_HTTP_STATUS = re.compile(
    r"(?:http\s*/\s*1\.[01]\s+|status(?:\s+code)?\s*[:=]\s*|"
    r"error\s*[:=]\s*|returned error:\s*|http error\s+|http\s+)(\d{3})\b",
    re.I,
)
_CERT_PHRASES = (
    "certificate verify failed",
    "sslcertverificationerror",
    "certificate has expired",
    "self-signed certificate",
    "self signed certificate",
    "unable to get local issuer certificate",
    "certificate_verify_failed",
    "ssl: certificate",
    "ssl certificate problem",
    "unknown ca",
    "curl: (60)",
)
_RESOLVER_PHRASES = (
    "resolutionimpossible",
    "conflicting dependencies",
    "package versions have conflicting",
    "the conflict is caused by",
    "resolver conflict",
)
_HASH_PHRASES = (
    "these packages do not match the hashes",
    "does not match the hashes",
    "hash mismatch",
)
_GENERIC_CONTENT_PHRASES = (
    "no matching distribution",
    "could not find a version that satisfies",
)
_AUTH_PHRASES = (
    "authentication failed",
    "could not read username",
    "terminal prompts disabled",
    "permission denied (publickey)",
    "invalid credentials",
    "http basic: access denied",
    "support for password authentication was removed",
    "authentication required",
    "invalid username or password",
)
_RATE_PHRASES = (
    "rate limit",
    "too many requests",
    "secondary rate limit",
)
_HOOK_PHRASES = (
    "hook trust",
    "requires native trust",
    "changed hooks require",
)
_CONNECT_PHRASES = (
    "could not resolve host",
    "temporary failure in name resolution",
    "name or service not known",
    "nodename nor servname",
    "temporary failure resolving",
    "network is unreachable",
    "connection timed out",
    "connection reset by peer",
    "connection refused",
    "connection aborted",
    "failed to connect",
    "operation timed out",
    "read operation timed out",
    "timed out",
    "curl: (6)",
    "curl: (7)",
    "curl: (28)",
    "curl: (56)",
    "newconnectionerror",
    "remote end closed connection",
    "unexpected eof",
    "connection broken",
    "bad gateway",
    "service unavailable",
    "gateway time-out",
    "gateway timeout",
    "error sending request",
    "dns error",
    "recv failure",
    "send failure",
    "max retries exceeded",
    "proxy connect aborted",
)


def _has_phrase(text, phrases):
    return any(phrase in text for phrase in phrases)


def classify_transport_text(text):
    """Return (category, retry_after_seconds) or None.

    `text` is command output examined only in memory. The result is a static
    category and an optional Retry-After. It never includes the output.
    Exit status alone is not a category: 128 and a bare 403 are not enough.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    sample = text[:65536]
    lower = sample.lower()
    retry_after = None
    match = _RETRY_AFTER.search(sample)
    if match:
        retry_after = _finite_delay(match.group(1))
    codes = {int(item) for item in _HTTP_STATUS.findall(sample)}
    if _has_phrase(lower, _CERT_PHRASES):
        return ("certificate", None)
    if _has_phrase(lower, _HASH_PHRASES):
        return ("bad_content", None)
    if _has_phrase(lower, _RESOLVER_PHRASES):
        return ("resolver", None)
    if 429 in codes or _has_phrase(lower, _RATE_PHRASES):
        return ("rate_limited", retry_after)
    if 401 in codes or _has_phrase(lower, _AUTH_PHRASES):
        return ("authentication", None)
    if 403 in codes or (
        ("access denied" in lower and "http basic" not in lower)
        or "write access to repository not granted" in lower
        or ("forbidden" in lower and "403" in lower)
        or ("permission denied" in lower and "publickey" not in lower)
    ):
        return ("permission", None)
    if _has_phrase(lower, _HOOK_PHRASES):
        return ("hook_trust", None)
    # A demonstrated transport failure wins over pip's generic final summary.
    if (codes & {500, 502, 503, 504}) or _has_phrase(lower, _CONNECT_PHRASES):
        return ("temporary_network", retry_after)
    if _has_phrase(lower, _GENERIC_CONTENT_PHRASES):
        return ("bad_content", None)
    return None


def _finite_delay(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number == float("inf") or number == float("-inf") or number < 0:
        return None
    return number


_STREAM_EDGE = 4096


def _stream_edges(buf):
    """Head and tail of one stream. The raw bytes are not retained."""
    if not buf:
        return ""
    if isinstance(buf, str):
        text = buf
    else:
        text = bytes(buf).decode("utf-8", "replace")
    if len(text) <= _STREAM_EDGE * 2:
        return text
    return text[:_STREAM_EDGE] + "\n" + text[-_STREAM_EDGE:]


def _joined_output(stdout, stderr):
    parts = []
    for buf in (stdout, stderr):
        piece = _stream_edges(buf)
        if piece:
            parts.append(piece)
    return "\n".join(parts)


def _attach_transport(exc, stdout, stderr, *, timed_out):
    kind = classify_transport_text(_joined_output(stdout, stderr))
    if kind is not None:
        category, retry_after = kind
        exc.category = category
        if retry_after is not None:
            exc.retry_after = retry_after
        return
    if timed_out:
        exc.category = "temporary_network"


def _cleanup_step(receipt, operation, stage):
    try:
        operation()
        return False
    except Exception as exc:
        receipt["cleanup_failed"] = True
        receipt.setdefault("cleanup", []).append(dict(stage=stage, error_type=type(exc).__name__))
        return True


def _run_posix(process, timeout, max_output, pgid, check, cancel, transport=False, owner=None, receipt=None, terminal=None):
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "out")
    selector.register(process.stderr, selectors.EVENT_READ, "err")
    deadline = None if timeout is None else time.monotonic() + timeout
    output = bytearray()
    errors = bytearray() if transport else None
    cap = _Cap(max_output)
    terminated = False

    def timeout_error():
        err = TimeoutError("deadline exceeded")
        if transport:
            _attach_transport(err, output, errors, timed_out=True)
        return err

    def failed_error():
        err = RuntimeError(f"command failed ({process.returncode})")
        err.mindie_output = bytes(output).decode("utf-8", "replace")
        err.returncode = process.returncode
        if transport:
            _attach_transport(err, output, errors, timed_out=False)
        return err

    try:
        while True:
            if _cancelled(cancel):
                raise CommandCancelled("command cancelled")
            if _owner_exited(owner):
                raise OwnerExited("maintenance owner exited")
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise timeout_error()
            alive = process.poll() is None
            if not alive and not selector.get_map():
                break
            if selector.get_map():
                events = selector.select(0.05 if remaining is None else min(0.05, remaining))
                if not events and not alive:
                    # The child exited; silence from an inherited pipe holder
                    # is not evidence of remaining work.
                    break
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), _CHUNK)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    cap.add(chunk)
                    if key.data == "out":
                        output.extend(chunk)
                        if terminal is not None and terminal(bytes(output)):
                            # Terminal evidence closes the business operation;
                            # reaping below has its own small cleanup wait.
                            terminated = True
                    elif errors is not None:
                        errors.extend(chunk)
                if terminated:
                    break
                continue
            # Pipes closed while the child is still running.
            try:
                process.wait(timeout=0.05 if remaining is None else min(0.05, remaining))
            except subprocess.TimeoutExpired:
                continue
            break
        if _cancelled(cancel):
            raise CommandCancelled("command cancelled")
        if _owner_exited(owner):
            raise OwnerExited("maintenance owner exited")
        if process.poll() is None:
            if terminated:
                # Terminal evidence closed the operation; the finally below
                # owns and reaps the tree instead of waiting on the child.
                pass
            elif deadline is None:
                # The loop above only exits on observed termination, so this
                # returns at once; it is a reap, never a business deadline.
                process.wait()
            else:
                try:
                    process.wait(timeout=max(0.01, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    err = timeout_error()
                    if transport:
                        raise err from None
                    raise err
        if _cancelled(cancel):
            raise CommandCancelled("command cancelled")
        if not terminated and check and process.returncode:
            raise failed_error()
        if terminated and process.returncode:
            # The terminal envelope was already delivered; a late nonzero exit
            # is cleanup evidence, never a second business result.
            if receipt is not None:
                receipt["cleanup_failed"] = True
        return output.decode("utf-8", "replace")
    finally:
        # Cleanup never masks the business result; failures land on the receipt.
        _cleanup_step(receipt, lambda: _kill_tree(process, pgid), "terminate_owned_tree")
        def reap():
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                _kill_tree(process, pgid)
                process.wait(timeout=1)
        _cleanup_step(receipt, reap, "reap_process")
        _cleanup_step(receipt, selector.close, "close_selector")
        _cleanup_step(receipt, process.stdout.close, "close_stdout")
        _cleanup_step(receipt, process.stderr.close, "close_stderr")


def _run_windows(process, timeout, max_output, pgid, check, cancel, transport=False, owner=None, receipt=None, terminal=None):
    # Windows reader threads drain the pipes; the Job owns all pipe holders.
    deadline = None if timeout is None else time.monotonic() + timeout
    output = bytearray()
    errors = bytearray() if transport else None
    cap = _Cap(max_output)
    lock = threading.Lock()
    failure = []
    terminated = False

    def reader(stream, dest):
        try:
            while True:
                chunk = stream.read1(_CHUNK)
                if not chunk:
                    return
                with lock:
                    cap.add(chunk)
                    if dest is not None:
                        dest.extend(chunk)
        except (OSError, ValueError) as exc:
            failure.append(exc)

    threads = [
        threading.Thread(target=reader, args=(process.stdout, output), daemon=True),
        threading.Thread(target=reader, args=(process.stderr, errors), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        drain_until = None
        while any(thread.is_alive() for thread in threads) or process.poll() is None:
            if _cancelled(cancel):
                raise CommandCancelled("command cancelled")
            if _owner_exited(owner):
                raise OwnerExited("maintenance owner exited")
            if deadline is not None and time.monotonic() >= deadline:
                err = TimeoutError("deadline exceeded")
                if transport:
                    _attach_transport(err, output, errors, timed_out=True)
                raise err
            if failure:
                raise failure[0]
            with lock:
                snapshot = bytes(output)
            if terminal is not None and snapshot and terminal(snapshot):
                terminated = True
                break
            if process.poll() is not None:
                if not any(thread.is_alive() for thread in threads):
                    break
                # The child exited; an inherited pipe holder is not remaining
                # work. One short drain window, then the operation closes.
                if drain_until is None:
                    drain_until = time.monotonic() + 0.2
                elif time.monotonic() >= drain_until:
                    break
            time.sleep(0.02)
        if failure:
            raise failure[0]
        # A fast native child can finish between samples. Its final buffered
        # envelope still owns the outcome, including a reported error + exit 1.
        with lock:
            snapshot = bytes(output)
        if terminal is not None and snapshot and terminal(snapshot):
            terminated = True
        if process.poll() is None and not terminated:
            process.wait(None if deadline is None else max(0.01, deadline - time.monotonic()))
        if _cancelled(cancel):
            raise CommandCancelled("command cancelled")
        if _owner_exited(owner):
            raise OwnerExited("maintenance owner exited")
        if not terminated and check and process.returncode:
            err = RuntimeError(f"command failed ({process.returncode})")
            err.mindie_output = bytes(output).decode("utf-8", "replace")
            err.returncode = process.returncode
            if transport:
                _attach_transport(err, output, errors, timed_out=False)
            raise err
        if terminated and process.returncode:
            if receipt is not None:
                receipt["cleanup_failed"] = True
        return bytes(output).decode("utf-8", "replace")
    finally:
        # Cleanup never masks the business result; failures land on the receipt.
        _cleanup_step(receipt, lambda: _kill_tree(process, pgid), "terminate_owned_tree")
        _cleanup_step(receipt, lambda: process.wait(timeout=1), "reap_process")
        for thread in threads:
            _cleanup_step(receipt, lambda thread=thread: thread.join(timeout=1), "join_reader")
        for thread, stream in zip(threads, (process.stdout, process.stderr)):
            if thread.is_alive():
                receipt["cleanup_failed"] = True
                receipt.setdefault("cleanup", []).append(dict(stage="join_reader", error_type="ReaderStillRunning"))
                # Closing a BufferedReader held by a blocked reader would
                # block this cleanup indefinitely after an ownership failure.
                continue
            _cleanup_step(receipt, stream.close, "close_pipe")


def run(
    argv,
    stdin="",
    *,
    timeout=None,
    env=None,
    cwd=None,
    max_output=MAX_OUTPUT,
    check=True,
    cancel=None,
    transport=False,
    receipt=None,
    terminal=None,
    allow_service=False,
):
    if (not isinstance(argv, (list, tuple)) or not argv
            or not isinstance(argv[0], str) or not argv[0]
            or not all(isinstance(item, str) for item in argv)):
        raise ValueError("command must be a nonempty argv list")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("an explicit execution timeout must be a positive finite number")
    if terminal is not None and not callable(terminal):
        raise ValueError("terminal must be a predicate over accumulated stdout bytes")
    explicit_receipt = receipt is not None
    receipt = receipt if explicit_receipt else {}
    receipt["started"] = False
    receipt["cleanup_failed"] = False
    receipt["cleanup"] = []
    if _cancelled(cancel):
        raise CommandCancelled("command cancelled")
    owner = _maintenance_owner()
    if _owner_exited(owner):
        # Never start an unobserved (possibly billed) child for a dead owner.
        raise OwnerExited("maintenance owner exited")
    data = stdin.encode() if isinstance(stdin, str) else (stdin or b"")
    with tempfile.TemporaryFile() as stream:
        if data:
            stream.write(data)
            stream.seek(0)
        process = _spawn(argv, stream, env, cwd, allow_service=allow_service)
        if receipt is not None:
            receipt["started"] = True
        pgid = None
        if POSIX:
            try:
                pgid = os.getpgid(process.pid)
            except OSError:
                pgid = process.pid

        def cleanup():
            try:
                _kill_tree(process, pgid)
            except OSError:
                # Interpreter-exit best effort has no receipt channel.
                pass

        atexit.register(cleanup)
        try:
            if POSIX:
                result = _run_posix(
                    process, timeout, max_output, pgid, check, cancel, transport,
                    owner=owner, receipt=receipt, terminal=terminal,
                )
            else:
                result = _run_windows(
                    process, timeout, max_output, pgid, check, cancel, transport,
                    owner=owner, receipt=receipt, terminal=terminal,
                )
            if _cancelled(cancel):
                raise CommandCancelled("command cancelled")
            if receipt["cleanup_failed"] and not explicit_receipt:
                raise CommandCleanupError(result, receipt)
            return result
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            if timeout is None:
                exc.cleanup_failed = receipt["cleanup_failed"]
                exc.cleanup = receipt["cleanup"]
                raise
            message = f"command timed out after {timeout}s"
            category = getattr(exc, "category", None)
            retry_after = getattr(exc, "retry_after", None)
            if transport and category is None:
                category = "temporary_network"
            if category:
                failure = CommandTimedOut(
                    message, category=category, retry_after=retry_after
                )
            else:
                failure = CommandTimedOut(message)
            failure.cleanup_failed = receipt["cleanup_failed"]
            failure.cleanup = receipt["cleanup"]
            raise failure from exc
        except BaseException as exc:
            exc.cleanup_failed = receipt["cleanup_failed"]
            exc.cleanup = receipt["cleanup"]
            raise
        finally:
            try:
                atexit.unregister(cleanup)
            except Exception:
                pass
