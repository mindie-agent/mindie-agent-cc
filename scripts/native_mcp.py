"""Bounded native MCP transport, read_pool fast lane, and generation invalidation.

[Role] Keep the native connection alive across committed runtime updates while
routing `readOnlyHint=True` tools onto a dedicated `read_pool` (`ThreadPoolExecutor`).
[Invariants] Preserve pending identity, cancellation, read_pool non-blocking
concurrency, and separate control capacity.
[Timeouts & Locks] Existing facade budgets; serialized output and 1s catalog poll.
[Side-Effect Policy] Dispatch each operation once; refresh never activates a task.
"""
from concurrent.futures import Future, ThreadPoolExecutor, wait as wait_futures
import json
import os
import sys
import threading
import time

from mcp_catalog import is_readonly_tool

READ_POOL_WORKERS = 16
READ_LIMIT = 64


def serve(surface: str, api) -> int:
    """Hold a shared runtime lease for the whole resident MCP connection."""
    binding = api._verify_installation()
    from storage_lifecycle import runtime_leases

    with runtime_leases(binding):
        return _serve(surface, api, binding)


def _serve(surface: str, api, binding) -> int:
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
    read_futures: set[Future[None]] = set()
    read_pool = ThreadPoolExecutor(max_workers=READ_POOL_WORKERS, thread_name_prefix="mindie-mcp-read")
    active_work = 0
    active_control = 0
    active_read = 0

    def send(response):
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
        nonlocal active_work, active_control, active_read
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
                elif slot == "work":
                    active_work -= 1
                elif slot == "read":
                    active_read -= 1

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
            local_name = _local_tool_name(message) if method == "tools/call" else None
            is_control = method == "tools/call" and local_name in CONTROL_TOOLS
            is_read = (
                method == "tools/list"
                or (
                    method == "tools/call"
                    and not is_control
                    and is_readonly_tool(surface, local_name)
                )
            )
            slot = None
            cancel = None
            with pending_lock:
                reap()
                if ident in pending:
                    duplicate = True
                else:
                    duplicate = False
                    if is_read:
                        if active_read < READ_LIMIT:
                            slot = "read"
                            active_read += 1
                    elif is_control:
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
            if slot == "read":
                fut = read_pool.submit(execute, raw, message, ident, cancel, slot)
                with pending_lock:
                    read_futures.add(fut)
                fut.add_done_callback(lambda done: read_futures.discard(done))
            else:
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
            pending_reads = {f for f in read_futures if not f.done()}
        for event in events:
            event.set()
        update.close()
        deadline = time.monotonic() + EOF_JOIN
        if pending_reads:
            wait_futures(pending_reads, timeout=max(0.0, deadline - time.monotonic()))
        read_pool.shutdown(wait=False, cancel_futures=True)
        for thread in list(workers):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
    return 0
