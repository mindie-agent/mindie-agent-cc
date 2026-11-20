#!/usr/bin/env python3
"""Pure-stdlib cold-start launcher for the MindIE Claude Code plugin.

[Role] Resolves the profile, serves passive hooks, cold-start marker-only `SessionStart`, and consent-gated `Stop`, and hands off MCP to `entry.py` or seed bootstrap.
[Invariants] Zero third-party imports and zero SQLite on hook fast paths; `SessionStart` never runs `uv sync` or runtime builds; `Stop` checks consent tier and `os.path.commonpath` roots first.
[Timeouts & Locks] No locks; at most one bounded `1048577`-byte stdin read on `Stop`.
[Side-Effect Policy] Writes `{}`, the atomic pending-update marker, and at most one child process.
"""
import json, os, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MAX_STDIN = 1048577
PENDING_SCHEMA = "mindie-pending-update/1"

def _home(kind: str) -> Path:
    explicit = os.environ.get("MINDIE_CONFIG_HOME" if kind == "config" else "MINDIE_DATA_HOME")
    if explicit:
        return Path(explicit)
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") if kind == "config" else Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") if kind == "config" else Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return base / "mindie-agent"

def config_path() -> Path:
    explicit = os.environ.get("MINDIE_CC_CONFIG") or os.environ.get("MINDIE_AGENT_CONFIG")
    return (Path(explicit) if explicit else _home("config") / "cc.json").expanduser().resolve()

def state_dir() -> Path:
    return (_home("data") / "state" / "cc").expanduser().resolve()

def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(Path(path).read_bytes().decode("utf-8-sig"))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None

def _python_from(doc: dict | None) -> Path | None:
    raw = doc.get("python") if isinstance(doc, dict) else None
    if not isinstance(raw, str) or not raw or (os.name == "nt" and "\\microsoft\\windowsapps\\" in raw.replace("/", "\\").lower()):
        return None
    try:
        return Path(raw) if Path(raw).is_file() and (os.name != "nt" or os.stat(raw).st_size > 0) else None
    except TimeoutError:
        raise
    except OSError:
        return None

def active_python(cfg_path: Path) -> Path | None:
    """Resolve the prepared interpreter from `cc.json`, legacy installs, or runtime.json."""
    cfg = _read_json(cfg_path)
    if (found := _python_from(cfg)) is not None:
        return found
    if isinstance(cfg, dict):
        refs = [cfg.get(k) for k in ("adapter_config", "installation", "installation_config")]
        if isinstance(rel := cfg.get("component_release"), str) and rel:
            refs.append(str(Path(rel) / "runtime.json"))
        for ref in refs:
            if isinstance(ref, str) and ref and (found := _python_from(_read_json(Path(ref)))) is not None:
                return found
    for legacy in (cfg_path.with_name("installation.json"), cfg_path.with_name("cc.installation.json")):
        if (found := _python_from(_read_json(legacy))) is not None:
            return found
    return None

def _stop_roots(cfg_path: Path) -> list[str]:
    community, consent = _read_json(cfg_path.parent / "mindie-community.json"), _read_json(cfg_path.parent / "mindie-consent.json")
    if (community is not None and community.get("enabled") is not True) or consent is None:
        return []
    choice, legacy = consent.get("choice"), consent.get("enabled")
    if not (choice in {"contribute", "local-only"} or (choice is None and legacy is True)):
        return []
    raw = community.get("project_roots") if isinstance(community, dict) and isinstance(community.get("project_roots"), list) else consent.get("project_roots")
    if not isinstance(raw, list) or not raw or any(not isinstance(i, str) or not os.path.isabs(i) for i in raw):
        return []
    return [os.path.normcase(os.path.abspath(i)) for i in raw]

def _in_scope(raw: bytes, roots: list[str]) -> bool:
    if not 0 < len(raw) <= MAX_STDIN or not raw.strip():
        return False
    try:
        event = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return False
    if not isinstance(event, dict):
        return False
    keys = ("cwd",) if isinstance(event.get("cwd"), str) and os.path.isabs(event["cwd"]) else ("cwd", "workspace", "project_root")
    for key in keys:
        val = event.get(key)
        if not isinstance(val, str) or not os.path.isabs(val):
            continue
        cand = os.path.normcase(os.path.abspath(val))
        for root in roots:
            if os.path.splitdrive(cand)[0] != os.path.splitdrive(root)[0]:
                continue
            try:
                if os.path.commonpath([cand, root]) == root:
                    return True
            except ValueError:
                continue
    return False

def _enqueue_pending(cfg_path: Path | None = None) -> None:
    cfg = _read_json(cfg_path) if cfg_path is not None else None
    raw_state = cfg.get("state_dir") if isinstance(cfg, dict) else None
    target = Path(raw_state) if isinstance(raw_state, str) and os.path.isabs(raw_state) else state_dir()
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker, tmp = target / "pending-update-check.json", target / f".pending-update-check.{os.getpid()}.tmp"
    try:
        tmp.write_text(json.dumps({"schema": PENDING_SCHEMA, "host": "cc", "reason": "session_start", "requested_at": time.time()}) + "\n", encoding="utf-8")
        os.replace(tmp, marker)
    finally:
        tmp.unlink(missing_ok=True)

def _quiet() -> int:
    sys.stdout.buffer.write(b"{}\n"); sys.stdout.buffer.flush(); return 0

def _run(cmd: list[str], stdin_buf: bytes | None, *, timeout: float | None = None, capture: bool = False) -> int:
    if stdin_buf is not None or capture:
        return subprocess.run(cmd, input=stdin_buf, capture_output=capture, timeout=timeout, creationflags=CREATE_NO_WINDOW).returncode
    try:
        sys.stdin.fileno(); sys.stdout.fileno(); sys.stderr.fileno()
        io_kw = {"stdin": sys.stdin, "stdout": sys.stdout, "stderr": sys.stderr}
    except TimeoutError:
        raise
    except Exception:
        io_kw = {}
    return subprocess.run(cmd, **io_kw, timeout=timeout, creationflags=CREATE_NO_WINDOW).returncode

def _exec_entry(python: Path, args: list[str]) -> int:
    dispatch = [str(python), "-I", "-X", "utf8", str(HERE / "entry.py"), *args]
    if os.name != "nt":
        os.execv(str(python), dispatch)
    return _run(dispatch, None)

def _cold(args: list[str], cfg_path: Path) -> int:
    from host_adapter import plugin_root_dir, verify_bundled_seed
    try:
        seed = verify_bundled_seed()
    except TimeoutError:
        raise
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"MindIE seed unavailable: {type(exc).__name__}\n"); return 1
    cmd = [sys.executable, "-I", "-X", "utf8", str(seed["path"]), "--host", "cc", "--plugin-root", str(plugin_root_dir()), "--config", str(cfg_path)]
    boot = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr, creationflags=CREATE_NO_WINDOW)
    if (python := active_python(cfg_path)) is None:
        sys.stderr.write("MindIE runtime interpreter unavailable after seed bootstrap\n")
        return boot.returncode if boot.returncode != 0 else 1
    return _exec_entry(python, args)

def main(argv: list[str] | None = None) -> int:
    args, cfg_path = list(sys.argv[1:] if argv is None else argv), config_path()
    if len(args) >= 2 and args[0].lower() == "hook":
        op = args[1].lower().replace("_", "-")
        if op in {"pretool", "pre-tool-use", "prompt", "user-prompt-submit"}:
            return _quiet()
        if op == "stop":
            roots = _stop_roots(cfg_path)
            raw = sys.stdin.buffer.read(MAX_STDIN) if roots else b""
            if (python := (active_python(cfg_path) if roots and _in_scope(raw, roots) else None)) is not None:
                try:
                    _run([str(python), "-I", "-X", "utf8", str(HERE / "entry.py"), "hook", "stop"], raw, timeout=8.0, capture=True)
                except subprocess.TimeoutExpired:
                    pass
            return _quiet()
        if op == "session-start":
            try:
                _enqueue_pending(cfg_path)
            except TimeoutError:
                raise
            except OSError:
                pass
            return _quiet()
    return _exec_entry(python, args) if (python := active_python(cfg_path)) is not None else _cold(args, cfg_path)

if __name__ == "__main__":
    raise SystemExit(main())
