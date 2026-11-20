"""Hook fast paths, MCP surface dispatch, revocation permanence, and setup CLI tests.

[Role]
Verifies `< 30ms` SQLite-free `Stop` and cold `SessionStart` fast paths,
`Stop` revocation permanence (`inactive` sessions are never revived), positive
and negative `knowledge`/`remote`/`components` MCP dispatch, and 9-domain setup.
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
EXPECTED_DOMAINS = {
    "vllm-ascend", "operators", "xllm", "mindie-sd", "infer-core",
    "infer-distributed", "infer-observability", "infer-ops-compat", "agent-engineering",
}


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


def _run_entry(monkeypatch, argv: list[str], stdin: bytes = b"") -> tuple[int, bytes, float]:
    import entry

    stdout = _Stream()
    monkeypatch.setattr(sys, "stdin", _Stream(stdin))
    monkeypatch.setattr(sys, "stdout", stdout)
    started = time.perf_counter()
    code = entry.main(argv)
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
    import session_start

    code, output, elapsed = _run_launch(monkeypatch, ["hook", "pretool"], b"not-json")
    assert (code, output) == (0, b"{}\n")
    assert elapsed < 0.030

    # Cold start (no cc.json / active_python is None) MUST be marker-only (< 30ms, zero uv sync / SQLite)!
    stdout = _Stream()
    monkeypatch.setattr(sys, "stdin", _Stream())
    monkeypatch.setattr(sys, "stdout", stdout)
    started = time.perf_counter()
    cold_code = session_start.main(["hook", "session-start"])
    cold_elapsed = time.perf_counter() - started
    assert (cold_code, stdout.buffer.getvalue()) == (0, b"{}\n")
    assert cold_elapsed < 0.030
    marker = Path(os.environ["MINDIE_DATA_HOME"]) / "state" / "cc" / "pending-update-check.json"
    assert marker.is_file()
    assert json.loads(marker.read_text(encoding="utf-8"))["schema"] == "mindie-pending-update/1"
    assert _no_sqlite(isolated_environment)

    # Warm entry.py hook session-start also stays marker-only and SQLite-free.
    marker.unlink()
    _write_config(Path(os.environ["MINDIE_CC_CONFIG"]), {"python": sys.executable})
    code, output, elapsed = _run_entry(monkeypatch, ["hook", "session-start"])
    assert (code, output) == (0, b"{}\n")
    assert elapsed < 0.030
    assert marker.is_file()
    assert _no_sqlite(isolated_environment)


def test_stop_never_revives_revoked_inactive_session(monkeypatch, isolated_environment) -> None:
    from mindie_harness.sharing import configure_sharing
    from mindie_knowledge.loop.activation import Admission

    _, profile = _adapter()
    workspace = (isolated_environment / "work" / "project").resolve()
    workspace.mkdir(parents=True)
    _write_config(profile.config_path, {"python": sys.executable})
    configure_sharing(profile, choice="local-only", project_roots=[workspace])

    projects_dir = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "workspace"
    projects_dir.mkdir(parents=True, exist_ok=True)
    transcript_file = projects_dir / f"{SESSION}.jsonl"
    records = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": SESSION, "timestamp": "2026-01-01T00:00:00Z"},
        {"type": "user", "sessionId": SESSION, "timestamp": "2026-01-01T00:00:01Z",
         "cwd": str(workspace), "message": {"content": "investigate aclgraph capture"}},
        {"type": "assistant", "sessionId": SESSION, "timestamp": "2026-01-01T00:00:02Z",
         "cwd": str(workspace), "message": {"content": [{"type": "text", "text": "verified"}]}},
    ]
    transcript_file.write_bytes(b"".join(json.dumps(row).encode("utf-8") + b"\n" for row in records))

    # First Stop mints an on-demand lease for a missing authorized session.
    turn_1 = json.dumps({
        "hook_event_name": "Stop", "session_id": SESSION, "prompt_id": "turn-1",
        "cwd": str(workspace), "transcript_path": str(transcript_file),
    }).encode("utf-8")
    code, output, _ = _run_entry(monkeypatch, ["hook", "stop"], turn_1)
    assert (code, output) == (0, b"{}\n")

    admission = Admission(profile.admission_db)
    assert admission.inspect(SESSION)["status"] == "active"

    # Explicit user revocation sets status == "inactive" (enabled=0).
    assert admission.deactivate(SESSION) is True
    assert admission.inspect(SESSION)["status"] == "inactive"
    assert admission.inspect(SESSION)["enabled"] is False

    # Subsequent Stop turns MUST NEVER revive the revoked session!
    turn_2 = json.dumps({
        "hook_event_name": "Stop", "session_id": SESSION, "prompt_id": "turn-2",
        "cwd": str(workspace), "transcript_path": str(transcript_file),
    }).encode("utf-8")
    code, output, _ = _run_entry(monkeypatch, ["hook", "stop"], turn_2)
    assert (code, output) == (0, b"{}\n")
    assert admission.inspect(SESSION)["status"] == "inactive"
    assert admission.inspect(SESSION)["enabled"] is False
    assert admission.active_lease(SESSION) is None


def _adapter():
    import host_adapter

    adapter = host_adapter.ClaudeHostAdapter()
    return adapter, adapter.resolve_profile()


def _tools(surface: str) -> set[str]:
    adapter, profile = _adapter()
    return {tool["name"] for tool in adapter.mcp_tools(surface, profile)}


def _call(surface: str, requests: list[dict]) -> dict:
    import threading
    from mindie_harness.api import serve_mcp

    adapter, profile = _adapter()
    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    stdin_stream = os.fdopen(in_r, "rb")
    in_writer = os.fdopen(in_w, "wb")
    out_reader = os.fdopen(out_r, "rb")
    stdout_stream = os.fdopen(out_w, "wb")
    codes: list[int] = []
    worker = threading.Thread(
        target=lambda: codes.append(serve_mcp(surface, profile=profile, adapter=adapter, stdin=stdin_stream, stdout=stdout_stream)),
        daemon=True,
    )
    worker.start()
    replies: dict = {}
    try:
        in_writer.write(b"\n".join(json.dumps(item).encode("utf-8") for item in requests) + b"\n")
        in_writer.flush()
        for _ in requests:
            line = out_reader.readline()
            assert line, f"unexpected EOF waiting for {surface} MCP replies"
            msg = json.loads(line)
            replies[msg["id"]] = msg
    finally:
        in_writer.close()
        worker.join(timeout=10.0)
        stdin_stream.close()
        stdout_stream.close()
        out_reader.close()
    assert codes == [0]
    return replies


def _request(call_id: int, name: str, arguments: dict | None = None) -> dict:
    return {"jsonrpc": "2.0", "id": call_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}}}


def test_mcp_surfaces_are_strictly_separated(isolated_environment) -> None:
    assert _tools("knowledge") == KNOWLEDGE_TOOLS
    assert _tools("remote") == REMOTE_TOOLS
    components = _tools("components")
    assert {"mindie_doctor", "mindie_status", "diagnostics_status", "diagnostics_bundle",
            "profiler", "sim", "coordinator", "npu_top", "debugger",
            "rsi_loop", "experiment_ledger"} <= components
    assert not any(name.startswith("knowledge_") for name in components)
    assert not any(name.startswith("remote_") for name in components)

    # Wrong-surface calls fail closed with unknown_operation.
    knowledge = _call("knowledge", [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        _request(2, "profiler"),
        _request(6, "knowledge_query", {"query": "aclgraph"}),
    ])
    assert "unknown_operation" in knowledge[2]["result"]["content"][0]["text"]
    assert knowledge[6]["result"]["isError"] is False

    remote = _call("remote", [
        _request(3, "remote_BashOutput"),
        _request(4, "knowledge_query"),
        _request(7, "remote_auth", {"action": "status", "host": "127.0.0.1"}),
    ])
    assert "unknown_operation" in remote[3]["result"]["content"][0]["text"]
    assert "unknown_operation" in remote[4]["result"]["content"][0]["text"]
    assert remote[7]["result"]["isError"] is False

    component = _call("components", [
        _request(5, "knowledge_query"),
        _request(8, "sim", {"argv": ["--help"]}),
        _request(9, "mindie_doctor", {}),
    ])
    assert "unknown_operation" in component[5]["result"]["content"][0]["text"]
    assert component[8]["result"]["isError"] is False, component[8]
    assert component[9]["result"]["isError"] is False
    assert "mindie-doctor/1" in component[9]["result"]["content"][0]["text"]

    # Zero admission.sqlite3 created across all public read-only MCP calls!
    assert not list(isolated_environment.rglob("*admission*.sqlite*"))


def _setup(*args: str) -> dict:
    proc = subprocess.run([sys.executable, "-B", str(SCRIPTS / "setup.py"), *args],
                          capture_output=True, text=True, timeout=60, creationflags=_NO_WIN)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_setup_sharing_and_doctor(isolated_environment) -> None:
    workspace = isolated_environment / "workspace"
    workspace.mkdir()
    initialized = _setup("init")
    assert initialized["status"] == "configured"
    assert set(initialized["domain_configs"]) == EXPECTED_DOMAINS
    for domain, eng_path in initialized["domain_configs"].items():
        assert Path(eng_path).is_file(), domain
        assert "/engines/cc/" in Path(eng_path).as_posix()

    local = _setup("sharing", "--local-only", "--project-root", str(workspace))
    assert local["choice"] == "local-only" and local["enabled"] is True
    assert [Path(item).resolve() for item in local["project_roots"]] == [workspace.resolve()]
    assert _setup("sharing", "--status")["tier"] == "local-only"
    assert _setup("sharing", "--enable", "--project-root", str(workspace))["choice"] == "contribute"
    assert _setup("sharing", "--disable")["choice"] == "disabled"
    doctor = _setup("doctor", "--timeout", "10")
    assert doctor["isError"] is False
    assert "mindie-doctor/1" in doctor["content"][0]["text"]
    bad = subprocess.run([sys.executable, "-B", str(SCRIPTS / "setup.py"), "sharing", "--enable", "--disable"],
                         capture_output=True, text=True, timeout=20, creationflags=_NO_WIN)
    assert bad.returncode != 0


def test_cold_seed_uses_devnull_stdin_and_fails_nonzero_without_valid_runtime(monkeypatch, isolated_environment) -> None:
    import mindie_launch

    cfg = Path(os.environ["MINDIE_CC_CONFIG"])
    calls: list[dict] = []

    class _Done:
        returncode = 0

    def _fake_run(cmd, **kw):
        calls.append({"cmd": cmd, "kw": kw})
        return _Done()

    client_stdin = _Stream(b'{"jsonrpc":"2.0","id":1,"method":"initialize"}\n')
    monkeypatch.setattr(sys, "stdin", client_stdin)
    monkeypatch.setattr(mindie_launch.subprocess, "run", _fake_run)

    # Seed exits 0 without creating a valid runtime interpreter -> MUST exit non-zero (1) and leave stdin unread.
    assert mindie_launch._cold(["mcp", "knowledge"], cfg) == 1
    assert len(calls) == 1
    assert calls[0]["kw"]["stdin"] is subprocess.DEVNULL
    assert calls[0]["kw"]["stdout"] is sys.stderr
    assert calls[0]["kw"]["stderr"] is sys.stderr
    assert client_stdin.buffer.tell() == 0

    # WindowsApps 0-byte store alias stub MUST be rejected as an invalid runtime interpreter.
    win_stub = isolated_environment / "Microsoft" / "WindowsApps" / "python.exe"
    win_stub.parent.mkdir(parents=True)
    win_stub.write_bytes(b"")
    _write_config(cfg, {"python": str(win_stub)})
    if os.name == "nt":
        assert mindie_launch.active_python(cfg) is None
        assert mindie_launch._cold(["mcp", "knowledge"], cfg) == 1


def test_single_lock_binding_respects_deferred_and_keeps_vllm_ascend_engine(monkeypatch, isolated_environment) -> None:
    import entry
    from mindie_harness.runtime import knowledge_binding as kb
    import setup as cc_setup

    initialized = cc_setup.initialize_profile(domain="operators")
    assert initialized["status"] == "configured"
    assert initialized["engine_config"].endswith("vllm-ascend.engine.json")
    cfg_path = Path(initialized["config_path"])
    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert saved["engine_config"].endswith("vllm-ascend.engine.json")

    seen_flags: list[bool] = []

    def _deferred(profile, **kw):
        seen_flags.append(bool(kw.get("already_locked")))
        return {"status": "deferred", "reason": "knowledge_service_busy", "reportable": False}

    monkeypatch.setattr(kb, "ensure_knowledge_binding", _deferred)
    deferred_res = cc_setup.initialize_profile(domain="operators")
    assert deferred_res["status"] == "deferred"
    assert json.loads(cfg_path.read_text(encoding="utf-8")) == saved

    _, profile = _adapter()
    cur_doc, cur_ep = entry.initialize_profile(profile)
    assert cur_doc == saved
    assert cur_ep == Path(saved["engine_config"])
    assert json.loads(cfg_path.read_text(encoding="utf-8")) == saved
    assert seen_flags == [True, True]


def test_wheel_layout_resolves_transcript_adapter_without_noop_fallback(isolated_environment) -> None:
    import importlib.util
    import shutil
    from mindie_harness.runtime.knowledge_binding import ensure_knowledge_binding

    wheel_pkg = isolated_environment / "site-packages" / "mindie_cc"
    (wheel_pkg / "resources" / "assets").mkdir(parents=True)
    shutil.copy2(SCRIPTS / "host_adapter.py", wheel_pkg / "host_adapter.py")
    shutil.copy2(SCRIPTS / "transcript.py", wheel_pkg / "transcript.py")
    shutil.copy2(ROOT / "assets" / "seed-lock.json", wheel_pkg / "resources" / "assets" / "seed-lock.json")
    assert not (wheel_pkg.parent / "scripts" / "transcript.py").exists()
    assert not (wheel_pkg / "resources" / "scripts" / "transcript.py").exists()

    spec = importlib.util.spec_from_file_location("_wheel_host_adapter", wheel_pkg / "host_adapter.py")
    assert spec and spec.loader
    wheel_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wheel_mod)

    _, profile = _adapter()
    eff_root = wheel_mod.adapter_plugin_root(profile.state_dir)
    res = ensure_knowledge_binding(profile, plugin_root=eff_root, python_executable=sys.executable)
    assert res["status"] == "configured"
    assert res["standalone_adapter"] is False
    assert Path(res["transcript_adapter"]).name == "transcript.py"
    assert Path(res["transcript_adapter"]).read_bytes() == (SCRIPTS / "transcript.py").read_bytes()
