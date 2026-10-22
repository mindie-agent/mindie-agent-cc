"""Host isolation and marker registration for the complete test suite.

Every test runs with its home, XDG roots, Claude profile, adapter config and
diagnostics locations redirected below pytest's ``tmp_path``. A live-NPU test
keeps its SSH credentials usable by exposing the real ``~/.ssh`` through the
isolated home directory; it never reads or writes a host MindIE profile.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_REAL_HOME = Path.home()


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_npu: non-destructive integration against standing A2/A3 hosts",
    )


@pytest.fixture(autouse=True)
def _isolated_host_environment(tmp_path, request, monkeypatch):
    """Redirect every host-owned path into this test's temporary directory."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    if request.node.get_closest_marker("live_npu") is not None:
        real_ssh = _REAL_HOME / ".ssh"
        if real_ssh.is_dir() and not (home / ".ssh").exists():
            os.symlink(real_ssh, home / ".ssh")
    locations = {
        "HOME": home,
        "USERPROFILE": home,
        "XDG_CONFIG_HOME": tmp_path / "xdg-config",
        "XDG_DATA_HOME": tmp_path / "xdg-data",
        "XDG_STATE_HOME": tmp_path / "xdg-state",
        "CLAUDE_CONFIG_DIR": tmp_path / "claude-config",
        "MINDIE_CC_CONFIG": tmp_path / "mindie-cc" / "cc.json",
        "MINDIE_DIAGNOSTICS_ROOT": tmp_path / "mindie-diagnostics",
        "MINDIE_DIAGNOSTICS_CONFIG": tmp_path / "mindie-diagnostics" / "diagnostics.json",
        "MINDIE_REMOTE_STATE_DIR": tmp_path / "mindie-remote-state",
    }
    for name, location in locations.items():
        location = Path(location)
        if name in {"MINDIE_CC_CONFIG", "MINDIE_DIAGNOSTICS_CONFIG"}:
            location.parent.mkdir(parents=True, exist_ok=True)
        else:
            location.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(location))
    yield
