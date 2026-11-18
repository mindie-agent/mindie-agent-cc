#!/usr/bin/env python3
"""Legacy pre-0.1.30 `mcp_server.py` shim for in-flight `v0.1.29` sessions.

[Role] Serves one-shot `mcp_server.py <surface> --once` or stdio MCP requests spawned by still-open `v0.1.29` `mindie_launch.py:_dispatch` processes after `cc.json` switches to `0.1.31+`.
[Invariants] Preserves surface separation (`knowledge`, `remote`, `components`) via `entry.main(["mcp", surface])`.
[Timeouts & Locks] Inherits `MCPStdioServer` pool draining on EOF and per-call router deadlines.
[Side-Effect Policy] Delegates exclusively to `entry.main`.
"""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import entry  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = [item for item in (sys.argv[1:] if argv is None else argv) if item != "--once"]
    return entry.main(["mcp", args[0] if args else "all"])


if __name__ == "__main__":
    raise SystemExit(main())
