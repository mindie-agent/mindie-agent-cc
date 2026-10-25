"""Outer launcher diagnostics. Not native acceptance."""

import json
import os
import subprocess
import sys
import threading
from contextlib import nullcontext
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
    current = dict(scripts=str(scripts), python=sys.executable,
                   adapter_config=str(make_config(tmp_path)))
    response = dict(jsonrpc="2.0", id="slow-read", result={"content": []})
    raw = json.dumps(dict(jsonrpc="2.0", id="slow-read", method="tools/call")).encode()
    cancel = threading.Event()
    for surface in ("knowledge", "remote"):
        with patch.object(mindie_launch, "_runtime", return_value=current), \
                patch.object(mindie_launch, "installed_binding", return_value=nullcontext({})), \
                patch.object(mindie_launch.bounded, "run",
                             return_value=json.dumps(response)) as runner:
            assert mindie_launch._dispatch(surface, raw, "slow-read", cancel) == response
        expected = (mindie_launch.REMOTE_MIN_BUDGET_SECONDS if surface == "remote"
                    else mindie_launch.CALL_CHILD_BUDGET)
        assert runner.call_args.kwargs.get("timeout") == expected
        assert runner.call_args.kwargs["cancel"] is cancel
        assert runner.call_args.args[0][-2:] == [surface, "--once"]
        expected_output = (mindie_launch.KNOWLEDGE_MAX_OUTPUT if surface == "knowledge"
                           else mindie_launch.REMOTE_MAX_OUTPUT)
        assert runner.call_args.kwargs["max_output"] == expected_output


def test_only_knowledge_business_calls_can_transfer_service_ownership(tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    import mindie_launch

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "mcp_server.py").touch()
    current = dict(scripts=str(scripts), python=sys.executable,
                   adapter_config=str(make_config(tmp_path)))
    response = dict(jsonrpc="2.0", id="ownership", result={"content": []})
    cases = [
        ("knowledge", "tools/call", "knowledge_query", True),
        ("knowledge", "tools/call", "knowledge_explain", True),
        ("knowledge", "tools/call", "knowledge_feedback", True),
        ("knowledge", "tools/call", "knowledge_distill", True),
        ("knowledge", "tools/call", "mindie_status", False),
        ("knowledge", "tools/list", None, False),
        ("remote", "tools/call", "remote_bash", False),
    ]
    for surface, method, name, expected in cases:
        raw = json.dumps(dict(jsonrpc="2.0", id="ownership", method=method,
                              params=dict(name=name))).encode()
        with patch.object(mindie_launch, "_runtime", return_value=current), \
                patch.object(mindie_launch, "installed_binding", return_value=nullcontext({})), \
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

def _run(tmp, env, payload=b"", *, warning=False,
         expected_stage=None, expected_category=None):
    env = dict(env)
    env["MINDIE_DIAGNOSTICS_ROOT"] = str(tmp / "diag")
    env["MINDIE_DIAGNOSTICS_CONFIG"] = str(tmp / "diagnostics.json")
    proc = subprocess.run(
        [sys.executable, str(LAUNCH), "hook", "stop"],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=5,
    )
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    paths = sorted((tmp / "diag").rglob("*.jsonl*"))
    evidence = (f"returncode={proc.returncode}\nstdout={stdout!r}\nstderr={stderr!r}\n"
                f"diagnostic_paths={[str(path) for path in paths]}")
    assert proc.returncode == 0, f"hook failed\n{evidence}"
    try:
        value = json.loads(stdout or "{}")
    except ValueError as exc:
        raise AssertionError(f"hook stdout is not JSON: {exc}\n{evidence}") from None
    if warning:
        assert set(value) == {"systemMessage"}, evidence
        assert "encountered an error" in value["systemMessage"], evidence
        assert "decision" not in value and "continue" not in value, evidence
    else:
        assert value == {}, evidence
    records = []
    text = []
    for path in paths:
        raw = path.read_text(encoding="utf-8")
        text.append(raw)
        for line in raw.splitlines():
            try:
                records.append(json.loads(line))
            except ValueError as exc:
                raise AssertionError(f"invalid diagnostic record {path}: {exc}\n{evidence}") from None
    if expected_stage is not None or expected_category is not None:
        def matches(record):
            attributes = record.get("attributes") if isinstance(record, dict) else None
            if not isinstance(attributes, dict):
                return False
            return ((expected_stage is None or attributes.get("stage") == expected_stage)
                    and (expected_category is None
                         or attributes.get("category") == expected_category))
        observed = []
        for record in records:
            attributes = record.get("attributes") if isinstance(record, dict) else None
            if isinstance(attributes, dict):
                observed.append((record.get("operation"), attributes.get("stage"),
                                 attributes.get("category")))
        assert any(matches(record) for record in records), (
            f"expected a recorded diagnostic event "
            f"stage={expected_stage!r} category={expected_category!r}; "
            f"observed={observed}\n{evidence}")
    return "\n".join(text)


def test_missing_config_and_explicit_off_stay_quiet(tmp_path):
    env = os.environ.copy()
    env.pop("MINDIE_CC_CONFIG", None)
    env.pop("PYTHONPATH", None)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "empty-config")
    assert _run(tmp_path, env) == ""

    config = make_config(tmp_path / "off", sharing=False)
    assert _run(tmp_path / "off", env_for(config)) == ""


def test_existing_bad_community_is_recorded_and_user_visible(tmp_path):
    bad = tmp_path / "bad"
    config = make_config(bad, sharing=True)
    (bad / "cc.community.json").write_text("{", encoding="utf-8")
    text = _run(bad, env_for(config), warning=True,
                expected_stage="sharing-probe", expected_category="configuration")
    assert "configuration" in text
    assert "forwarded" not in text


def test_invalid_community_is_not_explicit_off(tmp_path):
    for name, value in (
        ("missing-enabled", {}),
        ("wrong-schema", {"schema": "unsupported", "enabled": False}),
        ("invalid-idle", {"schema": "mindie-community-config/1",
                          "enabled": False, "idle_seconds": 1}),
    ):
        root = tmp_path / name
        config = make_config(root, sharing=False)
        (root / "cc.community.json").write_text(json.dumps(value), encoding="utf-8")
        assert "configuration" in _run(
            root, env_for(config), warning=True,
            expected_stage="sharing-probe", expected_category="configuration")


def test_bad_adapter_configuration_has_nonblocking_warning(tmp_path):
    config = make_config(tmp_path, sharing=False)
    config.write_text('{"PRIVATE_SENTINEL":', encoding="utf-8")
    text = _run(tmp_path, env_for(config), warning=True,
                expected_stage="installation", expected_category="configuration")
    assert "configuration" in text
    assert "PRIVATE_SENTINEL" not in text


def test_helper_failure_keeps_visible_warning_even_if_diagnostic_write_fails(capsys):
    import mindie_launch

    with patch.object(mindie_launch, "_sharing_enabled", return_value=True), \
            patch.object(mindie_launch, "binding_snapshot", return_value={
                "python": sys.executable, "runtime_scripts": str(SCRIPTS),
                "adapter_config": "fixture",
            }), \
            patch.object(mindie_launch.Path, "exists", return_value=True), \
            patch.object(mindie_launch, "_read_hook_stdin", return_value=b"{}"), \
            patch.object(mindie_launch.bounded, "run", side_effect=RuntimeError("PRIVATE_SENTINEL")), \
            patch.object(mindie_launch.diagnostic_support, "failure", side_effect=OSError("full")):
        assert mindie_launch._hook("stop") == 0
    output = capsys.readouterr()
    assert set(json.loads(output.out)) == {"systemMessage"}
    assert "PRIVATE_SENTINEL" not in output.out


def test_initialize_reports_cached_installed_distribution_version():
    import mindie_launch
    import product_contract

    product_contract.installed_version.cache_clear()
    try:
        with patch.object(product_contract.metadata, "version", return_value="0.0.2") as version:
            for _ in range(2):
                response = mindie_launch._local_answer(
                    {"jsonrpc": "2.0", "id": "init", "method": "initialize"})
                assert response["result"]["serverInfo"]["version"] == "0.0.2"
        version.assert_called_once_with("mindie-agent-cc")
    finally:
        product_contract.installed_version.cache_clear()


def test_incomplete_first_start_does_not_claim_an_available_old_owner(capsys):
    import mindie_launch
    with patch("harness.require_binding_support", return_value="2.1.269"), \
            patch.object(mindie_launch, "_read_hook_stdin",
                         return_value=b'{"hook_event_name":"SessionStart","source":"startup"}'), \
            patch.object(mindie_launch, "installed_binding", return_value=nullcontext({
            "python": sys.executable, "runtime_scripts": str(SCRIPTS),
            "adapter_config": "fixture",
    })), patch.object(mindie_launch.bounded, "run",
                     return_value='{"status":"incomplete"}'):
        assert mindie_launch.main(["hook", "session-start"]) == 0
    message = json.loads(capsys.readouterr().out)["systemMessage"]
    assert message == "MindIE startup maintenance is incomplete."


def test_optional_maintenance_timeout_keeps_ready_binding_and_warns(tmp_path, capsys):
    import maintenance
    import mindie_launch

    scripts = tmp_path / "maintenance-worker"
    scripts.mkdir()
    (scripts / "maintenance.py").write_text(
        "import time\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    binding = dict(python=sys.executable, runtime_scripts=str(scripts),
                   adapter_config=str(tmp_path / "ready-adapter.json"))
    Path(binding["adapter_config"]).write_text('{"ready":true}', encoding="utf-8")
    with patch("harness.require_binding_support", return_value="2.1.299"), \
            patch.object(mindie_launch, "_read_hook_stdin", return_value=b"{}"), \
            patch.object(mindie_launch, "installed_binding",
                         return_value=nullcontext(binding)), \
            patch.object(mindie_launch, "_record_hook"), \
            patch.object(maintenance, "TOTAL_BUDGET", 0.2):
        assert mindie_launch.main(["hook", "session-start"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "systemMessage": "MindIE startup maintenance is incomplete.",
    }
    assert Path(binding["adapter_config"]).read_text() == '{"ready":true}'


def test_session_start_forwards_native_fork_payload_to_selected_maintenance(capsys, tmp_path):
    import mindie_launch
    payload = b'{"hook_event_name":"SessionStart","source":"fork","session_id":"native-child"}'
    with patch("harness.require_binding_support", return_value="2.1.299"), \
            patch.object(mindie_launch, "_read_hook_stdin", return_value=payload), \
            patch.object(mindie_launch, "installed_binding", return_value=nullcontext({
                "python": sys.executable, "runtime_scripts": str(SCRIPTS),
                "adapter_config": str(make_config(tmp_path)),
            })), patch.object(mindie_launch.bounded, "run",
                              return_value='{"status":"ready"}') as runner:
        assert mindie_launch.main(["hook", "session-start"]) == 0
    assert runner.call_args.args[1] == payload
    assert Path(runner.call_args.args[0][-1]).name == "maintenance.py"
    assert json.loads(capsys.readouterr().out) == {}


def test_missing_session_start_payload_warns_without_reporting_ready(capsys):
    import mindie_launch
    with patch.object(mindie_launch, "_read_hook_stdin", return_value=b""), \
            patch.object(mindie_launch, "_record_hook"), \
            patch.object(mindie_launch.bounded, "run") as runner:
        assert mindie_launch.main(["hook", "session-start"]) == 0
    assert set(json.loads(capsys.readouterr().out)) == {"systemMessage"}
    runner.assert_not_called()


def test_each_mcp_request_selects_current_complete_helper_without_old_entry_reselecting(tmp_path):
    import mindie_launch
    config = make_config(tmp_path)
    fixed = json.loads(config.read_text(encoding="utf-8"))
    raw = b'{"jsonrpc":"2.0","id":"one","method":"tools/call","params":{"name":"remote_bash"}}'
    response = '{"jsonrpc":"2.0","id":"one","result":{"content":[]}}'
    with patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(config)}), \
            patch.object(mindie_launch.bounded, "run", return_value=response) as runner:
        assert mindie_launch._dispatch("remote", raw, "one", binding=fixed)["id"] == "one"
        runner.reset_mock()
        selected = dict(fixed, engine_config=str(tmp_path / "other.engine.json"),
                        runtime_scripts=str(tmp_path / "new-scripts"),
                        python=str(tmp_path / "new-python"))
        config.write_text(json.dumps(selected), encoding="utf-8")
        assert mindie_launch._dispatch("remote", raw, "one", binding=fixed)["id"] == "one"
        assert runner.call_args.args[0][:2] == [
            selected["python"], str(Path(selected["runtime_scripts"]) / "mcp_server.py")]
        assert json.loads(config.read_text(encoding="utf-8")) == selected
