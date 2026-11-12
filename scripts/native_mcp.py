"""Bounded native MCP transport and selected-generation invalidation.

[Role] Keep the native connection alive across committed runtime updates.
[Invariants] Preserve pending identity, cancellation and separate control capacity.
[Timeouts & Locks] Existing facade budgets; serialized output and 1s catalog poll.
[Side-Effect Policy] Dispatch each operation once; refresh never activates a task.
"""
import json
import os
import sys
import threading
import time

def serve(surface: str, api) -> int:
    CONTROL_LIMIT = api.CONTROL_LIMIT
    CONTROL_TOOLS = api.CONTROL_TOOLS
    DispatchFailure = api.DispatchFailure
    EOF_JOIN = api.EOF_JOIN
    InstallationUnavailable = api.InstallationUnavailable
    LockTimeout = api.LockTimeout
    OUTPUT_BUDGET = api.OUTPUT_BUDGET
    WORK_LIMIT = api.WORK_LIMIT
    _dispatch = api._dispatch
    _front_reject = api._front_reject
    _local_answer = api._local_answer
    _local_tool_name = api._local_tool_name
    _read_mcp_line = api._read_mcp_line
    _stage_error = api._stage_error
    _valid_rpc_id = api._valid_rpc_id
    _verify_installation = api._verify_installation
    bounded = api.bounded
    diagnostic_support = api.diagnostic_support
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

    from remote_dev.mcp.catalog_refresh import CatalogRefresh, file_revision
    refresh = CatalogRefresh(lambda: file_revision(binding["base_config"]), send)

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
            except (TimeoutError, bounded.CommandTimedOut):
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
            refresh.check()
            send(response)
        except TimeoutError:
            raise
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
                if method == "initialize":
                    refresh.start()
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
                name=f"mindie-mcp-{ident}", daemon=True,
            )
            thread.start()
            workers.append(thread)
    finally:
        refresh.close()
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

