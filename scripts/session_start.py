#!/usr/bin/env python3
"""Stdlib SessionStart trampoline: start the shared clock, then run CPython 3.13.

[Role]
Cold SessionStart entry before `bootstrap.py`. Safe on host Python 3.9+.

[Invariants]
- Stdlib only: never imports `tomllib`, `product_contract`, or `mindie_launch`.
- Sets `MINDIE_SESSION_START_DEADLINE_UNIX` before `uv --python 3.13`.
- Inner CPython 3.13 rebases that wall deadline onto local monotonic time.
  macOS 3.9 `time.monotonic()` (`mach_absolute_time`) is not comparable to
  3.13 (`CLOCK_MONOTONIC`) after `execve`.

[Timeouts & Locks]
- Deadline is `BOOTSTRAP_TOTAL + UV_PYTHON_PROVISION` from this process start
  so interpreter fetch is inside the shared clock.

[Side-Effect Policy]
- Re-execs `uv --python 3.13` onto this file, then `runpy` into `bootstrap.py`.
"""
from __future__ import annotations

import os
import runpy
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bounded
from paths import which_host_binary

DEADLINE_ENV = "MINDIE_SESSION_START_DEADLINE_MONOTONIC"
DEADLINE_UNIX_ENV = "MINDIE_SESSION_START_DEADLINE_UNIX"
INNER_ENV = "MINDIE_SESSION_START_INNER"
BOOTSTRAP_TOTAL = float(os.environ.get("MINDIE_BOOTSTRAP_TOTAL_SECONDS") or 1200.0)
UV_PYTHON_PROVISION = float(os.environ.get("MINDIE_UV_PYTHON_PROVISION_SECONDS") or 180.0)


def arm_deadline(inner=None):
    """Start a cross-interpreter clock, then rebase onto this interpreter.

    Outer 3.9 publishes wall time only. Inner 3.13 always overwrites
    `DEADLINE_ENV` from that wall deadline so a foreign monotonic value
    cannot expire the budget the instant CPython 3.13 starts.
    """
    if inner is None:
        inner = os.environ.get(INNER_ENV) == "1"
    unix = os.environ.get(DEADLINE_UNIX_ENV)
    if not unix:
        extra = 0.0 if inner else UV_PYTHON_PROVISION
        unix = str(time.time() + BOOTSTRAP_TOTAL + extra)
        os.environ[DEADLINE_UNIX_ENV] = unix
    if not inner:
        return None
    remaining = float(unix) - time.time()
    deadline = time.monotonic() + remaining
    os.environ[DEADLINE_ENV] = str(deadline)
    return deadline


def _reexec_python313(argv):
    uv = which_host_binary("uv", which_fn=shutil.which)
    if uv is None:
        raise FileNotFoundError("uv is required to prepare the locked MindIE runtime")
    os.environ[INNER_ENV] = "1"
    command = [uv, "--native-tls", "run", "--no-project", "--python", "3.13",
               "python", str(Path(__file__).resolve()), *argv]
    bounded.exec_or_wait(command, dict(os.environ))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    inner = os.environ.get(INNER_ENV) == "1"
    arm_deadline(inner)
    if not inner:
        _reexec_python313(argv)
        return
    bootstrap = Path(__file__).resolve().with_name("bootstrap.py")
    sys.path.insert(0, str(bootstrap.parent))
    sys.argv = [str(bootstrap), *argv]
    runpy.run_path(str(bootstrap), run_name="__main__")


if __name__ == "__main__":
    main()
