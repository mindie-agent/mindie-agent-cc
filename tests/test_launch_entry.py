"""Outer launcher diagnostics. Not native acceptance."""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

from support import SCRIPTS, env_for, make_config

ROOT = Path(__file__).resolve().parents[1]
LAUNCH = ROOT / "scripts" / "mindie_launch.py"


def test_mcp_dispatch_has_no_total_deadline_and_keeps_caller_cancel(tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    import mindie_launch

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "mcp_server.py").touch()
    current = dict(generation=str(tmp_path), python=sys.executable,
                   adapter_config=str(tmp_path / "adapter.json"))
    response = dict(jsonrpc="2.0", id="slow-read", result={"content": []})
    raw = json.dumps(dict(jsonrpc="2.0", id="slow-read", method="tools/call")).encode()
    cancel = threading.Event()
    for surface in ("knowledge", "remote"):
        with patch.object(mindie_launch, "_state_dir", return_value=tmp_path), \
                patch.object(mindie_launch, "_shared_lock", return_value=object()), \
                patch.object(mindie_launch, "_current", return_value=current), \
                patch.object(mindie_launch, "_release") as release, \
                patch.object(mindie_launch.bounded, "run",
                             return_value=json.dumps(response)) as runner:
            assert mindie_launch._dispatch(surface, raw, "slow-read", cancel) == response
        assert runner.call_args.kwargs.get("timeout") is None
        assert runner.call_args.kwargs["cancel"] is cancel
        assert runner.call_args.args[0][-2:] == [surface, "--once"]
        if surface == "knowledge":
            assert runner.call_args.kwargs["max_output"] == mindie_launch.KNOWLEDGE_MAX_OUTPUT
        release.assert_called_once()


def test_only_knowledge_business_calls_can_transfer_service_ownership(tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    import mindie_launch

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "mcp_server.py").touch()
    current = dict(generation=str(tmp_path), python=sys.executable,
                   adapter_config=str(tmp_path / "adapter.json"))
    response = dict(jsonrpc="2.0", id="ownership", result={"content": []})
    cases = [
        ("knowledge", "tools/call", "knowledge_query", True),
        ("knowledge", "tools/call", "knowledge_explain", True),
        ("knowledge", "tools/call", "knowledge_feedback", True),
        ("knowledge", "tools/call", "mindie_status", False),
        ("knowledge", "tools/list", None, False),
        ("remote", "tools/call", "remote_bash", False),
    ]
    for surface, method, name, expected in cases:
        raw = json.dumps(dict(jsonrpc="2.0", id="ownership", method=method,
                              params=dict(name=name))).encode()
        with patch.object(mindie_launch, "_state_dir", return_value=tmp_path), \
                patch.object(mindie_launch, "_shared_lock", return_value=object()), \
                patch.object(mindie_launch, "_current", return_value=current), \
                patch.object(mindie_launch, "_release"), \
                patch.object(mindie_launch.bounded, "run",
                             return_value=json.dumps(response)) as runner:
            mindie_launch._dispatch(surface, raw, "ownership")
        assert runner.call_args.kwargs["allow_service"] is expected


def test_only_stop_and_explicit_contribution_entries_allow_service_transfer():
    import mindie_launch
    assert mindie_launch._hook_can_start_service("stop", b"{}") is True
    for command, expected in (
        ("mindie-agent", True), ("mindie-agent:init", True),
        ("mindie-agent:sharing-enable", True), ("mindie-agent:status", False),
        ("mindie-agent:reporting-enable", False), ("mindie-agent:deactivate", False),
    ):
        event = dict(expansion_type="slash_command", command_source="plugin", command_name=command)
        raw = json.dumps(event).encode()
        assert mindie_launch._hook_can_start_service("expansion", raw) is expected
        assert mindie_launch._hook_can_start_service("pretool", raw) is False
        event["command_source"] = "user"
        assert mindie_launch._hook_can_start_service("expansion", json.dumps(event).encode()) is False

def _run(tmp, env, payload=b""):
    env = dict(env)
    env["MINDIE_DIAGNOSTICS_ROOT"] = str(tmp / "diag")
    proc = subprocess.run(
        [sys.executable, str(LAUNCH), "hook", "stop"],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=5,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert json.loads(proc.stdout.decode() or "{}") == {}
    events = list((tmp / "diag").rglob("*.jsonl"))
    return "\n".join(path.read_text(encoding="utf-8") for path in events)


def test_missing_config_and_explicit_off_stay_quiet(tmp_path):
    env = os.environ.copy()
    env.pop("MINDIE_CC_CONFIG", None)
    env.pop("PYTHONPATH", None)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "empty-config")
    assert _run(tmp_path, env) == ""

    config = make_config(tmp_path / "off", sharing=False)
    assert _run(tmp_path / "off", env_for(config)) == ""


def test_existing_bad_community_and_missing_generation_are_recorded(tmp_path):
    bad = tmp_path / "bad"
    config = make_config(bad, sharing=True)
    (bad / "cc.community.json").write_text("{", encoding="utf-8")
    text = _run(bad, env_for(config))
    assert "configuration" in text
    assert "forwarded" not in text

    armed = tmp_path / "armed"
    config = make_config(armed, sharing=True)
    text = _run(armed, env_for(config))
    assert "generation-unavailable" in text
    assert "forwarded" not in text
