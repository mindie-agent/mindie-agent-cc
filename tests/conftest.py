"""Isolated environment for the thin-plugin contract tests.

[Role]
Autouse fixture redirects every host/profile/temp environment variable into a
per-test temporary directory so no test touches the developer's real
`~/.config`, `~/.claude`, plugin data, or remote auth state.
[Invariants]
- Every patched variable is absolute and lives under the test `tmp_path`.
- Subprocesses inherit the patched environment through `os.environ`.
[Timeouts & Locks]
- No locks or network access.
[Side-Effect Policy]
- Writes only inside `tmp_path`; restores the environment after each test.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_DIRECTORY_VARIABLES = (
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "MINDIE_CONFIG_HOME",
    "MINDIE_DATA_HOME",
    "MINDIE_DIAGNOSTICS_ROOT",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_PLUGIN_DATA",
    "REMOTE_DEV_AUTH_DIR",
    "UV_CACHE_DIR",
    "TMPDIR",
)
_FILE_VARIABLES = (
    "MINDIE_CC_CONFIG",
    "MINDIE_AGENT_CONFIG",
    "MINDIE_DIAGNOSTICS_CONFIG",
)


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point every host/profile/temp variable at one fresh temporary tree."""
    for name in _DIRECTORY_VARIABLES:
        target = tmp_path / name.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(target))
    for name in _FILE_VARIABLES:
        target = tmp_path / "config" / f"{name.lower()}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(target))
    monkeypatch.delenv("_MINDIE_UV_FLAGS", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    return tmp_path
