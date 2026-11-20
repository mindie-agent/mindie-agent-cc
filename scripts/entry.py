#!/usr/bin/env python3
"""Thin runtime handoff to `mindie_harness` for the Claude Code host.

[Role] Resolves `cc`, provisions 9-domain bindings via `ensure_knowledge_binding`, rebinds native metadata, and delegates MCP and revocation-safe `Stop`.
[Invariants] `ManagedMCPAdapter("cc")` keeps surfaces separate; `Stop` preserves `inactive` revocation permanence via `run_stop_capture`; remote state defaults to `state_dir/remote` (`claude`).
[Timeouts & Locks] `Stop` hands off within `timeout_sec=6.0`; hooks bypass `.update.lock` and SQLite on cold/passive paths.
[Side-Effect Policy] Writes profile JSON, native rebind stamps, pending markers, and opt-in bounded capture rows.
"""
import hashlib, json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

NATIVE_VERSION, STOP_TIMEOUT, MAX_EVENT = "0.1.32", 6.0, 1048576

def _doc(path: Path) -> dict | None:
    from mindie_harness.atomic import read_json_safe
    val = read_json_safe(path)
    return val if isinstance(val, dict) else None

def _emit(payload: dict | None = None) -> None:
    sys.stdout.buffer.write((json.dumps(payload or {}) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()

def _in_roots(candidate: object, roots: object) -> bool:
    if not isinstance(candidate, str) or not os.path.isabs(candidate) or not isinstance(roots, list):
        return False
    sel = os.path.normcase(os.path.abspath(candidate))
    for root in roots:
        if isinstance(root, str) and os.path.isabs(root):
            norm = os.path.normcase(os.path.abspath(root))
            try:
                if os.path.splitdrive(sel)[0] == os.path.splitdrive(norm)[0] and os.path.commonpath([sel, norm]) == norm:
                    return True
            except ValueError:
                continue
    return False

def initialize_profile(profile) -> tuple[dict | None, Path | None]:
    """Ensure `cc.json` and 9-domain engines exist under one `.update.lock` transaction."""
    from host_adapter import adapter_plugin_root, plugin_root_dir
    from mindie_harness.atomic import atomic_write_json
    from mindie_harness.locks import FileLock, LockTimeoutError
    from mindie_harness.runtime.knowledge_binding import ensure_knowledge_binding
    import mindie_launch

    def _cur_ret(doc: dict | None) -> tuple[dict | None, Path | None]:
        ep = doc.get("engine_config") if isinstance(doc, dict) else None
        return (doc or None), (Path(ep) if isinstance(ep, str) and os.path.isabs(ep) else None)

    lock = FileLock(profile.config_path.with_name(f".{profile.config_path.name}.update.lock"), exclusive=True, timeout=5.0)
    try:
        lock.acquire()
    except LockTimeoutError:
        return _cur_ret(_doc(profile.config_path))
    try:
        cur = _doc(profile.config_path) or {}
        found = mindie_launch._python_from(cur) or mindie_launch.active_python(profile.config_path)
        if found is None:
            return None, None
        python, base_val = str(found), (cur["base_config"] if isinstance(cur.get("base_config"), str) and os.path.isabs(cur["base_config"]) else str(profile.config_path))
        if str(profile.config_path) != base_val and cur.get("schema") == "mindie-cc-installation/1":
            return _cur_ret(cur)
        res = ensure_knowledge_binding(profile, plugin_root=adapter_plugin_root(profile.state_dir), python_executable=python,
                                       install_redactor=False, retire_previous=False, already_locked=True)
        if res.get("status") != "configured":
            return _cur_ret(cur)
        fields = dict(res["binding_fields"])
        updated = {**cur, **fields, "schema": "mindie-cc-installation/1", "facade_protocol": 1, "host": "cc", "python": python,
                   "base_config": base_val, "adapter_config": cur["adapter_config"] if isinstance(cur.get("adapter_config"), str) and os.path.isabs(cur["adapter_config"]) else str(profile.config_path),
                   "engine_config": str(fields["engine_config"]), "domain_configs": dict(fields["domain_configs"]), "runtime_scripts": str(HERE),
                   "state_dir": cur["state_dir"] if isinstance(cur.get("state_dir"), str) and os.path.isabs(cur["state_dir"]) else str(profile.state_dir),
                   "community_config": cur["community_config"] if isinstance(cur.get("community_config"), str) and os.path.isabs(cur["community_config"]) else str(fields["community_config"]),
                   "source_root": cur["source_root"] if isinstance(cur.get("source_root"), str) and os.path.isabs(cur["source_root"]) else str(plugin_root_dir())}
        if updated != cur or not profile.config_path.is_file():
            atomic_write_json(profile.config_path, updated)
        return updated, Path(updated["engine_config"])
    finally:
        lock.release()

def _stop(profile, engine_path: Path | None = None) -> int:
    _ = engine_path
    raw = sys.stdin.buffer.read(MAX_EVENT)
    try:
        event = json.loads(raw.decode("utf-8-sig")) if raw.strip() else {}
    except (UnicodeDecodeError, ValueError):
        _emit(); return 0
    if (not isinstance(event, dict) or event.get("hook_event_name") not in {None, "Stop"}
            or event.get("stop_hook_active") not in {None, False}
            or (isinstance(event.get("agent_id"), str) and event["agent_id"].strip())):
        _emit(); return 0
    from mindie_harness.sharing import sharing_status

    cwd = event["cwd"] if "cwd" in event else event.get("workspace_root")
    status = sharing_status(profile, cwd=cwd if isinstance(cwd, str) else None)
    session, turn, transcript = event.get("session_id"), (event.get("prompt_id") or event.get("turn_id")), event.get("transcript_path")
    if (status.get("tier") not in {"contribute", "local-only"} or not status.get("cwd_in_scope")
            or not isinstance(session, str) or not session.strip() or not isinstance(transcript, str) or not os.path.isabs(transcript)):
        _emit(); return 0
    try:
        from host_adapter import adapter_plugin_root
        from mindie_harness.runtime.knowledge_binding import run_stop_capture
        from mindie_knowledge.loop.activation import Admission
        from transcript import capture_source

        pinned = None
        if profile.admission_db.is_file():
            admission = Admission(profile.admission_db)
            if admission.inspect(session).get("status") == "inactive":
                _emit(); return 0
            existing = admission.knowledge_lease(session)
            pinned = existing if isinstance(existing, dict) and existing.get("activated_at") != 0.0 else None
        source = capture_source(transcript, session, binding=pinned)
        if not _in_roots(source["project_root"], status.get("project_roots")):
            _emit(); return 0
        payload: dict[str, object] = {"hook_event_name": "Stop", "stop_hook_active": False, "harness": "claude",
                                      "session_id": session, "cwd": source["project_root"], "transcript_path": source["transcript_path"]}
        if isinstance(turn, str) and turn.strip():
            payload.update(identity_kind="turn", turn_id=turn.strip())
        else:
            size = Path(source["transcript_path"]).stat().st_size
            payload.update(identity_kind="notification", event_id=hashlib.sha256(f"{session}:{source['transcript_path']}:{size}".encode()).hexdigest()[:32])
        run_stop_capture(profile, payload, plugin_root=adapter_plugin_root(profile.state_dir), timeout_sec=STOP_TIMEOUT)
    except TimeoutError:
        raise
    except Exception:
        pass
    _emit(); return 0

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    from host_adapter import ClaudeHostAdapter

    adapter = ClaudeHostAdapter()
    profile = adapter.resolve_profile()
    os.environ.setdefault("REMOTE_DEV_STATE_DIR", str(profile.state_dir / "remote"))
    os.environ.setdefault("REMOTE_DEV_HARNESS_PROFILE", "claude")
    if args and args[0] == "hook":
        op = args[1].lower().replace("_", "-") if len(args) > 1 else ""
        if op == "session-start":
            from mindie_harness.runtime.activation import write_pending_update_check
            try:
                write_pending_update_check(profile, reason="session_start")
            except TimeoutError:
                raise
            except (OSError, ValueError):
                pass
            _emit(); return 0
        if op == "stop":
            try:
                return _stop(profile)
            except TimeoutError:
                _emit(); return 0
        _emit(); return 0
    cfg, _ = initialize_profile(profile)
    if cfg is not None and str(profile.config_path) == cfg.get("base_config", str(profile.config_path)) and cfg.get("adapter_config") == str(profile.config_path):
        try:
            from mindie_harness.mcp.gateway import rebind_native_plugin_if_needed
            rebind_native_plugin_if_needed(profile, native_version=NATIVE_VERSION, runtime_scripts=HERE)
        except TimeoutError:
            raise
        except Exception:
            pass
    if args and args[0] == "mcp":
        from mindie_harness.api import serve_mcp
        return serve_mcp(args[1] if len(args) > 1 else "all", profile=profile, adapter=adapter)
    _emit(); return 0

if __name__ == "__main__":
    raise SystemExit(main())
