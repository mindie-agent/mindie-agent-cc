#!/usr/bin/env python3
"""Pure-stdlib cold-start launcher for the MindIE Claude Code plugin.

[Role] Resolves the profile, serves passive hooks and the consent-gated `Stop` fast path, and hands off to `entry.py` or the bundled seed bootstrap.
[Invariants] Zero third-party imports and SQLite on hook fast paths; `Stop` requires a `contribute`/`local-only` tier, enabled community sharing, and absolute `project_roots`; `os.path.commonpath` on the same drive keeps `/work/app-other` out of `/work/app`.
[Timeouts & Locks] No locks; at most one bounded `1048577`-byte stdin read on `Stop`.
[Side-Effect Policy] Writes `{}`, the atomic pending-update marker, and one child process.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

_NO_WIN = 0x08000000 if os.name == "nt" else 0
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MAX_STDIN = 1048577
PENDING_SCHEMA = "mindie-pending-update/1"

def _home(kind: str) -> Path:
    explicit = os.environ.get("MINDIE_CONFIG_HOME" if kind == "config" else "MINDIE_DATA_HOME")
    if explicit:
        return Path(explicit)
    if os.name == "nt":
        default = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") if kind == "config" else Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    else:
        default = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") if kind == "config" else Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return default / "mindie-agent"

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
    return Path(raw) if isinstance(raw, str) and raw and Path(raw).is_file() else None

def active_python(cfg_path: Path) -> Path | None:
    """Resolve the prepared interpreter from `cc.json`, legacy installs, or runtime.json."""
    cfg = _read_json(cfg_path)
    found = _python_from(cfg)
    if found is not None:
        return found
    if isinstance(cfg, dict):
        refs = [cfg.get(key) for key in ("adapter_config", "installation", "installation_config")]
        release = cfg.get("component_release")
        if isinstance(release, str) and release:
            refs.append(str(Path(release) / "runtime.json"))
        for ref in refs:
            if isinstance(ref, str) and ref:
                found = _python_from(_read_json(Path(ref)))
                if found is not None:
                    return found
    for legacy in (cfg_path.with_name("installation.json"), cfg_path.with_name("cc.installation.json")):
        found = _python_from(_read_json(legacy))
        if found is not None:
            return found
    return None

def _stop_roots(cfg_path: Path) -> list[str]:
    community = _read_json(cfg_path.parent / "mindie-community.json")
    consent = _read_json(cfg_path.parent / "mindie-consent.json")
    if (community is not None and community.get("enabled") is not True) or consent is None:
        return []
    choice, legacy = consent.get("choice"), consent.get("enabled")
    if not (choice in {"contribute", "local-only"} or (choice is None and legacy is True and legacy is not False)):
        return []
    raw = community.get("project_roots") if isinstance(community, dict) else None
    if not isinstance(raw, list):
        raw = consent.get("project_roots")
    if not isinstance(raw, list) or not raw:
        return []
    roots = []
    for item in raw:
        if not isinstance(item, str) or not os.path.isabs(item):
            return []
        roots.append(os.path.normcase(os.path.abspath(item)))
    return roots

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
        value = event.get(key)
        if not isinstance(value, str) or not os.path.isabs(value):
            continue
        candidate = os.path.normcase(os.path.abspath(value))
        for root in roots:
            if os.path.splitdrive(candidate)[0] != os.path.splitdrive(root)[0]:
                continue
            try:
                if os.path.commonpath([candidate, root]) == root:
                    return True
            except ValueError:
                continue
    return False

def _enqueue_pending() -> None:
    target = state_dir()
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker, tmp = target / "pending-update-check.json", target / f".pending-update-check.{os.getpid()}.tmp"
    try:
        tmp.write_text(json.dumps({"schema": PENDING_SCHEMA, "host": "cc", "reason": "session_start", "requested_at": time.time()}) + "\n", encoding="utf-8")
        os.replace(tmp, marker)
    finally:
        tmp.unlink(missing_ok=True)

def _quiet() -> int:
    sys.stdout.buffer.write(b"{}\n")
    sys.stdout.buffer.flush()
    return 0

def _run(cmd: list[str], stdin_buf: bytes | None, *, timeout: float | None = None, capture: bool = False) -> int:
    if stdin_buf is not None or capture:
        return subprocess.run(cmd, input=stdin_buf, capture_output=capture, timeout=timeout, creationflags=_NO_WIN).returncode
    try: sys.stdin.fileno(); sys.stdout.fileno(); sys.stderr.fileno(); io_kw = {"stdin": sys.stdin, "stdout": sys.stdout, "stderr": sys.stderr}
    except Exception: io_kw = {}
    return subprocess.run(cmd, **io_kw, timeout=timeout, creationflags=_NO_WIN).returncode

def _cold(args: list[str], cfg_path: Path) -> int:
    from host_adapter import verify_bundled_seed

    try:
        seed = verify_bundled_seed()
    except TimeoutError:
        raise
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"MindIE seed unavailable: {type(exc).__name__}\n")
        return 1
    command = [sys.executable, "-I", "-X", "utf8", str(seed["path"]), "--host", "cc",
               "--plugin-root", str(HERE.parent), "--config", str(cfg_path)]
    boot = subprocess.run(command, stdout=sys.stderr, stderr=sys.stderr, creationflags=_NO_WIN)
    python = active_python(cfg_path)
    if python is None:
        return boot.returncode
    dispatch = [str(python), "-I", "-X", "utf8", str(HERE / "entry.py"), *args]
    if os.name != "nt":
        os.execv(str(python), dispatch)
    return _run(dispatch, None)

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cfg_path = config_path()
    if len(args) >= 2 and args[0].lower() == "hook":
        op = args[1].lower().replace("_", "-")
        if op in {"pretool", "pre-tool-use", "prompt", "user-prompt-submit"}:
            return _quiet()
        if op == "stop":
            roots = _stop_roots(cfg_path)
            if not roots:
                return _quiet()
            raw = sys.stdin.buffer.read(MAX_STDIN)
            if not _in_scope(raw, roots):
                return _quiet()
            python = active_python(cfg_path)
            if python is None:
                return _quiet()
            try:
                _run([str(python), "-I", "-X", "utf8", str(HERE / "entry.py"), "hook", "stop"], raw, timeout=8.0, capture=True)
            except subprocess.TimeoutExpired:
                pass
            return _quiet()
        if op == "session-start":
            if active_python(cfg_path) is not None or "--offline" in os.environ.get("_MINDIE_UV_FLAGS", ""):
                _enqueue_pending()
                return _quiet()
            return _cold(args, cfg_path)
    python = active_python(cfg_path)
    if python is not None:
        dispatch = [str(python), "-I", "-X", "utf8", str(HERE / "entry.py"), *args]
        if os.name != "nt":
            os.execv(str(python), dispatch)
        return _run(dispatch, None)
    return _cold(args, cfg_path)

if __name__ == "__main__":
    raise SystemExit(main())
