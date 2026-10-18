#!/usr/bin/env python3
"""Stable native facade; each operation dispatches the selected complete helper."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bounded


import diagnostic_support
from install_state import (
    InstallationUnavailable, LockTimeout, binding_snapshot, installed_binding,
)

HOOK_TOTAL = float(os.environ.get("MINDIE_HOOK_TOTAL_SECONDS") or 10.0)
HOOK_LOCK_BUDGET = float(os.environ.get("MINDIE_HOOK_LOCK_BUDGET_SECONDS") or 2.0)
BOOTSTRAP_TOTAL = float(os.environ.get("MINDIE_BOOTSTRAP_TOTAL_SECONDS") or 540.0)
# Knowledge stdout only. A legal maximum page measured 817407 bytes.
KNOWLEDGE_MAX_OUTPUT = 1024 * 1024
MAX_RPC_ID = 256
WORK_LIMIT = 3
CONTROL_LIMIT = 1
EOF_JOIN = 3.0
OUTPUT_BUDGET = 1.0
CONTROL_TOOLS = {"remote_job_status", "remote_job_stop", "remote_job_tail"}
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,95}$")
CONFIG_ENV = "MINDIE_CC_CONFIG"


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
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _record_hook(stage, category, op="stop", exception=None):
    """One static diagnostic for an outer entry failure. Genuine off stays quiet."""
    try:
        diagnostic_support.failure(
            "hook", stage, category, exception=exception, reportable=(op != "stop"),
        )
    except Exception:
        pass


def _sharing_enabled(binding=None) -> bool:
    """True only when community sharing is explicitly enabled.

    A missing adapter or community file, or an explicit off, is quiet.
    An existing file that cannot be read or parsed is a configuration
    failure, not off.
    """
    from entry import _read_regular_json

    path = _config_path()
    state, data = (None, binding) if binding is not None else _read_regular_json(path)
    if state == "missing":
        return False
    if state is not None:
        raise ValueError("adapter configuration is unavailable")
    community = data.get("community_config")
    if community is None:
        return False
    if not isinstance(community, str) or not os.path.isabs(community):
        raise ValueError("community configuration path is invalid")
    from mindie_knowledge.loop.settings import load

    sharing = load(community)
    if sharing.state == "missing":
        return False
    if sharing.state not in {"enabled", "disabled"}:
        raise ValueError("community sharing configuration is invalid")
    return sharing.enabled


class DispatchFailure(RuntimeError):
    """Known frontend contract failure. Only a safe stage and diagnostic."""

    def __init__(self, diagnostic, stage):
        super().__init__(stage)
        self.diagnostic = diagnostic
        self.stage = stage


def _dispatch_failure(current, stage, category, exc, started):
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    diagnostic = diagnostic_support.failure(
        "mcp_dispatch",
        stage,
        category,
        exception=exc,
        revision=current.get("sha"),
        elapsed_ms=elapsed_ms,
    )
    return DispatchFailure(diagnostic, stage)


def _child_env(current: dict) -> dict:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PYTHONIOENCODING"] = "utf-8"
    env[CONFIG_ENV] = current["adapter_config"]
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


def _expansion_error(text: str) -> int:
    payload = {
        "additionalContext": text,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptExpansion",
            "additionalContext": text,
        },
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


def _read_hook_stdin(deadline):
    """Deadline-bounded raw fd read; never buffered I/O (shutdown can hang).

    Stops at EOF, the deadline, or the first complete JSON
    value so a held-open pipe cannot consume the helper's remaining time.
    Windows native select is sockets-only; a daemon os.read thread is the
    portable bound (code-only on Windows; not natively verified).
    """
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
                except (OSError, ValueError):
                    return
                if not chunk:
                    return
                with lock:
                    buf.extend(chunk)
                    # Native Stop is one object. Avoid reparsing a growing
                    # final answer after every chunk (quadratic work).
                    if not chunk.rstrip().endswith((b'}', b']')):
                        continue
                    try:
                        json.loads(bytes(buf))
                    except ValueError:
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
        return bytes(buf)


def _hook_can_start_service(op, raw):
    if op == "stop":
        return True
    if op != "expansion":
        return False
    try:
        event = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return False
    return (
        isinstance(event, dict) and event.get("expansion_type") == "slash_command"
        and event.get("command_source") == "plugin"
        and event.get("command_name") in {
            "mindie-agent", "mindie-agent:mindie-agent", "mindie-agent:init",
            "mindie-agent:sharing-enable",
        }
    )


def _runtime(binding):
    return dict(python=binding["python"], scripts=binding["runtime_scripts"],
                adapter_config=binding["adapter_config"])


def _verify_installation(expected=None, *, timeout=5.0):
    with installed_binding(expected, timeout=timeout) as value:
        return value


def _hook(op: str) -> int:
    deadline = time.monotonic() + HOOK_TOTAL
    if op == "stop" and not _config_path().exists():
        return _fail_open()
    raw = _read_hook_stdin(deadline)
    stage = "installation"
    try:
        admission = (
            nullcontext(binding_snapshot()) if op == "stop"
            else installed_binding(timeout=min(HOOK_LOCK_BUDGET, max(0, deadline - time.monotonic())))
        )
        with admission as binding:
            stage = "sharing-probe"
            if op == "stop" and not _sharing_enabled(binding):
                return _fail_open()
            selected = _runtime(binding)
            script = Path(selected["scripts"]) / "bridge.py"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("hook input deadline exhausted")
            stage = "helper_failed"
            out = bounded.run(
                [selected["python"], str(script), op], raw, timeout=remaining,
                env=_child_env(selected), cwd=str(script.parent), check=True,
                allow_service=_hook_can_start_service(op, raw),
            )
        value = json.loads(out.strip())
        if not isinstance(value, dict):
            raise ValueError("hook result must be one object")
        print(json.dumps(value, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        category = "child_failure" if stage == "helper_failed" else "configuration"
        _record_hook(stage, category, op, exc)
        if isinstance(exc, LockTimeout):
            return _hook_warning(unavailable=True)
        if op == "expansion":
            return _expansion_error("MindIE entry failed; inspect reporting-status.")
        return _hook_warning()


def _local_answer(message: dict):
    method = message.get("method")
    ident = message.get("id")
    if method == "initialize":
        from product_contract import installed_version
        return dict(
            jsonrpc="2.0",
            id=ident,
            result=dict(
                protocolVersion="2025-11-25",
                capabilities={"tools": {}},
                serverInfo={"name": "mindie-cc-front", "version": installed_version()},
            ),
        )
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
    return dict(
        jsonrpc="2.0",
        id=ident,
        result=dict(
            content=[dict(type="text", text=text[:500])],
            isError=True,
        ),
    )


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
    return dict(
        jsonrpc="2.0",
        id=ident,
        result=dict(
            content=[dict(type="text", text=text[:500])],
            isError=True,
            structuredContent=structured,
        ),
    )


def _dispatch(surface: str, raw: bytes, ident, cancel=None, *, binding=None):
    if cancel is not None and cancel.is_set():
        raise bounded.CommandCancelled("command cancelled")
    selected = {}
    started = time.monotonic()
    payload = raw if raw.endswith(b"\n") else raw + b"\n"
    message = json.loads(raw)
    allow_service = (surface == "knowledge" and message.get("method") == "tools/call"
                     and _local_tool_name(message) in {
                         "knowledge_query", "knowledge_explain", "knowledge_feedback"})
    try:
        # All operations, including cancellation/status tools, take a shared
        # OS lock. Activation never interrupts a helper admitted here.
        with installed_binding(binding) as current:
            selected = _runtime(current)
            script = Path(selected["scripts"]) / "mcp_server.py"
            out = bounded.run(
                [selected["python"], str(script), surface, "--once"], payload,
                env=_child_env(selected), cwd=str(script.parent), cancel=cancel,
                allow_service=allow_service,
                **({"max_output": KNOWLEDGE_MAX_OUTPUT} if surface == "knowledge" else {}),
            )
    except bounded.CommandCancelled:
        raise
    except (InstallationUnavailable, LockTimeout) as exc:
        raise _dispatch_failure(selected, "installation_unavailable", "configuration",
                                exc, started) from None
    except Exception as exc:
        raise _dispatch_failure(selected, "helper_failed", "child_failure", exc, started) from None
    if cancel is not None and cancel.is_set():
        raise bounded.CommandCancelled("command cancelled")
    try:
        response = json.loads(out)
    except ValueError:
        raise _dispatch_failure(selected, "helper_protocol", "protocol_mismatch", None, started) from None
    if not isinstance(response, dict) or response.get("id") != ident:
        raise _dispatch_failure(selected, "helper_protocol", "protocol_mismatch", None, started)
    return response


def _read_mcp_line(stdin):
    line = stdin.readline()
    return line.rstrip(b"\r\n") if line else False


def _mcp(surface: str) -> int:
    binding = _verify_installation()
    from harness import require_binding_support
    require_binding_support(binding)
    from release_update import BackgroundCheck
    update = BackgroundCheck(lambda: _verify_installation(binding, timeout=0))
    output_fd = sys.stdout.fileno()
    if os.name == "posix":
        os.set_blocking(output_fd, False)
    stdin = sys.stdin.buffer
    output_lock = threading.Lock()
    transport_closed = threading.Event()
    pending_lock = threading.Lock()
    pending = {}
    workers = []
    active_work = 0
    active_control = 0

    def send(response):
        # A client that stops reading must not hold cancellation/EOF forever.
        deadline = time.monotonic() + OUTPUT_BUDGET
        if not output_lock.acquire(timeout=OUTPUT_BUDGET):
            raise TimeoutError("response output unavailable")
        try:
            data = memoryview(json.dumps(response, ensure_ascii=False).encode() + b"\n")
            while data:
                if transport_closed.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError("response output unavailable")
                try:
                    count = os.write(output_fd, data)
                except BlockingIOError:
                    transport_closed.wait(0.02)
                    continue
                if not count:
                    raise BrokenPipeError("response transport closed")
                data = data[count:]
        finally:
            output_lock.release()

    def reap():
        alive = []
        for thread in workers:
            if thread.is_alive():
                alive.append(thread)
            else:
                thread.join(timeout=0)
        workers[:] = alive

    def execute(raw, message, ident, cancel, slot):
        nonlocal active_work, active_control
        try:
            try:
                if cancel.is_set():
                    raise bounded.CommandCancelled("command cancelled")
                response = _dispatch(surface, raw, ident, cancel=cancel, binding=binding)
            except bounded.CommandCancelled:
                response = _stage_error(ident, message, "cancelled", surface)
            except DispatchFailure as exc:
                response = _stage_error(ident, message, exc.stage, surface)
                response["result"] = diagnostic_support.attach(
                    response["result"], exc.diagnostic
                )
            except bounded.CommandTimedOut:
                response = _stage_error(ident, message, "deadline", surface)
            except Exception:
                response = _stage_error(ident, message, "helper_failed", surface)
            # Terminal selection is atomic with cancellation; no state lock
            # spans output I/O. A later cancellation cannot change completion.
            with pending_lock:
                if cancel.is_set():
                    response = _stage_error(ident, message, "cancelled", surface)
                if pending.get(ident) is cancel:
                    pending.pop(ident)
            send(response)
        except OSError:
            transport_closed.set()
            with pending_lock:
                for event in pending.values():
                    event.set()
        finally:
            with pending_lock:
                if pending.get(ident) is cancel:
                    pending.pop(ident)
                if slot == "control":
                    active_control -= 1
                else:
                    active_work -= 1

    try:
        update.start_if_due()
        while not transport_closed.is_set():
            raw = _read_mcp_line(stdin)
            if raw is False:
                break
            if raw is None or not raw.strip():
                continue
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            method = message.get("method")
            if method == "notifications/cancelled":
                params = (
                    message.get("params")
                    if isinstance(message.get("params"), dict)
                    else {}
                )
                request_id = params.get("requestId")
                if _valid_rpc_id(request_id):
                    with pending_lock:
                        event = pending.get(request_id)
                        if event is not None:
                            event.set()
                continue
            if "id" not in message:
                continue
            ident = message["id"]
            if not _valid_rpc_id(ident):
                send(
                    dict(
                        jsonrpc="2.0",
                        id=None,
                        error=dict(code=-32600, message="Invalid request id"),
                    )
                )
                continue
            try:
                _verify_installation(binding)
            except (InstallationUnavailable, LockTimeout):
                send(_stage_error(ident, message, "installation_unavailable", surface))
                continue
            local = _local_answer(message)
            if local is not None:
                with pending_lock:
                    duplicate = ident in pending
                if duplicate:
                    send(_front_reject(ident, "Duplicate pending request; not executed."))
                    continue
                send(local)
                continue
            if method not in {"tools/list", "tools/call"}:
                send(
                    dict(
                        jsonrpc="2.0",
                        id=ident,
                        error=dict(code=-32601, message="Method not found"),
                    )
                )
                continue
            if method == "tools/call":
                update.start_if_due()
            is_control = (
                method == "tools/call" and _local_tool_name(message) in CONTROL_TOOLS
            )
            slot = None
            cancel = None
            with pending_lock:
                reap()
                if ident in pending:
                    duplicate = True
                else:
                    duplicate = False
                    if is_control:
                        if active_work < WORK_LIMIT:
                            slot = "work"
                        elif active_control < CONTROL_LIMIT:
                            slot = "control"
                    elif active_work < WORK_LIMIT:
                        slot = "work"
                    if slot == "work":
                        active_work += 1
                    elif slot == "control":
                        active_control += 1
                    if slot:
                        cancel = threading.Event()
                        pending[ident] = cancel
            if duplicate:
                send(_front_reject(ident, "Duplicate pending request; not executed."))
                continue
            if slot is None:
                send(_front_reject(ident, "Request capacity reached; not executed."))
                continue
            thread = threading.Thread(
                target=execute,
                args=(raw, message, ident, cancel, slot),
                name=f"mindie-mcp-{ident}",
                daemon=True,  # EOF does not join a blocked transport writer forever.
            )
            thread.start()
            workers.append(thread)
    finally:
        transport_closed.set()
        with pending_lock:
            events = list(pending.values())
        for event in events:
            event.set()
        update.close()
        deadline = time.monotonic() + EOF_JOIN
        for thread in list(workers):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--source"]:
        source = Path(argv[1])
        argv = argv[2:]
        from setup import prepare_binding, publish_entry
        prepared = prepare_binding(source)
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
        deadline = time.monotonic() + BOOTSTRAP_TOTAL
        event = _read_hook_stdin(deadline)
        if not event:
            _record_hook("input", "missing_identity", op="session-start")
            return _hook_warning()
        try:
            with installed_binding(timeout=max(0.0, deadline - time.monotonic())) as binding:
                from harness import require_binding_support
                require_binding_support(binding)
                selected = _runtime(binding)
                script = Path(selected["scripts"]) / "maintenance.py"
                remaining = max(0.01, deadline - time.monotonic())
                raw = bounded.run([selected["python"], str(script)], event,
                                  timeout=remaining,
                                  env=_child_env(selected), cwd=str(script.parent))
            result = json.loads(raw)
        except (InstallationUnavailable, LockTimeout):
            return _hook_warning(unavailable=True)
        if result["status"] != "ready":
            print(json.dumps({"systemMessage": "MindIE startup maintenance is incomplete."}))
            return 0
        return _fail_open()
    if kind == "hook" and name in {"expansion", "pretool", "stop"}:
        return _hook(name)
    if kind == "mcp" and name in {"knowledge", "remote"}:
        return _mcp(name)
    print("usage: mindie_launch.py hook|mcp ...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
