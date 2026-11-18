#!/usr/bin/env python3
"""File-based MCP and hook launcher for the MindIE Claude Code plugin.

[Role] Resolves a prepared release `entry.py` when present, otherwise `mindie_launch.py` or `session_start.py`, and forwards argv via `runpy`.
[Invariants] 100% pure stdlib; zero inline interpreter snippets in manifests or hooks.
[Timeouts & Locks] Pure file-existence resolution; no locks or subprocesses.
[Side-Effect Policy] Strictly read-only until the final `runpy` handoff.
"""
import json
import os
from pathlib import Path
import runpy
import sys

def _release_version(plugin_root: Path) -> str:
    try:
        data = json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return "0.1.30"
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) and version else "0.1.30"

def resolve_target(args: list[str], *, plugin_root: Path | None = None, plugin_data: Path | None = None,
                   release_version: str | None = None) -> Path:
    """Pick the prepared release entry, or the retained cold-start launcher."""
    root = plugin_root if plugin_root is not None else Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parents[1])
    raw_data = str(plugin_data) if plugin_data is not None else os.environ.get("CLAUDE_PLUGIN_DATA", "")
    if raw_data:
        prepared = Path(raw_data) / "releases" / (release_version or _release_version(root)) / "entry.py"
        if prepared.is_file():
            return prepared
    return Path(__file__).resolve().with_name("session_start.py" if args == ["hook", "session-start"] else "mindie_launch.py")

def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    version = None
    if len(args) >= 2 and args[0] == "--release-version":
        version, args = args[1], args[2:]
    target = resolve_target(args, release_version=version)
    if str(target.parent) not in sys.path:
        sys.path.insert(0, str(target.parent))
    sys.argv = [str(target), *args]
    runpy.run_path(str(target), run_name="__main__")

if __name__ == "__main__":
    main()
