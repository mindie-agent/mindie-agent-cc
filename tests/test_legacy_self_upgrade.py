"""End-to-end pre-0.1.30 (`v0.1.29 -> v0.1.31`) native self-upgrade handoff tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
_NO_WIN = 0x08000000 if os.name == "nt" else 0


def _assert_v0129_complete_binding(expected: dict, selected_path: Path) -> dict:
    value = json.loads(selected_path.read_text(encoding="utf-8"))
    assert value["schema"] == "mindie-cc-installation/1"
    assert value["facade_protocol"] == 1
    for key in ("base_config", "engine_config", "python", "state_dir", "runtime_scripts", "adapter_config", "community_config"):
        assert isinstance(value.get(key), str) and os.path.isabs(value[key]), key
    assert value["base_config"] == expected["base_config"]
    assert value["state_dir"] == expected["state_dir"]
    fixed = json.loads(Path(value["adapter_config"]).read_text(encoding="utf-8"))
    assert value == fixed
    return value


def test_v0129_to_v0131_self_upgrade_handoff_cas_and_live_frontend(tmp_path: Path, monkeypatch) -> None:
    cfg_dir = tmp_path / "cfg"
    state_dir = tmp_path / "legacy-adapter-state"
    cfg_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    base_config = cfg_dir / "cc.json"
    old_engine = cfg_dir / "cc.engine.json"
    community = cfg_dir / "mindie-community.json"
    knowledge_root = tmp_path / "custom-knowledge"
    old_engine.write_text(json.dumps({
        "root": str(knowledge_root),
        "domain": "operators",
        "admission_path": str(tmp_path / "admission.sqlite3"),
    }), encoding="utf-8")
    legacy_binding = {
        "schema": "mindie-cc-installation/1",
        "facade_protocol": 1,
        "base_config": str(base_config),
        "adapter_config": str(base_config),
        "engine_config": str(old_engine),
        "python": "/nonexistent/v0.1.29/python",
        "runtime_scripts": str(tmp_path / "v0.1.29/scripts"),
        "state_dir": str(state_dir),
        "community_config": str(community),
        "component_commands": {},
    }
    base_config.write_text(json.dumps(legacy_binding, indent=2), encoding="utf-8")

    # Step 1: v0.1.29 bootstrap._component_override invokes candidate scripts/component_update.py
    comp = subprocess.run(
        [sys.executable, "-I", "-B", str(SCRIPTS / "component_update.py"),
         str(ROOT), sys.executable, str(state_dir / "component-runtimes"), sys.executable],
        capture_output=True, text=True, timeout=60, creationflags=_NO_WIN,
    )
    assert comp.returncode == 0, comp.stderr
    assert json.loads(comp.stdout) is None

    # Step 2: v0.1.29 release_update.py runs setup.py --source <installed> --config <base_config>
    env = dict(os.environ, MINDIE_CC_CONFIG=str(base_config), MINDIE_SETUP_ERROR_ENVELOPE="1")
    prep = subprocess.run(
        [sys.executable, str(SCRIPTS / "setup.py"), "--source", str(ROOT), "--config", str(base_config)],
        env=env, capture_output=True, text=True, timeout=60, creationflags=_NO_WIN,
    )
    assert prep.returncode == 0, prep.stderr
    prepared = json.loads(prep.stdout)
    assert prepared["status"] == "prepared"
    assert json.loads(base_config.read_text(encoding="utf-8"))["python"] == "/nonexistent/v0.1.29/python"
    selected = _assert_v0129_complete_binding(legacy_binding, Path(prepared["config"]))
    assert selected["python"] == sys.executable
    assert selected["runtime_scripts"] == str(SCRIPTS)

    # Step 3: CAS deferral if component_commands mutated concurrently
    mutated = dict(legacy_binding, component_commands={"mindie-sim": ["other", "-m", "mindie_sim"]})
    base_config.write_text(json.dumps(mutated), encoding="utf-8")
    cas_defer = subprocess.run(
        [sys.executable, str(SCRIPTS / "setup.py"), "--activate", prepared["config"]],
        env=env, capture_output=True, text=True, timeout=60, creationflags=_NO_WIN,
    )
    assert cas_defer.returncode == 0, cas_defer.stderr
    assert json.loads(cas_defer.stdout) == {"stage": "component_commands_changed", "status": "deferred", "switched": False}

    # Step 4: Uncertain retirement (`cleanup_failed=True` or `OSError`) refuses canonical switch
    base_config.write_text(json.dumps(legacy_binding), encoding="utf-8")
    import mindie_knowledge.loop.lifecycle as lifecycle
    import setup as cc_setup

    (knowledge_root / "operators").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(lifecycle, "retire_service", lambda *a, **kw: {"idle": True, "cleanup_failed": True})
    captured: list[dict] = []
    monkeypatch.setattr(cc_setup, "_emit", lambda doc: captured.append(doc))
    assert cc_setup._legacy_activate(Path(prepared["config"])) == 0
    assert captured[-1] == {"status": "incomplete", "stage": "old_retirement", "switched": False}
    assert json.loads(base_config.read_text(encoding="utf-8"))["python"] == "/nonexistent/v0.1.29/python"

    def _raise_oserror(*a, **kw):
        raise OSError("socket error")

    monkeypatch.setattr(lifecycle, "retire_service", _raise_oserror)
    assert cc_setup._legacy_activate(Path(prepared["config"])) == 0
    assert captured[-1] == {"status": "incomplete", "stage": "old_retirement", "switched": False}
    assert json.loads(base_config.read_text(encoding="utf-8"))["python"] == "/nonexistent/v0.1.29/python"
    monkeypatch.undo()

    # Remove empty domain dir and activate cleanly via subprocess
    (knowledge_root / "operators").rmdir()
    act = subprocess.run(
        [sys.executable, str(SCRIPTS / "setup.py"), "--activate", prepared["config"]],
        env=env, capture_output=True, text=True, timeout=60, creationflags=_NO_WIN,
    )
    assert act.returncode == 0, act.stderr
    activated = json.loads(act.stdout)
    assert activated["status"] == "activated" and activated["switched"] is True
    final_binding = _assert_v0129_complete_binding(legacy_binding, base_config)
    assert final_binding["python"] == sys.executable

    # Step 5: Live in-flight v0.1.29 frontend shims (`bridge.py` and `mcp_server.py --once`) pass staged adapter_config in MINDIE_CC_CONFIG across repeated calls without breaking _complete_binding
    child_env = dict(env, MINDIE_CC_CONFIG=final_binding["adapter_config"])
    for req_id in (7, 8):
        hook_proc = subprocess.run(
            [final_binding["python"], str(Path(final_binding["runtime_scripts"]) / "bridge.py"), "pretool"],
            input="{}", env=child_env, capture_output=True, text=True, timeout=30, creationflags=_NO_WIN,
        )
        assert hook_proc.returncode == 0, hook_proc.stderr
        assert json.loads(hook_proc.stdout) == {}
        _assert_v0129_complete_binding(legacy_binding, base_config)

        mcp_proc = subprocess.run(
            [final_binding["python"], str(Path(final_binding["runtime_scripts"]) / "mcp_server.py"), "remote", "--once"],
            input=json.dumps({"jsonrpc": "2.0", "id": req_id, "method": "tools/list", "params": {}}) + "\n",
            env=child_env, capture_output=True, text=True, timeout=30, creationflags=_NO_WIN,
        )
        assert mcp_proc.returncode == 0, mcp_proc.stderr
        mcp_reply = json.loads(mcp_proc.stdout)
        assert mcp_reply["id"] == req_id
        tool_names = {item["name"] for item in mcp_reply["result"]["tools"]}
        assert "remote_Bash" in tool_names and "remote_BashOutput" not in tool_names
        _assert_v0129_complete_binding(legacy_binding, base_config)
