"""Finite old-task removal preserves effects and never chooses plugin code."""
import json
from pathlib import Path
import plistlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from support import SCRIPTS
import sys
sys.path.insert(0, str(SCRIPTS))
import legacy_update
from install_state import _Held, LockTimeout


def test_other_profiles_task_is_not_removed(tmp_path):
    config = tmp_path / "cc.json"
    adapter = {"state_dir": str(tmp_path / "state")}
    plist = tmp_path / "test.plist"
    plist.write_bytes(plistlib.dumps({"Label": "owned", "ProgramArguments": [
        "python", str(tmp_path / "somewhere/mindie_launch.py"),
        "--config", str(config), "updater", "check"]}))
    with patch.object(legacy_update.sys, "platform", "darwin"), \
            patch.object(legacy_update, "_native") as native:
        with pytest.raises(ValueError, match="retained launcher"):
            legacy_update.remove_schedule(adapter, config, label="owned", plist_path=plist)
    native.assert_not_called()
    assert plist.exists()


def test_owned_old_task_is_removed_once_and_verified_absent(tmp_path):
    config = tmp_path / "cc.json"
    adapter = {"state_dir": str(tmp_path / "state")}
    plist = tmp_path / "test.plist"
    plist.write_bytes(plistlib.dumps({"Label": "owned", "ProgramArguments": [
        "python", str(tmp_path / "state/update/launch/old/mindie_launch.py"),
        "--config", str(config), "updater", "check"]}))
    responses = [SimpleNamespace(returncode=x) for x in (0, 0, 113)]
    with patch.object(legacy_update.sys, "platform", "darwin"), \
            patch.object(legacy_update, "_native", side_effect=responses) as native:
        assert legacy_update.remove_schedule(adapter, config, label="owned", plist_path=plist) == "removed"
    assert [call.args[0][1] for call in native.call_args_list] == ["print", "bootout", "print"]
    assert not plist.exists()


def test_busy_old_writer_blocks_before_status_or_task_mutation(tmp_path):
    adapter = {"state_dir": str(tmp_path)}
    with _Held(tmp_path / "update/check.lock", True, 0):
        with patch.object(legacy_update, "status") as status, \
                patch.object(legacy_update, "remove_schedule") as remove:
            with pytest.raises(LockTimeout):
                legacy_update.migrate(adapter, tmp_path / "cc.json")
    status.assert_not_called()
    remove.assert_not_called()


def test_unknown_legacy_install_is_retained_after_scheduler_disabled(tmp_path):
    adapter = {"state_dir": str(tmp_path)}
    update = tmp_path / "update"
    update.mkdir()
    state = update / "status.json"
    state.write_text(json.dumps({"native_install": {"outcome": "unknown", "mutation": "started"}}))
    before = state.read_bytes()
    with patch.object(legacy_update, "remove_schedule", return_value="removed") as remove:
        with pytest.raises(RuntimeError, match="remains unknown"):
            legacy_update.migrate(adapter, tmp_path / "cc.json")
    assert state.read_bytes() == before
    remove.assert_called_once()
    assert legacy_update.status(adapter)["automatic_retry"] is False


def test_nonregular_or_oversized_legacy_metadata_is_not_read_or_migrated(tmp_path):
    path = tmp_path / "status.json"
    path.mkdir()
    with pytest.raises(ValueError, match="bounded regular"):
        legacy_update._read(path)
    path.rmdir()
    with path.open("wb") as stream:
        stream.truncate(1024 * 1024 + 1)
    with pytest.raises(ValueError, match="bounded regular"):
        legacy_update._read(path)
