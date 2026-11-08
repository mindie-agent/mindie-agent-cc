#!/usr/bin/env python3
"""Stdlib SessionStart trampoline: start the shared clock, then run CPython 3.13.

[Role]
Cold SessionStart entry before `bootstrap.py`. Safe on host Python 3.9+.

[Invariants]
- Stdlib only: never imports `tomllib`, `product_contract`, or `mindie_launch`.
- Sets `MINDIE_SESSION_START_DEADLINE_MONOTONIC` before `uv --python 3.13`.

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

DEADLINE_ENV = "MINDIE_SESSION_START_DEADLINE_MONOTONIC"
INNER_ENV = "MINDIE_SESSION_START_INNER"
BOOTSTRAP_TOTAL = float(os.environ.get("MINDIE_BOOTSTRAP_TOTAL_SECONDS") or 1200.0)
UV_PYTHON_PROVISION = float(os.environ.get("MINDIE_UV_PYTHON_PROVISION_SECONDS") or 180.0)


def _reexec_python313(argv):
    uv = shutil.which("uv")
    if uv is None:
        raise FileNotFoundError("uv is required to prepare the locked MindIE runtime")
    os.environ[INNER_ENV] = "1"
    command = [uv, "--native-tls", "run", "--no-project", "--python", "3.13",
               "python", str(Path(__file__).resolve()), *argv]
    os.execve(uv, command, dict(os.environ))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    inner = os.environ.get(INNER_ENV) == "1"
    if not os.environ.get(DEADLINE_ENV):
        extra = 0.0 if inner else UV_PYTHON_PROVISION
        os.environ[DEADLINE_ENV] = str(time.monotonic() + BOOTSTRAP_TOTAL + extra)
    if not inner:
        _reexec_python313(argv)
        return
    bootstrap = Path(__file__).resolve().with_name("bootstrap.py")
    sys.path.insert(0, str(bootstrap.parent))
    sys.argv = [str(bootstrap), *argv]
    runpy.run_path(str(bootstrap), run_name="__main__")


if __name__ == "__main__":
    main()
