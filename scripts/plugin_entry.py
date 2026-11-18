#!/usr/bin/env python3
"""File-based MCP and hook launcher for `mindie-agent-cc` (zero `python -c`).

[Role]
Resolves the prepared release `entry.py` under `$CLAUDE_PLUGIN_DATA/releases/<ver>/entry.py`
when available, or falls back to `$CLAUDE_PLUGIN_ROOT/scripts/bootstrap.py`, and
dispatches CLI arguments (`mcp <surface>` or `hook <event>`).

[Invariants]
- 100% pure Python stdlib; zero inline `python -c` blobs in `.mcp.json` (`AD-12`).
- Preserves exact `sys.argv` forwarding expected by `entry.py` and `bootstrap.py`.

[Timeouts & Locks]
- Pure file-existence resolution before `runpy.run_path` handoff.

[Side-Effect Policy]
- Strictly read-only until handing off to `entry.py` or `bootstrap.py`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import sys


def _release_version(plugin_root: Path) -> str:
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        version = data.get("version")
        if isinstance(version, str) and version:
            return version
    return "0.1.29"


def resolve_target(
    *,
    plugin_root: Path | None = None,
    plugin_data: Path | None = None,
    release_version: str | None = None,
) -> Path:
    root = (
        plugin_root
        if plugin_root is not None
        else Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parents[1])
    )
    version = release_version or _release_version(root)
    raw_data = str(plugin_data) if plugin_data is not None else os.environ.get("CLAUDE_PLUGIN_DATA", "")
    if raw_data:
        prepared = Path(raw_data) / "releases" / version / "entry.py"
        if prepared.is_file():
            return prepared
    bootstrap = root / "scripts" / "bootstrap.py"
    if bootstrap.is_file():
        return bootstrap
    raise RuntimeError("MindIE Claude plugin launcher target is unavailable")


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    version: str | None = None
    if len(args) >= 2 and args[0] == "--release-version":
        version = args[1]
        args = args[2:]
    target = resolve_target(release_version=version)
    sys.path.insert(0, str(target.parent))
    sys.argv = [str(target), *args]
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
