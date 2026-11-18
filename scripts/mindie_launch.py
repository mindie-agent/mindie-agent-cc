#!/usr/bin/env python3
"""Stable native facade; each operation dispatches the selected complete helper.

[Role]
Top-level entrypoint invoked by Claude Code hooks (`SessionStart`, `PreToolUse`,
`Stop`) and `.mcp.json` (`knowledge`, `remote`, `components`).

[Invariants]
- Resolves the locked runtime binding (`install_state.py`) or runs cold-start
  `bootstrap.py` when required.
- Passive `PreToolUse` and `UserPromptSubmit` hooks never block tool execution or
  create SQLite state when unconfigured; `Stop` checks sharing consent and
  authorized `project_roots` before spawning capture workers.

[Timeouts & Locks]
- `HOOK_TOTAL = 10.0s`, `HOOK_LOCK_BUDGET = 2.0s`. SessionStart outer timeout
  covers `UV_PYTHON_PROVISION` + `BOOTSTRAP_TOTAL` + cleanup grace; the shared
  deadline is read before `--source` setup. Remote MCP: native wait + 60s grace.

[Side-Effect Policy]
- Dispatches into `bridge.py`, `mcp_server.py`, or background `maintenance.py` /
  `release_update.py` under the locked runtime interpreter.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path
import stat as stat_mod

sys.path.insert(0, str(Path(__file__).resolve().parent))

HOOK_TOTAL = float(os.environ.get("MINDIE_HOOK_TOTAL_SECONDS") or 10.0)
HOOK_LOCK_BUDGET = float(os.environ.get("MINDIE_HOOK_LOCK_BUDGET_SECONDS") or 2.0)
BOOTSTRAP_TOTAL = float(os.environ.get("MINDIE_BOOTSTRAP_TOTAL_SECONDS") or 1200.0)
UV_PYTHON_PROVISION = 180.0  # SessionStart `uv --python 3.13` may fetch CPython.
SESSION_START_CLEANUP_GRACE = 60.0
SESSION_START_DEADLINE_ENV = "MINDIE_SESSION_START_DEADLINE_MONOTONIC"
# Knowledge stdout only. A legal maximum page measured 817407 bytes.
KNOWLEDGE_MAX_OUTPUT = 1024 * 1024
MAX_RPC_ID = 256
WORK_LIMIT = 3
CONTROL_LIMIT = 1
EOF_JOIN = 3.0
OUTPUT_BUDGET = 1.0
CALL_CHILD_BUDGET = 60.0
KNOWLEDGE_SYNC_BUDGET = 300.0
KNOWLEDGE_DISTILL_DEFAULT_BUDGET = 420.0
KNOWLEDGE_HELPER_GRACE = 30.0
ENTRY_CHILD_BUDGET = 180.0
REMOTE_GRACE_SECONDS = 60.0
REMOTE_MIN_BUDGET_SECONDS = 180.0
REMOTE_MAX_OUTPUT = 32 * 1024 * 1024
REMOTE_NATIVE_MAX_WAIT_MS = {
    "remote_bash": 600000,
    "remote_bashoutput": 600000,
    "remote_taskoutput": 600000,
}
CONTROL_TOOLS = {"remote_job_status", "remote_job_stop", "remote_job_tail"}
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,95}$")
_COMMUNITY_REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_COMMUNITY_BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,120}\Z")
_REQUIRED_BINDING_KEYS = ("python", "runtime_scripts", "adapter_config", "base_config", "engine_config")
CONFIG_ENV = "MINDIE_CC_CONFIG"


def __getattr__(name: str):
    if name == "bounded":
        import bounded as mod

        globals()["bounded"] = mod
        return mod
    if name == "diagnostic_support":
        import diagnostic_support as mod

        globals()["diagnostic_support"] = mod
        return mod
    if name in {"InstallationUnavailable", "LockTimeout", "binding_snapshot", "installed_binding"}:
        import install_state as mod

        for attr in ("InstallationUnavailable", "LockTimeout", "binding_snapshot", "installed_binding"):
            globals().setdefault(attr, getattr(mod, attr))
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _attr(name: str):
    return sys.modules[__name__].__dict__[name] if name in globals() else __getattr__(name)


def _config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "mindie-agent" / "cc.json"


def _load_config():
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _record_hook(stage, category, op="stop", exception=None):
    """One static diagnostic for an outer entry failure. Genuine off stays quiet."""
    try:
        _attr("diagnostic_support").failure(
            "hook", stage, category, exception=exception,
            reportable=op not in {"stop", "pretool", "prompt", "expansion"},
        )
    except Exception:
        pass


def _read_small_json(path: Path, limit: int = 65536) -> tuple[str | None, dict | None]:
    try:
        st = path.stat()
        if not stat_mod.S_ISREG(st.st_mode) or st.st_size > limit:
            return "unavailable", None
        with path.open("rb") as stream:
            blob = stream.read(limit + 1)
    except FileNotFoundError:
        return "missing", None
    except TimeoutError:
        raise
    except OSError:
        return "unavailable", None
    if len(blob) > limit:
        return "unavailable", None
    try:
        data = json.loads(blob.decode("utf-8-sig"))
    except (UnicodeError, ValueError):
        return "corrupt", None
    if not isinstance(data, dict):
        return "corrupt", None
    return None, data


def _fast_binding_snapshot(base_data: dict | None = None) -> dict:
    if "binding_snapshot" in globals():
        return globals()["binding_snapshot"]()
    base = _config_path()
    if base_data is None:
        state, data = _read_small_json(base)
        if state is not None:
            raise ValueError("adapter configuration is unavailable")
    else:
        data = base_data
    selected_raw = data.get("adapter_config")
    if isinstance(selected_raw, str) and selected_raw and Path(selected_raw) != base:
        s_state, s_data = _read_small_json(Path(selected_raw))
        if s_state is not None:
            raise ValueError("selected adapter binding is unavailable")
        data = s_data
    for key in _REQUIRED_BINDING_KEYS:
        val = data.get(key)
        if not isinstance(val, str) or not val or not os.path.isabs(val):
            raise ValueError("selected adapter binding is incomplete")
    if not Path(data["python"]).is_file() or not Path(data["runtime_scripts"]).is_dir():
        raise ValueError("selected adapter binding is incomplete")
    return data


def _validate_community_doc(data: dict) -> tuple[bool, list[str]]:
    if data.get("schema") != "mindie-community-config/1":
        raise ValueError("community sharing configuration is invalid")
    enabled = data.get("enabled")
    if type(enabled) is not bool:
        raise ValueError("community sharing configuration is invalid")
    idle = data.get("idle_seconds")
    if idle is not None and (type(idle) is not int or not 30 <= idle <= 86400):
        raise ValueError("community sharing configuration is invalid")
    repo = data.get("repository")
    if repo is not None and (not isinstance(repo, str) or not _COMMUNITY_REPO_RE.fullmatch(repo)):
        raise ValueError("community sharing configuration is invalid")
    branch = str(data.get("branch") or "main")
    if not _COMMUNITY_BRANCH_RE.fullmatch(branch) or ".." in branch:
        raise ValueError("community sharing configuration is invalid")
    roots = data.get("project_roots", [])
    if not isinstance(roots, list) or not all(isinstance(r, str) and os.path.isabs(r) for r in roots):
        raise ValueError("community sharing configuration is invalid")
    if enabled:
        gen = data.get("generation")
        enabled_at = data.get("enabled_at")
        if (not isinstance(gen, str) or not gen
                or type(enabled_at) not in (int, float)
                or not math.isfinite(enabled_at) or enabled_at <= 0):
            raise ValueError("community sharing configuration is invalid")
    return enabled, roots


def _sharing_enabled(binding=None) -> bool:
    """True only for explicit enabled sharing; unreadable config is not off."""
    path = _config_path()
    state, data = (None, binding) if binding is not None else _read_small_json(path)
    if state == "missing":
        return False
    if state is not None:
        raise ValueError("adapter configuration is unavailable")
    community = data.get("community_config")
    if community is None:
        return False
    if not isinstance(community, str) or not os.path.isabs(community):
        raise ValueError("community configuration path is invalid")
    c_state, c_data = _read_small_json(Path(community))
    if c_state == "missing":
        return False
    if c_state is not None:
        raise ValueError("community sharing configuration is invalid")
    enabled, _ = _validate_community_doc(c_data)
    return enabled


_DEFAULT_SHARING_ENABLED = _sharing_enabled


def _probe_stop_consent(binding: dict) -> tuple[bool, list[Path] | None]:
    """Single pure-stdlib read of community + consent before reading Stop stdin."""
    if _sharing_enabled is not _DEFAULT_SHARING_ENABLED:
        return bool(_sharing_enabled(binding)), None
    community = binding.get("community_config") if isinstance(binding, dict) else None
    if community is None:
        return False, None
    if not isinstance(community, str) or not os.path.isabs(community):
        raise ValueError("community configuration path is invalid")
    c_state, c_data = _read_small_json(Path(community))
    if c_state == "missing":
        return False, None
    if c_state is not None:
        raise ValueError("community sharing configuration is invalid")
    enabled, raw_roots = _validate_community_doc(c_data)
    if not enabled:
        return False, None
    consent_ref = c_data.get("consent_config")
    if not isinstance(consent_ref, str) or not consent_ref or not os.path.isabs(consent_ref):
        return True, None
    s_state, s_data = _read_small_json(Path(consent_ref))
    if s_state == "missing":
        return False, None
    if s_state is not None or s_data.get("schema") != "mindie-consent/1":
        return True, None
    choice = s_data.get("choice")
    if choice in {"disabled", "later", "read-only"}:
        return False, None
    if choice != "contribute":
        return True, None
    if not raw_roots:
        return False, None
    return True, [Path(r).expanduser().resolve(strict=False) for r in raw_roots]


def _path_in_roots(candidate: str, roots: list[Path]) -> bool:
    if not roots or not isinstance(candidate, str) or not os.path.isabs(candidate):
        return False
    try:
        resolved = Path(candidate).expanduser().resolve(strict=False)
        return any(resolved == root or resolved.is_relative_to(root) for root in roots)
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return False


class DispatchFailure(RuntimeError):
    """Known frontend contract failure. Only a safe stage and diagnostic."""

    def __init__(self, diagnostic, stage):
        super().__init__(stage)
        self.diagnostic = diagnostic
        self.stage = stage


def _dispatch_failure(current, stage, category, exc, started):
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    diagnostic = _attr("diagnostic_support").failure(
        "mcp_dispatch", stage, category, exception=exc,
        revision=current.get("sha"), elapsed_ms=elapsed_ms,
    )
    return DispatchFailure(diagnostic, stage)


_CONNECTION_ID = os.environ.get("MINDIE_MCP_CONNECTION_ID") or f"cc-mcp-{os.getpid()}-{os.urandom(6).hex()}"
_LAUNCH_CWD = os.getcwd()


def _child_env(current: dict) -> dict:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PYTHONIOENCODING"] = "utf-8"
    env["MINDIE_MCP_CONNECTION_ID"] = _CONNECTION_ID
    env.setdefault("MINDIE_HOST_CWD", _LAUNCH_CWD)
    env[CONFIG_ENV] = current["adapter_config"]
    adapter = json.loads(Path(current["adapter_config"]).read_text(encoding="utf-8"))
    env.update(adapter.get("diagnostics_context", {}))
    env.setdefault("MINDIE_DIAGNOSTICS_CONFIG",
                   str(Path(adapter["base_config"]).parent / "diagnostics.json"))
    return env


def _fail_open() -> int:
    print(json.dumps({}), flush=True)
    return 0


def _hook_warning(*, unavailable=False) -> int:
    # Native user-visible warning; no blocking decision or extra model turn.
    message = (
        "MindIE is temporarily unavailable during installation. "
        "Task completion is unaffected; existing choices and data are preserved."
    ) if unavailable else (
        "MindIE background capture encountered an error. "
        "Task completion is unaffected; details are in MindIE diagnostics."
    )
    print(json.dumps({"systemMessage": message}), flush=True)
    return 0


def _session_start_incomplete() -> int:
    print(json.dumps({"systemMessage": "MindIE startup maintenance is incomplete."}))
    return 0


def _read_hook_stdin(deadline):
    """Deadline-bounded raw fd read; never buffered I/O (shutdown can hang)."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return b""
    buf = bytearray()
    lock = threading.Lock()
    finished = threading.Event()

    def reader():
        try:
            fd = sys.stdin.fileno()
            while True:
                try:
                    chunk = os.read(fd, 65536)
                except TimeoutError:
                    raise
                except (OSError, ValueError):
                    return
                if not chunk:
                    return
                with lock:
                    buf.extend(chunk)
                    if not chunk.rstrip().endswith((b'}', b']')):
                        continue
                    try:
                        json.loads(bytes(buf).removeprefix(b"\xef\xbb\xbf").decode("utf-8"))
                    except (UnicodeDecodeError, ValueError):
                        continue
                    return
        finally:
            finished.set()

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    finished.wait(timeout=max(0.0, remaining))
    if not finished.is_set():
        return b""
    with lock:
        return bytes(buf).removeprefix(b"\xef\xbb\xbf")


def _hook_can_start_service(op, raw):
    return op == "stop"


def _stop_event_in_scope(raw: bytes, roots: list[Path] | None) -> bool:
    if not raw:
        return True
    try:
        event = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return True
    if not isinstance(event, dict):
        return True
    if event.get("hook_event_name") not in {None, "Stop"}:
        return False
    if event.get("stop_hook_active") not in {None, False}:
        return False
    agent_id = event.get("agent_id")
    if isinstance(agent_id, str) and agent_id.strip():
        return False
    if roots is not None:
        for candidate_dir in (event.get("cwd"), event.get("workspace_root")):
            if isinstance(candidate_dir, str) and candidate_dir.strip():
                if not _path_in_roots(candidate_dir, roots):
                    return False
    return True


def _runtime(binding):
    return dict(python=binding["python"], scripts=binding["runtime_scripts"],
                adapter_config=binding["adapter_config"])


def _verify_installation(expected=None, *, timeout=5.0):
    with _attr("installed_binding")(expected, timeout=timeout) as value:
        return value


def _hook(op: str) -> int:
    if op in {"expansion", "prompt", "user-prompt-submit"}:
        return _fail_open()
    deadline = time.monotonic() + HOOK_TOTAL
    if not _config_path().exists():
        return _fail_open()
    stage = "installation"
    receipt = {}
    try:
        if op == "stop":
            if "binding_snapshot" in globals():
                binding = globals()["binding_snapshot"]()
                stage = "sharing-probe"
                should_run, roots = _probe_stop_consent(binding)
                if not should_run:
                    return _fail_open()
            else:
                state, base_data = _read_small_json(_config_path())
                if state == "missing":
                    return _fail_open()
                if state is not None:
                    raise ValueError("adapter configuration is unavailable")
                stage = "sharing-probe"
                should_run, roots = _probe_stop_consent(base_data)
                if not should_run:
                    return _fail_open()
                stage = "installation"
                binding = _fast_binding_snapshot(base_data)
            raw = _read_hook_stdin(deadline)
            if not _stop_event_in_scope(raw, roots):
                return _fail_open()
            admission = nullcontext(binding)
        else:
            raw = _read_hook_stdin(deadline)
            admission = _attr("installed_binding")(
                timeout=min(HOOK_LOCK_BUDGET, max(0, deadline - time.monotonic()))
            )
        with admission as active_binding:
            selected = _runtime(active_binding)
            script = Path(selected["scripts"]) / "bridge.py"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("hook input deadline exhausted")
            stage = "helper_failed"
            out = _attr("bounded").run(
                [selected["python"], str(script), op], raw, timeout=remaining,
                env=_child_env(selected), cwd=str(script.parent),
                check=op != "pretool", receipt=receipt,
                allow_service=_hook_can_start_service(op, raw),
            )
        if op == "pretool" and int(receipt.get("returncode") or 0):
            _record_hook("pretool_helper", "child_failure", op)
            return _fail_open()
        try:
            value = json.loads(out.strip()) if (out or "").strip() else None
        except ValueError:
            value = None
        if not isinstance(value, dict):
            if op == "pretool":
                return _fail_open()
            raise ValueError("hook result must be one object")
        print(json.dumps(value, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        category = ("timeout" if isinstance(exc, TimeoutError) else
                    "child_failure" if stage == "helper_failed" else "configuration")
        _record_hook(stage, category, op, exc)
        if op == "pretool":
            return _fail_open()
        lock_timeout_cls = globals().get("LockTimeout")
        if lock_timeout_cls is not None and isinstance(exc, lock_timeout_cls):
            return _hook_warning(unavailable=True)
        return _hook_warning()


def _local_answer(message: dict):
    method = message.get("method")
    ident = message.get("id")
    if method == "initialize":
        from product_contract import installed_version
        return dict(jsonrpc="2.0", id=ident, result=dict(
            protocolVersion="2025-11-25", capabilities={"tools": {"listChanged": True}},
            serverInfo={"name": "mindie-cc-front", "version": installed_version()}))
    if method == "ping":
        return dict(jsonrpc="2.0", id=ident, result={})
    return None


def _valid_rpc_id(value) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, str) and 1 <= len(value) <= MAX_RPC_ID


def _local_tool_name(message):
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    name = params.get("name")
    if not isinstance(name, str) or not name:
        return None
    return name.rsplit("__", 1)[-1]


def _validated_remote_ref(message):
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    for key in ("job_id", "session_id"):
        value = args.get(key)
        if isinstance(value, str) and JOB_ID_RE.fullmatch(value):
            return key, value
    return None, None


def _front_reject(ident, text: str):
    return dict(jsonrpc="2.0", id=ident,
                result=dict(content=[dict(type="text", text=text[:500])], isError=True))


def _stage_error(ident, message, stage: str, surface: str):
    structured = {"stage": stage}
    text = f"Unavailable ({stage}). Continue independently."
    if stage == "installation_unavailable":
        structured["execution"] = "not_started"
        text = "MindIE is temporarily unavailable during installation; this request was not started."
    elif surface == "remote":
        structured["remote_outcome"] = "unconfirmed"
        key, value = _validated_remote_ref(message)
        name = _local_tool_name(message)
        if key:
            structured[key] = value
            text = (
                f"Remote outcome unconfirmed ({stage}). "
                f"Inspect or stop original {key} {value} before a deliberate repeat."
            )
        elif name == "remote_bash":
            text = (
                f"Remote submission unconfirmed ({stage}). "
                "Inspect local remote-dev job records before a deliberate repeat."
            )
    return dict(jsonrpc="2.0", id=ident, result=dict(
        content=[dict(type="text", text=text[:500])], isError=True,
        structuredContent=structured))


def remote_inner_wait_seconds(name, arguments):
    """Largest legal native inner wait for this remote tool, in seconds."""
    key = name.rsplit("__", 1)[-1].lower() if isinstance(name, str) else ""
    native_ms = REMOTE_NATIVE_MAX_WAIT_MS.get(key, 0)
    explicit_ms = 0
    if isinstance(arguments, dict):
        for field in ("timeout", "yield_time_ms", "timeout_ms"):
            value = arguments.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if math.isfinite(value) and value > 0:
                explicit_ms = max(explicit_ms, float(value))
    return max(float(native_ms) / 1000.0, explicit_ms / 1000.0)


def remote_budget(name, arguments=None, *, native_wait_ms=None,
                  grace=REMOTE_GRACE_SECONDS, minimum=REMOTE_MIN_BUDGET_SECONDS):
    """Outer child budget: native wait + SSH/framing/drain/cleanup grace."""
    if isinstance(grace, bool) or not isinstance(grace, (int, float)) \
            or not math.isfinite(grace) or grace <= 0:
        raise ValueError("remote grace must be a positive finite number")
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)) \
            or not math.isfinite(minimum) or minimum <= 0:
        raise ValueError("remote minimum budget must be a positive finite number")
    if native_wait_ms is None:
        wait = remote_inner_wait_seconds(name, arguments)
    else:
        if isinstance(native_wait_ms, bool) or not isinstance(native_wait_ms, (int, float)) \
                or not math.isfinite(native_wait_ms) or native_wait_ms < 0:
            raise ValueError("native wait must be a nonnegative finite number of milliseconds")
        wait = float(native_wait_ms) / 1000.0
    return max(float(minimum), wait + float(grace))


def _knowledge_distill_budget() -> float:
    try:
        from mindie_knowledge.loop import summary_budget

        inner = float(summary_budget.distill_rpc_timeout())
    except Exception:
        worker_sec = float(os.environ.get("MINDIE_SUMMARY_TIMEOUT_SECONDS") or 315.0)
        inner = max(KNOWLEDGE_DISTILL_DEFAULT_BUDGET, worker_sec + 105.0)
    return inner + KNOWLEDGE_HELPER_GRACE


def _dispatch_budget(surface, message):
    name = _local_tool_name(message)
    arguments = (message.get("params") or {}).get("arguments") or {}
    if surface == "remote":
        return remote_budget(name, arguments)
    if surface == "components" and message.get("method") == "tools/call":
        from component_tools import MAX_TIMEOUT_MS
        milliseconds = arguments.get("timeout_ms", 120000)
        if type(milliseconds) is not int or not 1 <= milliseconds <= MAX_TIMEOUT_MS:
            milliseconds = 120000
        return milliseconds / 1000 + 30.0
    if surface == "knowledge" and name == "knowledge_sync":
        return KNOWLEDGE_SYNC_BUDGET + KNOWLEDGE_HELPER_GRACE
    if surface == "knowledge" and name == "knowledge_distill":
        return _knowledge_distill_budget()
    if name == "mindie_entry":
        return ENTRY_CHILD_BUDGET
    return CALL_CHILD_BUDGET


def _dispatch(surface: str, raw: bytes, ident, cancel=None, *, binding=None, deadline=None):
    bounded_mod = _attr("bounded")
    if cancel is not None and cancel.is_set():
        raise bounded_mod.CommandCancelled("command cancelled")
    selected = {}
    started = time.monotonic()
    payload = raw if raw.endswith(b"\n") else raw + b"\n"
    message = json.loads(raw)
    name = _local_tool_name(message)
    allow_service = (surface == "knowledge" and message.get("method") == "tools/call"
                     and name in {
                         "knowledge_query", "knowledge_explain", "knowledge_feedback",
                         "knowledge_distill"})
    try:
        with _attr("installed_binding")(binding) as current:
            selected = _runtime(current)
            script = Path(selected["scripts"]) / "mcp_server.py"
            process = {}
            child_budget = _dispatch_budget(surface, message)
            timeout = (
                child_budget
                if deadline is None
                else max(0.01, deadline - time.monotonic())
            )
            out = bounded_mod.run(
                [selected["python"], str(script), surface, "--once"], payload,
                timeout=timeout,
                env=_child_env(selected), cwd=str(script.parent), cancel=cancel,
                receipt=process,
                allow_service=allow_service,
                **({"max_output": KNOWLEDGE_MAX_OUTPUT} if surface == "knowledge"
                   else {"max_output": REMOTE_MAX_OUTPUT}),
            )
    except bounded_mod.CommandCancelled:
        raise
    except (TimeoutError, bounded_mod.CommandTimedOut):
        raise
    except (_attr("InstallationUnavailable"), _attr("LockTimeout")) as exc:
        raise _dispatch_failure(selected, "installation_unavailable", "configuration",
                                exc, started) from None
    except Exception as exc:
        raise _dispatch_failure(selected, "helper_failed", "child_failure", exc, started) from None
    if cancel is not None and cancel.is_set():
        raise bounded_mod.CommandCancelled("command cancelled")
    try:
        response = json.loads(out)
    except ValueError:
        raise _dispatch_failure(selected, "helper_protocol", "protocol_mismatch", None, started) from None
    if not isinstance(response, dict) or response.get("id") != ident:
        raise _dispatch_failure(selected, "helper_protocol", "protocol_mismatch", None, started)
    if process.get("cleanup_failed") and isinstance(response.get("result"), dict):
        response["result"].setdefault("structuredContent", {})["cleanup_warning"] = (
            "owned MCP helper cleanup did not complete; the tool result is preserved"
        )
    return response


def _read_mcp_line(stdin):
    line = stdin.readline()
    return line.rstrip(b"\r\n") if line else False


def _mcp(surface: str) -> int:
    from native_mcp import serve
    return serve(surface, sys.modules[__name__])


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rest = argv[2:] if argv[:1] == ["--source"] else argv
    if rest[:1] == ["--config"] and len(rest) > 1:
        rest = rest[2:]
    deadline = None
    if rest[:2] == ["hook", "session-start"]:
        raw = os.environ.get(SESSION_START_DEADLINE_ENV)
        deadline = float(raw) if raw else time.monotonic() + BOOTSTRAP_TOTAL
        if deadline - time.monotonic() <= 0:
            _record_hook("installation", "timeout", op="session-start")
            return _session_start_incomplete()
    if argv[:1] == ["--source"]:
        source = Path(argv[1])
        argv = argv[2:]
        from setup import prepare_binding, prepare_binding_within, publish_entry
        try:
            prepared = (
                prepare_binding(source) if deadline is None
                else prepare_binding_within(source, deadline)
            )
        except TimeoutError as exc:
            _record_hook("installation", "timeout", op="session-start", exception=exc)
            return _session_start_incomplete()
        os.environ[CONFIG_ENV] = prepared["config"]
        data = os.environ.get("CLAUDE_PLUGIN_DATA")
        if not data or not Path(data).is_absolute():
            raise ValueError("native CLAUDE_PLUGIN_DATA is required for the persistent release entry")
        version = json.loads((source / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
        publish_entry(Path(data) / "releases" / version / "entry.py", prepared["config"])
    if argv[:1] == ["--config"] and len(argv) > 1:
        os.environ[CONFIG_ENV] = str(Path(argv[1]).expanduser())
        argv = argv[2:]
    kind = argv[0] if argv else ""
    name = argv[1] if len(argv) > 1 else ""
    if kind == "hook" and name == "session-start":
        event = _read_hook_stdin(deadline)
        if not event:
            _record_hook("input", "missing_identity", op="session-start")
            return _hook_warning()
        if deadline - time.monotonic() <= 0:
            _record_hook("installation", "timeout", op="session-start")
            return _session_start_incomplete()
        try:
            with _attr("installed_binding")(timeout=max(0.0, deadline - time.monotonic())) as binding:
                from harness import require_binding_support
                require_binding_support(binding)
                selected = _runtime(binding)
                script = Path(selected["scripts"]) / "maintenance.py"
                from maintenance import TOTAL_BUDGET
                remaining = max(0.01, deadline - time.monotonic())
                raw = _attr("bounded").run([selected["python"], str(script)], event,
                                           timeout=min(remaining, TOTAL_BUDGET),
                                           env=_child_env(selected), cwd=str(script.parent))
            result = json.loads(raw)
        except (_attr("InstallationUnavailable"), _attr("LockTimeout")):
            return _hook_warning(unavailable=True)
        except Exception as exc:
            category = "timeout" if isinstance(exc, TimeoutError) else "unavailable"
            _record_hook("maintenance", category, op="session-start", exception=exc)
            result = {"status": "incomplete", "error_type": type(exc).__name__}
        if result["status"] != "ready":
            return _session_start_incomplete()
        return _fail_open()
    if kind == "hook" and name in {"expansion", "prompt", "user-prompt-submit", "pretool", "stop"}:
        return _hook(name)
    if kind == "mcp" and name in {"knowledge", "remote", "components"}:
        return _mcp(name)
    print("usage: mindie_launch.py hook|mcp ...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
