#!/usr/bin/env python3
"""Cold-start `SessionStart` trampoline for the MindIE Claude Code plugin.

[Role] Delegates to `mindie_launch.main(["hook", "session-start", *argv])` to write `pending-update-check.json` and print `{}` in `< 30ms`.
[Invariants] Pure stdlib; never builds a runtime, never runs `uv sync`, and never creates SQLite on cold start.
[Timeouts & Locks] No locks; sub-30ms marker-only handoff.
[Side-Effect Policy] Atomically writes `pending-update-check.json` via `mindie_launch` and emits `{}`.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
_NO_WIN = 0x08000000 if os.name == "nt" else 0
UV_COMMAND = ("uv", "--native-tls", "run", "--no-project", "--python", "3.13", "python")

def _reexec(args: list[str]) -> int:
    command = [*UV_COMMAND, str(Path(__file__).resolve()), *args]
    return subprocess.run(command, creationflags=_NO_WIN).returncode

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:2] == ["hook", "session-start"]:
        args = args[2:]
    if sys.version_info < (3, 11):
        return _reexec(args)
    here = str(HERE)
    if here not in sys.path:
        sys.path.insert(0, here)
    import mindie_launch

    return mindie_launch.main(["hook", "session-start", *args])

if __name__ == "__main__":
    raise SystemExit(main())
