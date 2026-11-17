"""Group G6a orthogonal regression tests for `mindie-agent-cc` (`#364/#365`, `AD-02/06/07/10/11/12`)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    xdg_cfg = tmp_path / "xdg-config"
    xdg_data = tmp_path / "xdg-data"
    xdg_state = tmp_path / "xdg-state"
    xdg_cache = tmp_path / "xdg-cache"
    claude_dir = tmp_path / "claude"
    plugin_data = tmp_path / "plugin-data"
    tmp_dir = tmp_path / "tmp"
    for d in (home, xdg_cfg, xdg_data, xdg_state, xdg_cache, claude_dir, plugin_data, tmp_dir):
        d.mkdir(parents=True, exist_ok=True)
    env_map = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "XDG_CONFIG_HOME": str(xdg_cfg),
        "XDG_DATA_HOME": str(xdg_data),
        "XDG_STATE_HOME": str(xdg_state),
        "XDG_CACHE_HOME": str(xdg_cache),
        "CLAUDE_CONFIG_DIR": str(claude_dir),
        "CLAUDE_PLUGIN_ROOT": str(ROOT),
        "CLAUDE_PLUGIN_DATA": str(plugin_data),
        "MINDIE_CONFIG_HOME": str(xdg_cfg / "mindie-agent"),
        "MINDIE_DATA_HOME": str(xdg_data / "mindie-agent"),
        "MINDIE_CC_CONFIG": str(xdg_cfg / "mindie-agent" / "cc.json"),
        "MINDIE_DIAGNOSTICS_ROOT": str(tmp_path / "diagnostics"),
        "MINDIE_DIAGNOSTICS_CONFIG": str(tmp_path / "diagnostics" / "diagnostics.json"),
        "REMOTE_DEV_AUTH_DIR": str(tmp_path / "remote-auth"),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "TMPDIR": str(tmp_dir),
    }
    for key, val in env_map.items():
        monkeypatch.setenv(key, val)
    return env_map


def test_issues_364_and_365_marketplace_add_failure_retries_cleanly_without_internal_crash(
    isolated_env, tmp_path
):
    """Exact #364 -> #365 sequence:
    1. `claude plugin marketplace add` fails with returncode=1 (`completed=True`).
    2. `native_effect` MUST be `'failed_cleanly'` (not `'completed'`, not `'unknown'`),
       and diagnostic recording MUST mark `reportable is False` (never `update_internal_failure`).
    3. 61s later (`> FAILURE_BACKOFF`), `install_selected` MUST re-run `marketplace add`
       instead of skipping it and crashing in `_verify_catalog -> _marketplace:327` (`ValueError`).
    """
    import release_update as update
    from paths import MARKETPLACE, PLUGIN_QUALIFIED

    claude_home = Path(isolated_env["CLAUDE_CONFIG_DIR"])
    state_root = tmp_path / "state"
    state_root.mkdir(parents=True)
    (claude_home / "settings.json").write_text(
        json.dumps({
            "extraKnownMarketplaces": {
                MARKETPLACE: {
                    "source": {"source": "git", "url": f"https://github.com/{update.REPOSITORY}.git"},
                    "autoUpdate": True,
                }
            }
        }),
        encoding="utf-8",
    )
    binding = {
        "base_config": isolated_env["MINDIE_CC_CONFIG"],
        "adapter_config": isolated_env["MINDIE_CC_CONFIG"],
        "engine_config": str(tmp_path / "engine.json"),
        "python": sys.executable,
        "runtime_scripts": str(SCRIPTS),
        "state_dir": str(state_root),
        "claude_config_dir": str(claude_home),
    }
    candidate_sha = "b" * 40
    candidate = {
        "tag_name": "v0.1.26",
        "metadata": {
            "version": "0.1.26",
            "source": {"repository": update.REPOSITORY, "sha": candidate_sha},
            "compatibility": update.compatibility(ROOT),
        },
    }
    state = {
        "status": "selected",
        "candidate": candidate,
        "native_effect": "not_started",
        "process": {},
        "activation_effect": "not_started",
        "activation_process": {},
    }

    marketplace_present = False
    installed_ver = "0.1.25"
    marketplace_add_calls = []

    def fake_run(argv, *args, receipt=None, **kwargs):
        nonlocal marketplace_present, installed_ver
        cmd = [str(x) for x in argv]
        if cmd[:3] == ["claude", "plugin", "list"]:
            return json.dumps([{
                "id": PLUGIN_QUALIFIED,
                "scope": "user",
                "enabled": True,
                "version": installed_ver,
                "installPath": str(ROOT),
            }])
        if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
            if not marketplace_present:
                return json.dumps([])
            return json.dumps([{
                "name": MARKETPLACE,
                "source": "git",
                "url": f"https://github.com/{update.REPOSITORY}.git",
                "ref": "v0.1.26",
                "installLocation": str(ROOT),
            }])
        if cmd[:4] == ["claude", "plugin", "marketplace", "add"]:
            marketplace_add_calls.append(cmd)
            if len(marketplace_add_calls) == 1:
                # Attempt 1 (#364): external `claude plugin marketplace add` fails with exit 1!
                if isinstance(receipt, dict):
                    receipt.update(
                        started=True,
                        completed=True,
                        cleanup_failed=False,
                        returncode=1,
                        stderr="Error: Failed to clone marketplace repository",
                    )
                return ""
            # Attempt 2 (#365 recovery): succeeds and registers marketplace!
            marketplace_present = True
            if isinstance(receipt, dict):
                receipt.update(started=True, completed=True, cleanup_failed=False, returncode=0)
            return "ok"
        if cmd[:3] == ["claude", "plugin", "update"]:
            installed_ver = "0.1.26"
            if isinstance(receipt, dict):
                receipt.update(started=True, completed=True, cleanup_failed=False, returncode=0)
            return "{}"
        if cmd[:2] == ["git", "-C"] and "rev-parse" in cmd:
            return candidate_sha + "\n"
        raise AssertionError(f"Unexpected command: {cmd}")

    # Round 1: `marketplace add` fails (#364)
    with patch.object(update, "run", side_effect=fake_run):
        with pytest.raises(RuntimeError) as caught:
            update.install_selected(binding, "claude", candidate, state)

    assert len(marketplace_add_calls) == 1
    assert state["native_effect"] == "failed_cleanly"
    assert getattr(caught.value, "reportable", None) is False
    assert getattr(caught.value, "category", None) in {"temporary_network", "prerequisite", "remote_target"}

    # Record failure diagnostic and verify reportable=False (never update_internal_failure!)
    state.update(status="failed", last_failed_check=1000.0)
    diag = update._record_update_failure("native_update", caught.value)
    assert diag.get("logging_failed") is False
    events_dir = Path(isolated_env["MINDIE_DIAGNOSTICS_ROOT"]) / "events" / "mindie-agent-cc"
    recorded_events = [
        json.loads(line)
        for jf in events_dir.glob("*.jsonl")
        for line in jf.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert recorded_events and all(ev["reportable"] is False for ev in recorded_events)
    assert update._pending_install(state) is True
    assert update.next_check_at(state) == 1000.0 + update.FAILURE_BACKOFF

    # Round 2 (61s later): `install_selected` MUST retry `marketplace add` and succeed (#365 fixed!)
    with patch.object(update, "run", side_effect=fake_run):
        installed_path = update.install_selected(binding, "claude", candidate, state)

    assert len(marketplace_add_calls) == 2
    assert installed_path == ROOT
    assert state["native_effect"] == "verified"

    # Meanwhile, a genuinely timed-out child (`completed=False`) MUST stay `native_effect == 'unknown'`
    timeout_state = {}

    def timed_out_child(*_a, receipt=None, **_kw):
        if isinstance(receipt, dict):
            receipt.update(started=True, completed=False, cleanup_failed=False, returncode=None)
        raise TimeoutError("claude plugin marketplace add timed out")

    with patch.object(update, "run", side_effect=timed_out_child):
        with pytest.raises(TimeoutError):
            update._mutation(binding, timeout_state, "claude", ["marketplace", "add", "x"], "catalog")
    assert timeout_state["native_effect"] == "unknown"


def test_ad02_subagent_and_reconnected_session_share_remote_dev_state_dir(
    isolated_env, tmp_path
):
    import mcp_server

    state_root = tmp_path / "cc-state"
    state_root.mkdir()
    observed_dirs: list[tuple[str, str]] = []

    def fake_call_tool(name, args):
        remote_dir = Path(os.environ["REMOTE_DEV_STATE_DIR"])
        observed_dirs.append((os.environ["REMOTE_DEV_SESSION_ID"], str(remote_dir)))
        jobs_file = remote_dir / "jobs" / "job-parent-01.json"
        if name in {"remote_bash", "remote.bash"}:
            jobs_file.parent.mkdir(parents=True, exist_ok=True)
            jobs_file.write_text(json.dumps({"job_id": "job-parent-01", "status": "running"}), encoding="utf-8")
            return {"content": [{"type": "text", "text": "started"}], "isError": False}
        assert jobs_file.is_file(), "Subagent/reconnected session could not see parent job_id!"
        return {"content": [{"type": "text", "text": jobs_file.read_text(encoding="utf-8")}], "isError": False}

    parent_session = str(uuid.uuid4())
    subagent_session = str(uuid.uuid4())
    with patch.object(mcp_server, "state_dir", return_value=state_root), \
         patch("remote_dev.mcp.tools.call_tool", side_effect=fake_call_tool):
        mcp_server.remote_call("remote_bash", {"command": "sleep 10"}, parent_session)
        poll = mcp_server.remote_call("remote_job_status", {"job_id": "job-parent-01"}, subagent_session)

    assert poll["isError"] is False
    assert observed_dirs[0][1] == str(state_root / "remote")
    assert observed_dirs[1][1] == str(state_root / "remote")

    # Pre-upgrade v0.1.25 per-session `remote/<legacy_session>/endpoints/<ep>/jobs/<job_id>.json`
    # receipts are non-destructively surfaced into shared `remote/endpoints/<ep>/jobs/` on upgrade!
    legacy_session = str(uuid.uuid4())
    legacy_job = (
        state_root / "remote" / legacy_session / "endpoints" / "ep-a3" / "jobs" / "job-legacy-01.json"
    )
    legacy_job.parent.mkdir(parents=True, exist_ok=True)
    legacy_job.write_text(json.dumps({"job_id": "job-legacy-01", "status": "running"}), encoding="utf-8")
    with patch.object(mcp_server, "state_dir", return_value=state_root), \
         patch("remote_dev.mcp.tools.call_tool", return_value={"content": [], "isError": False}):
        mcp_server.remote_call("remote_job_status", {"job_id": "job-legacy-01"}, subagent_session)
    adopted_job = state_root / "remote" / "endpoints" / "ep-a3" / "jobs" / "job-legacy-01.json"
    assert adopted_job.is_file()
    assert json.loads(adopted_job.read_text(encoding="utf-8"))["job_id"] == "job-legacy-01"
    # Original legacy session receipt stays intact for rollback safety, and jobs created
    # during a rollback in the same legacy session are also surfaced upon re-upgrade!
    assert legacy_job.is_file()
    rollback_job = legacy_job.with_name("job-during-rollback.json")
    rollback_job.write_text(json.dumps({"job_id": "job-during-rollback", "status": "running"}), encoding="utf-8")
    with patch.object(mcp_server, "state_dir", return_value=state_root), \
         patch("remote_dev.mcp.tools.call_tool", return_value={"content": [], "isError": False}):
        mcp_server.remote_call("remote_job_status", {"job_id": "job-during-rollback"}, subagent_session)
    assert (state_root / "remote" / "endpoints" / "ep-a3" / "jobs" / "job-during-rollback.json").is_file()


def test_ad11_single_skill_entry_subcommand_routing_and_p21_unactivated_knowledge_gate(
    isolated_env, tmp_path
):
    import entry
    import mcp_server
    from support import make_config

    # Principle 3 / AD-11: Only `skills/mindie-agent` remains under `skills/`
    skill_dirs = sorted(p.name for p in (ROOT / "skills").iterdir() if p.is_dir())
    assert skill_dirs == ["mindie-agent"]

    # Unified `/mindie-agent <subcommand>` routing
    cfg = make_config(tmp_path / "profile", sharing=False)
    os.environ["MINDIE_CC_CONFIG"] = str(cfg)
    session = str(uuid.uuid4())
    transcript = tmp_path / f"{session}.jsonl"
    transcript.write_text("", encoding="utf-8")

    def make_event(args_text: str) -> dict:
        return {
            "hook_event_name": "UserPromptExpansion",
            "expansion_type": "slash_command",
            "command_source": "plugin",
            "command_name": "mindie-agent",
            "command_args": args_text,
            "session_id": session,
            "prompt_id": str(uuid.uuid4()),
            "cwd": str(tmp_path),
            "transcript_path": str(transcript),
        }

    status_res = entry.dispatch_event(make_event("status"))
    assert status_res["configured"] is True
    # Read-only `/mindie-agent status` must NOT activate a knowledge lease or create admission.sqlite3!
    admission_db = tmp_path / "profile" / "state" / "admission.sqlite3"
    assert not admission_db.exists()

    # P2.1: Unactivated session calling `knowledge_query` is strictly denied without creating admission.sqlite3
    with pytest.raises(ValueError, match="manual /mindie-agent activation"):
        mcp_server.knowledge_call("knowledge_query", {"query": "aclError"}, session)
    assert not admission_db.exists()

    # Activate via `/mindie-agent`, then deactivate via `/mindie-agent deactivate`
    init_res = entry.dispatch_event(make_event(""))
    assert init_res["binding"]["enabled"] is True
    deact_res = entry.dispatch_event(make_event("deactivate"))
    assert deact_res["deactivated"] is True
    rep_res = entry.dispatch_event(make_event("reporting-status"))
    assert "enabled" in rep_res


def test_ad12_zero_inline_python_c_in_mcp_json_and_plugin_entry_handoff(
    isolated_env, tmp_path
):
    import plugin_entry

    mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert set(mcp) == {"knowledge", "remote", "components"}
    for name, server in mcp.items():
        assert "-c" not in server["args"], f"{name} still uses inline python -c"
        assert "${CLAUDE_PLUGIN_ROOT}/scripts/plugin_entry.py" in server["args"]
        assert server["args"][-1] == name

    # Cold path resolves `scripts/bootstrap.py`; prepared path resolves `releases/<ver>/entry.py`
    plugin_data = tmp_path / "data"
    assert plugin_entry.resolve_target(
        plugin_root=ROOT, plugin_data=plugin_data, release_version="0.1.26"
    ) == ROOT / "scripts" / "bootstrap.py"

    prepared = plugin_data / "releases" / "0.1.26" / "entry.py"
    prepared.parent.mkdir(parents=True)
    prepared.write_text("# prepared release entry\n", encoding="utf-8")
    assert plugin_entry.resolve_target(
        plugin_root=ROOT, plugin_data=plugin_data, release_version="0.1.26"
    ) == prepared


def test_ad07_symlinks_seed_lock_rsi_forwarders_and_v0125_upgrade_rollback(
    isolated_env, tmp_path
):
    import diagnostic_support
    import host_adapter
    import install_state
    import rsi_explore
    import rsi_loop
    import experiment_ledger

    # Bundled seed + ClaudeHostAdapter
    seed_info = host_adapter.verify_bundled_seed(ROOT)
    assert seed_info["schema"] == "mindie-harness-seed/1"
    adapter = host_adapter.ClaudeHostAdapter(
        plugin_root=ROOT,
        config_path=tmp_path / "cc.json",
        data_dir=tmp_path / "plugin-data",
    )
    if host_adapter.harness_available():
        from mindie_harness.api import HostAdapter

        assert isinstance(adapter, HostAdapter)

    # RSI forwarders < 30 lines
    for name in ("rsi_loop.py", "rsi_explore.py", "experiment_ledger.py"):
        lines = (SCRIPTS / name).read_text(encoding="utf-8").splitlines()
        assert len(lines) < 30, f"{name} exceeded 30 lines ({len(lines)})"
    assert rsi_explore.forwarded({"mode": "diagnose"})["mode"] == "diagnose"
    assert callable(rsi_loop.init) and callable(experiment_ledger.fingerprint)

    # AD-07 symlink read
    real_file = tmp_path / "real.txt"
    real_file.write_text("Version: 0.1.26\n", encoding="utf-8")
    sym_file = tmp_path / "sym.txt"
    try:
        os.symlink(real_file, sym_file)
        assert diagnostic_support._read_bytes_bounded(sym_file, 1024) == b"Version: 0.1.26\n"
    except OSError:
        pass

    # Baseline v0.1.25 -> v0.1.26 atomic pointer upgrade and rollback (`cc.json` / `installation.json`)
    base_cfg = tmp_path / "cfg" / "cc.json"
    state_dir = tmp_path / "state"
    eng_cfg = tmp_path / "cfg" / "engine.json"
    state_dir.mkdir(parents=True)

    def make_gen(ver: str) -> Path:
        gen_cfg = state_dir / "generations" / ver / "installation.json"
        scripts_dir = state_dir / "generations" / ver / "scripts"
        scripts_dir.mkdir(parents=True)
        payload = {
            "schema": install_state.INSTALLATION_SCHEMA,
            "facade_protocol": install_state.FACADE_PROTOCOL,
            "version": ver,
            "base_config": str(base_cfg),
            "adapter_config": str(gen_cfg),
            "engine_config": str(eng_cfg),
            "python": sys.executable,
            "state_dir": str(state_dir),
            "runtime_scripts": str(scripts_dir),
        }
        install_state.atomic_write(gen_cfg, payload)
        return gen_cfg

    v25_cfg = make_gen("0.1.25")
    v26_cfg = make_gen("0.1.26")

    # Start at v0.1.25
    install_state.atomic_write(base_cfg, json.loads(v25_cfg.read_text(encoding="utf-8")))
    os.environ["MINDIE_CC_CONFIG"] = str(base_cfg)
    with install_state.installed_binding() as active:
        assert active["version"] == "0.1.25"

    # Upgrade atomically to v0.1.26
    install_state.atomic_write(base_cfg, json.loads(v26_cfg.read_text(encoding="utf-8")))
    with install_state.installed_binding() as active:
        assert active["version"] == "0.1.26"

    # Rollback atomically to v0.1.25
    install_state.atomic_write(base_cfg, json.loads(v25_cfg.read_text(encoding="utf-8")))
    with install_state.installed_binding() as active:
        assert active["version"] == "0.1.25"
