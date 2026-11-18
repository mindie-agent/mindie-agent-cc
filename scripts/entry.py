#!/usr/bin/env python3
"""Thin runtime handoff to `mindie_harness` for the Claude Code host.

[Role] Resolves `cc`, ensures `cc.json`/`cc.engine.json`, rebinds native metadata, and delegates MCP and consent-gated `Stop`.
[Invariants] `ManagedMCPAdapter("cc")` keeps surfaces separate; `Stop` re-checks sharing tier and cwd; remote state defaults to `state_dir/remote` with profile `claude`.
[Timeouts & Locks] `Stop` hands off with `timeout=6.0`; harness owns generation locks.
[Side-Effect Policy] Writes profile JSON, rebinding metadata, the pending marker, and one bounded capture row.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

NATIVE_VERSION = "0.1.31"
DEFAULT_DOMAIN = "vllm-ascend"
STOP_TIMEOUT = 6.0
MAX_EVENT = 1048576

def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(Path(path).read_bytes().decode("utf-8-sig")) if Path(path).is_file() else None
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None

def _write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

def _emit(payload: dict | None = None) -> None:
    sys.stdout.buffer.write((json.dumps(payload or {}) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()

def _in_roots(candidate: object, roots: object) -> bool:
    if not isinstance(candidate, str) or not os.path.isabs(candidate) or not isinstance(roots, list):
        return False
    selected = os.path.normcase(os.path.abspath(candidate))
    for root in roots:
        if isinstance(root, str) and os.path.isabs(root):
            normalized = os.path.normcase(os.path.abspath(root))
            try:
                if (os.path.splitdrive(selected)[0] == os.path.splitdrive(normalized)[0]
                        and os.path.commonpath([selected, normalized]) == normalized):
                    return True
            except ValueError:
                continue
    return False

def initialize_profile(profile) -> tuple[dict | None, Path | None]:
    """Ensure `cc.json`/`cc.engine.json` exist; only within an active runtime."""
    from mindie_harness.locks import FileLock
    import mindie_launch

    lock = FileLock(profile.config_path.with_name(f".{profile.config_path.name}.update.lock"), exclusive=True, timeout=5.0)
    lock.acquire()
    try:
        cfg = _read_json(profile.config_path) or {}
        found = mindie_launch.active_python(profile.config_path)
        python = cfg.get("python") if isinstance(cfg.get("python"), str) and Path(cfg["python"]).is_file() else (str(found) if found else None)
        if python is None:
            return None, None
        raw = cfg.get("engine_config")
        engine_path = Path(raw) if isinstance(raw, str) and os.path.isabs(raw) else profile.config_path.with_name("cc.engine.json")
        defaults = dict(root=str((profile.data_dir / "knowledge").resolve()), domain=DEFAULT_DOMAIN,
                        admission_path=str(profile.admission_db), capture_mode="public-transcript",
                        community_config=str(profile.config_path.with_name("mindie-community.json")))
        prev_engine = _read_json(engine_path) or {}
        engine = {**defaults, **prev_engine, "transcript_adapter": str(HERE / "transcript.py")}
        if engine != prev_engine or not engine_path.is_file():
            _write_json(engine_path, engine)
        base_val = cfg["base_config"] if isinstance(cfg.get("base_config"), str) and os.path.isabs(cfg["base_config"]) else str(profile.config_path)
        adapter_val = cfg["adapter_config"] if isinstance(cfg.get("adapter_config"), str) and os.path.isabs(cfg["adapter_config"]) else str(profile.config_path)
        if str(profile.config_path) != base_val and cfg.get("schema") == "mindie-cc-installation/1":
            return cfg, engine_path
        state_val = cfg["state_dir"] if isinstance(cfg.get("state_dir"), str) and os.path.isabs(cfg["state_dir"]) else str(profile.state_dir)
        updated = {**cfg, "schema": "mindie-cc-installation/1", "facade_protocol": 1, "host": "cc",
                   "python": python, "base_config": base_val, "adapter_config": adapter_val,
                   "engine_config": str(engine_path), "runtime_scripts": str(HERE),
                   "state_dir": state_val, "community_config": engine["community_config"]}
        if updated != cfg or not profile.config_path.is_file():
            _write_json(profile.config_path, updated)
        return updated, engine_path
    finally:
        lock.release()

def _stop(profile, engine_path: Path | None) -> int:
    raw = sys.stdin.buffer.read(MAX_EVENT)
    try:
        event = json.loads(raw.decode("utf-8-sig")) if raw.strip() else {}
    except (UnicodeDecodeError, ValueError):
        _emit()
        return 0
    if (not isinstance(event, dict) or event.get("hook_event_name") not in {None, "Stop"}
            or event.get("stop_hook_active") not in {None, False}
            or (isinstance(event.get("agent_id"), str) and event["agent_id"].strip())):
        _emit()
        return 0
    from mindie_harness.sharing import sharing_status

    cwd = event["cwd"] if "cwd" in event else event.get("workspace_root")
    status = sharing_status(profile, cwd=cwd if isinstance(cwd, str) else None)
    session, turn, transcript = event.get("session_id"), (event.get("prompt_id") or event.get("turn_id")), event.get("transcript_path")
    if status.get("tier") not in {"contribute", "local-only"} or not status.get("cwd_in_scope"):
        _emit()
        return 0
    if not isinstance(session, str) or not session or not isinstance(transcript, str) or not os.path.isabs(transcript):
        _emit()
        return 0
    try:
        from mindie_knowledge.loop.activation import Admission
        from mindie_knowledge.loop.cli import capture_hook, config_at
        from mindie_knowledge.loop.handoff import _store_path, prepare_schema
        from transcript import capture_source

        if engine_path is None:
            _emit()
            return 0
        admission = Admission(profile.admission_db)
        existing = admission.knowledge_lease(session) if profile.admission_db.is_file() else None
        pinned = existing if isinstance(existing, dict) and existing.get("activated_at") != 0.0 else None
        source = capture_source(transcript, session, binding=pinned)
        if not _in_roots(source["project_root"], status.get("project_roots")):
            _emit()
            return 0
        if pinned is None:
            profile.admission_db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            lease = admission.activate(session, project_root=source["project_root"], root_session=session, not_before=0.0)
        else:
            lease = pinned
        if not _store_path(config_at(str(engine_path))).exists():
            prepare_schema(str(engine_path))
        payload = dict(hook_event_name="Stop", session_id=session, mindie_activation=lease.get("token"),
                       harness="claude", transcript_path=source["transcript_path"])
        if isinstance(turn, str) and turn.strip():
            payload.update(identity_kind="turn", turn_id=turn.strip())
        else:
            size = Path(source["transcript_path"]).stat().st_size
            payload.update(identity_kind="notification",
                           event_id=hashlib.sha256(f"{session}:{source['transcript_path']}:{size}".encode()).hexdigest()[:32])
        capture_hook(str(engine_path), payload, timeout=STOP_TIMEOUT)
    except TimeoutError:
        raise
    except Exception:
        pass
    _emit()
    return 0

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    from host_adapter import ClaudeHostAdapter

    adapter = ClaudeHostAdapter()
    profile = adapter.resolve_profile()
    os.environ.setdefault("REMOTE_DEV_STATE_DIR", str(profile.state_dir / "remote"))
    os.environ.setdefault("REMOTE_DEV_HARNESS_PROFILE", "claude")
    cfg, engine_path = initialize_profile(profile)
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
            _emit()
            return 0
        if op in {"pretool", "pre-tool-use", "prompt", "user-prompt-submit"}:
            _emit()
            return 0
        if op == "stop":
            try:
                return _stop(profile, engine_path)
            except TimeoutError:
                _emit()
                return 0
    _emit()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
