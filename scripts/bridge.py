#!/usr/bin/env python3
"""Legacy pre-0.1.30 `bridge.py` hook shim for in-flight `v0.1.29` sessions.

[Role] Accepts `bridge.py <hook_op>` from still-open `v0.1.29` `mindie_launch.py:_hook` processes after `cc.json` switches to `0.1.31+` and forwards to `entry.py hook <op>`.
[Invariants] Preserves hook stdin and emits a single JSON `{}` line on stdout.
[Timeouts & Locks] Inherits `entry.py` bounded `6.0s` `Stop` capture budget.
[Side-Effect Policy] Delegates exclusively to `entry.main`.
"""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import entry  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return entry.main(["hook", *(args or ["pretool"])])


if __name__ == "__main__":
    raise SystemExit(main())
