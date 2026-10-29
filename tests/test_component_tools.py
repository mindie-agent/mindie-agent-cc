"""Exercise native binding and real component parsers without knowledge setup."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import identity
import mcp_server
import mindie_launch
import process_probe

SESSION = "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"
CAPTURE = ROOT / "tests/fixtures/profiler/rank_0"


@pytest.fixture
def component_config(tmp_path, monkeypatch):
    config = Path(os.environ["MINDIE_CC_CONFIG"])
    config.write_text(json.dumps({
        "python": sys.executable,
        "state_dir": str(tmp_path / "state"),
        "base_config": str(config),
        "engine_config": str(tmp_path / "must-not-read-knowledge.json"),
    }))
    monkeypatch.setenv("MINDIE_COORDINATOR_STATE_DIR", str(tmp_path / "coordinator"))
    return config


def bound_call(name, arguments, *, cwd=None):
    call_id = "component_" + uuid.uuid4().hex
    native = identity.canonical_native_name("components", name)
    identity.publish_bind(SESSION, call_id, native, arguments, cwd=cwd)
    message = {"id": call_id, "method": "tools/call", "params": {
        "name": name, "arguments": arguments,
        "_meta": {"claudecode/toolUseId": call_id},
    }}
    return mcp_server.handle("components", message)["result"]


def test_native_manifest_discovery_and_hook_reach_real_local_diagnostics(component_config, tmp_path):
    manifest = json.loads((ROOT / ".mcp.json").read_text())
    surface = manifest["mcpServers"]["components"]["args"][-1]
    listed = mcp_server.handle(surface, {"id": 1, "method": "tools/list"})["result"]["tools"]
    status = next(tool for tool in listed if tool["name"] == "diagnostics_status")
    assert not any(tool["name"].startswith("knowledge_") for tool in listed)
    name = identity.canonical_native_name(surface, status["name"])
    hooks = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]["PreToolUse"]
    assert any(re.fullmatch(hook["matcher"], name) for hook in hooks)
    call_id = "component_hook_status"
    event = {
        "session_id": SESSION, "tool_use_id": call_id, "tool_name": name,
        "tool_input": {}, "cwd": str(tmp_path),
    }
    hook = subprocess.run(
        [sys.executable, str(ROOT / "scripts/bridge.py"), "pretool"],
        input=json.dumps(event), text=True, capture_output=True, timeout=5,
    )
    assert hook.returncode == 0, hook.stderr
    request = {"id": 2, "method": "tools/call", "params": {
        "name": status["name"], "arguments": {},
        "_meta": {"claudecode/toolUseId": call_id},
    }}
    result = mcp_server.handle(surface, request)["result"]
    assert not result["isError"], result
    assert result["structuredContent"]["result"]["status"] == "not_configured"
    replay = mcp_server.handle(surface, request)["result"]
    assert replay["isError"]
    assert "replay" in replay["content"][0]["text"]
    assert not Path(json.loads(component_config.read_text())["engine_config"]).exists()


def test_profiler_inventory_uses_real_capture_without_path(component_config, monkeypatch):
    monkeypatch.setenv("PATH", "")
    result = bound_call("profiler", {"argv": ["inventory", "--root", str(CAPTURE)]})
    assert not result["isError"], result
    inventory = result["structuredContent"]["result"]
    assert inventory["rank_count"] == 1
    assert inventory["ranks"][0]["files"]


def test_live_component_override_is_read_from_the_stable_profile(component_config, tmp_path, monkeypatch):
    profile = json.loads(component_config.read_text())
    profile["component_commands"] = {
        "profiler": [sys.executable, "-I", "-m", "mindie_profiler"],
    }
    component_config.write_text(json.dumps(profile))
    frozen = tmp_path / "installation.adapter.json"
    snapshot = dict(profile)
    snapshot["component_commands"] = {"profiler": [str(tmp_path / "old-unavailable-command")]}
    frozen.write_text(json.dumps(snapshot))
    monkeypatch.setenv("MINDIE_CC_CONFIG", str(frozen))
    result = bound_call("profiler", {"argv": ["inventory", "--root", str(CAPTURE)]})
    assert not result["isError"], result
    assert result["structuredContent"]["result"]["rank_count"] == 1
    assert json.loads(frozen.read_text())["component_commands"] == snapshot["component_commands"]


def test_sim_parser_error_preserves_stderr_and_exit_status(component_config, tmp_path):
    result = bound_call("sim", {"argv": ["invalid-component-operation"]}, cwd=str(tmp_path))
    assert result["isError"], result
    payload = result["structuredContent"]
    assert payload["exit_code"] == 2
    assert payload["stdout"] == ""
    assert "invalid choice" in payload["stderr"]
    assert "invalid-component-operation" in payload["stderr"]


@pytest.mark.parametrize("explicit_cwd", [False, True])
def test_sim_output_file_uses_native_or_explicit_working_directory(component_config, tmp_path, explicit_cwd):
    argv = [
        "pipeline-microbatches", "--stages", "3,5", "--microbatches", "2",
        "--output", "graph.json",
    ]
    arguments = {"argv": argv}
    if explicit_cwd:
        arguments["cwd"] = str(tmp_path)
    result = bound_call("sim", arguments, cwd=str(ROOT if explicit_cwd else tmp_path))
    assert not result["isError"], result
    graph = json.loads((tmp_path / "graph.json").read_text())
    assert graph["makespan_ms"] == 13.0
    assert len(graph["timeline"]) == 4
    assert result["structuredContent"]["result"] == graph


def test_coordinator_inventory_and_local_bundle(component_config, tmp_path):
    result = bound_call("coordinator", {"argv": ["hosts", "--action", "list"]})
    assert not result["isError"], result
    assert result["structuredContent"]["result"]["outcome"] == "success"
    logs = tmp_path / "logs"
    logs.mkdir()
    bundle = bound_call("diagnostics_bundle", {"root": str(logs), "output": str(tmp_path / "support.zip")})
    assert not bundle["isError"], bundle
    assert (tmp_path / "support.zip").is_file()


def test_missing_component_is_visible_without_running_a_fallback(component_config, tmp_path):
    adapter = json.loads(component_config.read_text())
    adapter["component_commands"] = {"sim": [str(tmp_path / "absent-runtime")]}
    component_config.write_text(json.dumps(adapter))
    response = bound_call("sim", {"argv": ["--help"]})
    assert response["isError"]
    assert "absent-runtime" in response["content"][0]["text"]


def test_component_stderr_obeys_the_same_output_bound(component_config):
    response = bound_call("sim", {
        "argv": ["invalid-component-operation"], "max_output_tokens": 8,
    })
    assert response["isError"]
    assert "output exceeds the bound" in response["content"][0]["text"]
    payload = response["structuredContent"]
    assert payload["status"] == "output_limit"
    assert payload["stdout"] == "" and payload["stderr"]
    assert len(payload["stderr"].encode()) <= 32


@pytest.mark.parametrize("mode", ["wait", "overflow"])
def test_interrupted_component_preserves_bounded_output_and_reaps_child(component_config, mode):
    adapter = json.loads(component_config.read_text())
    adapter["component_commands"] = {
        "sim": [sys.executable, str(ROOT / "tests/fixtures/component_boundary.py")],
    }
    component_config.write_text(json.dumps(adapter))
    result = bound_call("sim", {
        "argv": [mode], "timeout_ms": 1000, "max_output_tokens": 32,
    })
    assert result["isError"], result
    payload = result["structuredContent"]
    assert payload["status"] == ("timeout" if mode == "wait" else "output_limit")
    assert payload["stdout"].startswith("component began pid=")
    pid = int(payload["stdout"].splitlines()[0].split("pid=")[1])
    assert not process_probe.alive(pid)
    assert len((payload["stdout"] + payload["stderr"]).encode()) <= 128
    assert payload["output_truncated"] is (mode == "overflow")
    assert "remote_outcome" not in payload and "retryable" not in payload
    if mode == "wait":
        assert "component warning" in payload["stderr"]
    assert payload["exit_code"] is not None


def test_component_identity_timeout_does_not_claim_a_remote_outcome(component_config, monkeypatch):
    def storage_timeout(*_args):
        raise TimeoutError("local bind store timed out")

    monkeypatch.setattr(mcp_server, "claim_bind", storage_timeout)
    result = bound_call("diagnostics_status", {})
    assert result["isError"]
    assert result["structuredContent"] == {"stage": "timeout"}


def test_facade_delivers_invalid_component_input_to_the_bound_helper(component_config):
    adapter = json.loads(component_config.read_text())
    adapter.update(
        schema="mindie-cc-installation/1", facade_protocol=1,
        adapter_config=str(component_config), runtime_scripts=str(ROOT / "scripts"),
    )
    component_config.write_text(json.dumps(adapter))
    call_id = "invalid_component_timeout"
    args = {"argv": ["--help"], "timeout_ms": 0}
    identity.publish_bind(
        SESSION, call_id, identity.canonical_native_name("components", "sim"), args,
    )
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "sim", "arguments": args, "_meta": {"claudecode/toolUseId": call_id},
    }}
    result = mindie_launch._dispatch("components", json.dumps(request).encode(), 1)["result"]
    assert result["isError"] and "timeout_ms" in result["content"][0]["text"]
    replay = mcp_server.handle("components", request)["result"]
    assert replay["isError"] and "replay" in replay["content"][0]["text"]


def test_completed_component_survives_bind_accounting_timeout(component_config, tmp_path, monkeypatch):
    calls = []

    def accounting_timeout(call_id, succeeded):
        calls.append((call_id, succeeded))
        raise TimeoutError("receipt store timeout")

    monkeypatch.setattr(mcp_server, "finish_bind", accounting_timeout)
    logs = tmp_path / "logs"
    logs.mkdir()
    output = tmp_path / "support.zip"
    result = bound_call("diagnostics_bundle", {"root": str(logs), "output": str(output)})
    assert not result["isError"], result
    assert result["structuredContent"]["exit_code"] == 0
    assert result["structuredContent"]["result"]
    assert "bind_warning" in result["structuredContent"]
    assert output.is_file()
    assert len(calls) == 1 and calls[0][1] is True


def test_component_dispatch_budget_covers_requested_wait_and_cleanup():
    tools = mcp_server.handle("components", {"id": 1, "method": "tools/list"})["result"]["tools"]
    maximum = max(tool["inputSchema"]["properties"]["timeout_ms"]["maximum"] for tool in tools)
    message = {"method": "tools/call", "params": {
        "name": "sim", "arguments": {"argv": [], "timeout_ms": maximum},
    }}
    outer = mindie_launch._dispatch_budget("components", message)
    assert outer >= maximum / 1000 + 5 + 5 + (2 + 2 + 2)
    native = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["components"]
    assert native["timeout"] >= outer * 1000
    assert int(native["env"]["MCP_TOOL_TIMEOUT"]) >= outer * 1000
