#!/usr/bin/env python3
"""Cold-start `SessionStart` trampoline for the MindIE Claude Code plugin.

[Role] Delegates to `mindie_launch.main(["hook", "session-start", *argv])`, re-execing through `uv --python 3.13` only below Python 3.11.
[Invariants] Pure stdlib; extra hook flags such as `--offline` pass through unchanged.
[Timeouts & Locks] No locks; the re-exec inherits the host SessionStart timeout.
[Side-Effect Policy] Re-executes one interpreter or delegates in-process; writes no state.
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
