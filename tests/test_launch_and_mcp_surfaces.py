"""Hook fast paths, MCP surface separation, and setup CLI contract tests.

[Role]
Verifies the `< 30ms` SQLite-free `Stop` fast paths, passive hooks, the
`knowledge`/`remote`/`components` tool separation, and `setup.py` sharing and
doctor delegation.
[Invariants]
- Every subprocess uses the isolated environment from `conftest.py`.
- No test creates SQLite outside its `tmp_path`.
[Timeouts & Locks]
- In-process hook timing plus bounded `60s` setup subprocesses.
[Side-Effect Policy]
- Writes only inside the isolated test tree.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SESSION = "11111111-2222-3333-4444-555555555555"
_NO_WIN = 0x08000000 if os.name == "nt" else 0
KNOWLEDGE_TOOLS = {"knowledge_query", "knowledge_explain", "knowledge_feedback",
                   "knowledge_distill", "knowledge_sync"}
REMOTE_TOOLS = {"remote_Read", "remote_Write", "remote_Edit", "remote_Bash", "remote_Glob",
                "remote_Grep", "remote_TaskOutput", "remote_NotebookEdit", "remote_TaskStop",
                "remote_auth", "remote_probe", "remote_artifact_pull", "remote_artifact_push"}


class _Stream:
    def __init__(self, data: bytes = b"") -> None:
        self.buffer = io.BytesIO(data)


def _run_launch(monkeypatch, argv: list[str], stdin: bytes = b"") -> tuple[int, bytes, float]:
    import mindie_launch

    stdout = _Stream()
    monkeypatch.setattr(sys, "stdin", _Stream(stdin))
    monkeypatch.setattr(sys, "stdout", stdout)
    started = time.perf_counter()
    code = mindie_launch.main(argv)
    elapsed = time.perf_counter() - started
    return code, stdout.buffer.getvalue(), elapsed


def _write_config(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _authorize(tmp_path: Path, root: Path, *, choice: str = "contribute") -> Path:
    cfg = Path(os.environ["MINDIE_CC_CONFIG"])
    _write_config(cfg, {"python": sys.executable})
    _write_config(cfg.parent / "mindie-community.json", {
        "schema": "mindie-community-config/1", "enabled": True, "project_roots": [str(root)]})
    _write_config(cfg.parent / "mindie-consent.json", {
        "schema": "mindie-consent/1", "choice": choice, "enabled": True, "project_roots": [str(root)]})
    return cfg


def _no_sqlite(root: Path) -> bool:
    return not list(root.rglob("*.sqlite*"))


def test_stop_fast_path_is_quiet_when_unconfigured(monkeypatch, isolated_environment) -> None:
    code, output, elapsed = _run_launch(monkeypatch, ["hook", "stop"])
    assert code == 0
    assert output == b"{}\n"
    assert elapsed < 0.030
    assert _no_sqlite(isolated_environment)


def test_stop_fast_path_is_quiet_when_disabled(monkeypatch, isolated_environment) -> None:
    cfg = Path(os.environ["MINDIE_CC_CONFIG"])
    _write_config(cfg, {"python": sys.executable})
    _write_config(cfg.parent / "mindie-community.json", {
        "schema": "mindie-community-config/1", "enabled": False, "project_roots": [str(isolated_environment)]})
    _write_config(cfg.parent / "mindie-consent.json", {
        "schema": "mindie-consent/1", "choice": "disabled", "enabled": False})
    code, output, elapsed = _run_launch(monkeypatch, ["hook", "stop"])
    assert (code, output) == (0, b"{}\n")
    assert elapsed < 0.030
    assert _no_sqlite(isolated_environment)


def test_stop_fast_path_rejects_sibling_prefix(monkeypatch, isolated_environment) -> None:
    root = isolated_environment / "work" / "app"
    sibling = isolated_environment / "work" / "app-other"
    root.mkdir(parents=True)
    sibling.mkdir(parents=True)
    _authorize(isolated_environment, root)
    event = json.dumps({"session_id": SESSION, "prompt_id": "turn-1", "cwd": str(sibling),
                        "project_root": str(root),
                        "transcript_path": str(isolated_environment / "missing.jsonl")}).encode()
    code, output, elapsed = _run_launch(monkeypatch, ["hook", "stop"], event)
    assert (code, output) == (0, b"{}\n")
    assert elapsed < 0.030
    assert _no_sqlite(isolated_environment)


def test_pretool_is_passive_and_session_start_enqueues(monkeypatch, isolated_environment) -> None:
    code, output, elapsed = _run_launch(monkeypatch, ["hook", "pretool"], b"not-json")
    assert (code, output) == (0, b"{}\n")
    assert elapsed < 0.030
    _write_config(Path(os.environ["MINDIE_CC_CONFIG"]), {"python": sys.executable})
    code, output, elapsed = _run_launch(monkeypatch, ["hook", "session-start"])
    assert (code, output) == (0, b"{}\n")
    assert elapsed < 0.100
    marker = Path(os.environ["MINDIE_DATA_HOME"]) / "state" / "cc" / "pending-update-check.json"
    assert marker.is_file()
    assert json.loads(marker.read_text(encoding="utf-8"))["schema"] == "mindie-pending-update/1"
    assert _no_sqlite(isolated_environment)


def _adapter():
    import host_adapter

    adapter = host_adapter.ClaudeHostAdapter()
    return adapter, adapter.resolve_profile()


def _tools(surface: str) -> set[str]:
    adapter, profile = _adapter()
    return {tool["name"] for tool in adapter.mcp_tools(surface, profile)}


def _call(surface: str, requests: list[dict]) -> dict:
    from mindie_harness.api import serve_mcp

    adapter, profile = _adapter()
    stdin = io.BytesIO(b"\n".join(json.dumps(item).encode("utf-8") for item in requests) + b"\n")
    stdout = io.BytesIO()
    assert serve_mcp(surface, profile=profile, adapter=adapter, stdin=stdin, stdout=stdout) == 0
    return {message["id"]: message for message in map(json.loads, stdout.getvalue().splitlines())}


def _request(call_id: int, name: str) -> dict:
    return {"jsonrpc": "2.0", "id": call_id, "method": "tools/call", "params": {"name": name, "arguments": {}}}


def test_mcp_surfaces_are_strictly_separated() -> None:
    assert _tools("knowledge") == KNOWLEDGE_TOOLS
    assert _tools("remote") == REMOTE_TOOLS
    components = _tools("components")
    assert {"mindie_doctor", "mindie_status", "diagnostics_status", "profiler",
            "sim", "coordinator", "debugger"} <= components
    assert not any(name.startswith("knowledge_") for name in components)
    assert not any(name.startswith("remote_") for name in components)

    knowledge = _call("knowledge", [{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                                    _request(2, "profiler")])
    assert "unknown_operation" in knowledge[2]["result"]["content"][0]["text"]
    remote = _call("remote", [_request(3, "remote_BashOutput"), _request(4, "knowledge_query")])
    assert "unknown_operation" in remote[3]["result"]["content"][0]["text"]
    assert "unknown_operation" in remote[4]["result"]["content"][0]["text"]
    component = _call("components", [_request(5, "knowledge_query")])
    assert "unknown_operation" in component[5]["result"]["content"][0]["text"]


def _setup(*args: str) -> dict:
    proc = subprocess.run([sys.executable, "-B", str(SCRIPTS / "setup.py"), *args],
                          capture_output=True, text=True, timeout=60, creationflags=_NO_WIN)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_setup_sharing_and_doctor(isolated_environment) -> None:
    workspace = isolated_environment / "workspace"
    workspace.mkdir()
    local = _setup("sharing", "--local-only", "--project-root", str(workspace))
    assert local["choice"] == "local-only" and local["enabled"] is True
    assert [Path(item).resolve() for item in local["project_roots"]] == [workspace.resolve()]
    assert _setup("sharing", "--status")["tier"] == "local-only"
    assert _setup("sharing", "--enable", "--project-root", str(workspace))["choice"] == "contribute"
    assert _setup("sharing", "--disable")["choice"] == "disabled"
    doctor = _setup("doctor", "--timeout", "2")
    assert doctor["isError"] is False
    assert "mindie-doctor/1" in doctor["content"][0]["text"]
    bad = subprocess.run([sys.executable, "-B", str(SCRIPTS / "setup.py"), "sharing", "--enable", "--disable"],
                         capture_output=True, text=True, timeout=20, creationflags=_NO_WIN)
    assert bad.returncode != 0
