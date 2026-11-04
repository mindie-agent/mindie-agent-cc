"""finish_bind storage errors keep errno and a fixed path label."""

import errno
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import diagnostic_support
import identity
import mcp_server


SESSION = "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"


def _use_state(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    config = tmp_path / "cc.json"
    config.write_text(json.dumps({"state_dir": str(state)}), encoding="utf-8")
    monkeypatch.setenv("MINDIE_CC_CONFIG", str(config))
    return state


def _last_event(root):
    files = list(Path(root).glob("events/*/*.jsonl"))
    assert files, "expected a local diagnostic event"
    return json.loads(files[-1].read_text(encoding="utf-8").splitlines()[-1])


def test_native_plugin_manifest_supplies_package_version():
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert diagnostic_support.build_metadata()["version"] == manifest["version"]


def test_missing_bind_store_has_errno_and_no_absolute_path(tmp_path, monkeypatch):
    state = _use_state(tmp_path, monkeypatch)
    with pytest.raises(FileNotFoundError) as caught:
        identity.finish_bind("call-missing", True)
    err = caught.value
    assert err.errno == errno.ENOENT
    assert err.diagnostic_path == "mcp-binds.sqlite3"
    assert str(state) not in str(err)
    assert str(tmp_path) not in str(err)


def test_overlong_bind_path_reports_errno_without_the_location(tmp_path, monkeypatch):
    _use_state(tmp_path, monkeypatch)
    path = tmp_path / ("n" * 4000) / "mcp-binds.sqlite3"
    with pytest.raises(OSError) as caught:
        identity._connect(path, create=False)
    err = caught.value
    assert err.errno in {errno.ENAMETOOLONG, errno.ENOENT}
    assert err.diagnostic_path == "mcp-binds.sqlite3"
    text = str(err)
    assert str(tmp_path) not in text
    assert "n" * 80 not in text


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink loop")
def test_symlink_loop_is_not_reported_as_a_missing_file(tmp_path, monkeypatch):
    state = _use_state(tmp_path, monkeypatch)
    other = state / "other"
    link = state / "mcp-binds.sqlite3"
    link.symlink_to("other")
    other.symlink_to("mcp-binds.sqlite3")
    with pytest.raises(OSError) as caught:
        identity.finish_bind("call-loop", True)
    err = caught.value
    assert err.errno == errno.ELOOP
    assert not isinstance(err, FileNotFoundError)
    assert err.diagnostic_path == "mcp-binds.sqlite3"
    assert str(state) not in str(err)


def test_disk_full_while_restricting_the_store_is_visible(tmp_path, monkeypatch):
    state = _use_state(tmp_path, monkeypatch)
    identity._connect(identity.bind_db_path(), create=True).close()
    real = os.chmod

    def fail_chmod(path, mode):
        raise OSError(errno.ENOSPC, "No space left on device", str(path))

    monkeypatch.setattr(identity.os, "chmod", fail_chmod)
    with pytest.raises(OSError) as caught:
        identity.finish_bind("call-disk", True)
    err = caught.value
    assert err.errno == errno.ENOSPC
    assert err.diagnostic_path == "mcp-binds.sqlite3"
    assert str(state) not in str(err)
    monkeypatch.setattr(identity.os, "chmod", real)


def _ready_store():
    db = identity._connect(identity.bind_db_path(), create=True)
    try:
        identity._ensure_binds(db)
        db.commit()
    finally:
        db.close()


def test_chmod_permission_error_does_not_drop_the_receipt(tmp_path, monkeypatch):
    _use_state(tmp_path, monkeypatch)
    _ready_store()
    identity.bind_db_path().chmod(0o644)

    def fail_chmod(path, mode):
        raise PermissionError(errno.EPERM, "Operation not permitted", str(path))

    monkeypatch.setattr(identity.os, "chmod", fail_chmod)
    identity.finish_bind("call-mode", True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory modes")
def test_unreadable_parent_keeps_permission_errno(tmp_path, monkeypatch):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores directory mode")
    state = _use_state(tmp_path, monkeypatch)
    _ready_store()
    state.chmod(0)
    try:
        with pytest.raises(PermissionError) as caught:
            identity.finish_bind("call-perm", True)
        err = caught.value
        assert err.errno == errno.EACCES
        assert err.diagnostic_path == "mcp-binds.sqlite3"
        assert str(state) not in str(err)
        assert str(tmp_path) not in str(err)
    finally:
        state.chmod(0o700)


def test_finish_bind_oserror_preserves_the_tool_result_and_records_errno(tmp_path, monkeypatch):
    state = _use_state(tmp_path, monkeypatch)
    name = "knowledge_query"
    arguments = {"query": "npu"}
    key = "call-storage-errno"
    identity.publish_bind(
        SESSION, key, f"mcp__plugin_mindie-agent_knowledge__{name}", arguments,
    )
    request = {
        "jsonrpc": "2.0", "id": key, "method": "tools/call",
        "params": {
            "name": name, "arguments": arguments,
            "_meta": {"claudecode/toolUseId": key},
        },
    }
    payload = {"matches": [{"ref": "task:kept", "score": 1.0}]}
    armed = {"on": False}
    real = os.chmod

    def selective_chmod(path, mode, **kwargs):
        # os.chmod is process-global, and Path.chmod passes follow_symlinks.
        # Fail only the bind database so diagnostic logs can still be written.
        if armed["on"] and Path(path).name == "mcp-binds.sqlite3":
            raise OSError(errno.ENOSPC, "No space left on device", str(state / "hidden"))
        return real(path, mode, **kwargs)

    def business(*_args):
        armed["on"] = True
        return payload

    monkeypatch.setattr(identity.os, "chmod", selective_chmod)
    with patch.object(mcp_server, "knowledge_call", business):
        result = mcp_server.handle("knowledge", request)["result"]
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert structured["matches"] == payload["matches"]
    assert structured["bind_errno"] == errno.ENOSPC
    assert structured["bind_path"] == "mcp-binds.sqlite3"
    assert structured["bind_error_type"] == "OSError"
    rendered = json.dumps(result)
    assert str(state) not in rendered
    assert str(tmp_path) not in rendered
    assert "hidden" not in rendered
    event = _last_event(os.environ["MINDIE_DIAGNOSTICS_ROOT"])
    assert event["operation"] == "mcp.call"
    assert event["attributes"]["stage"] == "finish_bind"
    assert event["attributes"]["category"] == "local_storage"
    assert event["attributes"]["error_type"] == "OSError"
    assert event["attributes"]["errno"] == errno.ENOSPC
    assert event["attributes"]["path"] == "mcp-binds.sqlite3"
    assert event["package_version"] == diagnostic_support.build_metadata()["version"]
    assert str(state) not in json.dumps(event)
    assert str(tmp_path) not in json.dumps(event)
