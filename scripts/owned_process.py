"""POSIX owner-death guard for one standalone subprocess group.

[Role]
Spawns a POSIX process group monitored by an inherited owner pipe so parent
exit (including `SIGKILL`) immediately terminates the entire child group.

[Invariants]
- Only the direct caller holds the write end of `owner_pipe`.
- Explicitly detached services run in separate sessions with independent lifetimes.

[Timeouts & Locks]
- Blocks on `os.read(owner_pipe, 1)` until EOF, then sends `SIGKILL` to `-group`.

[Side-Effect Policy]
- Process-group lifecycle supervision only; no filesystem writes.
"""
import errno
import os
import signal
import subprocess
import sys


def main():
    owner, owner_pipe = int(sys.argv[1]), int(sys.argv[2])
    command = sys.argv[3:]
    if not command or os.getppid() != owner or os.getpgrp() != os.getpid():
        return 125
    group = os.getpid()
    if os.fork() == 0:
        for descriptor in (0, 1, 2):
            if descriptor != owner_pipe:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    if exc.errno != errno.EBADF:
                        raise
        try:
            while os.read(owner_pipe, 1):
                pass
        finally:
            os.killpg(group, signal.SIGKILL)
        os._exit(125)
    os.close(owner_pipe)
    try:
        child = subprocess.Popen(command)
    except OSError:
        return 127
    child.wait()
    if child.returncode < 0:
        if -child.returncode not in (signal.SIGKILL, signal.SIGSTOP):
            signal.signal(-child.returncode, signal.SIG_DFL)
        os.kill(os.getpid(), -child.returncode)
    return child.returncode


if __name__ == "__main__":
    raise SystemExit(main())
