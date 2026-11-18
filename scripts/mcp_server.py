#!/usr/bin/env python3
"""Adapter-owned MCP: default-on knowledge, remote-dev and deterministic components.

[Role]
Serves stdio JSON-RPC MCP requests for MindIE `knowledge`, `remote`, and
`components` surfaces in Claude Code with default-enabled `knowledge_*` discovery,
zero pre-call session-binding gates, and concurrent read-only execution (`read_pool`).

[Invariants]
- Session identity is never taken from model-supplied tool arguments. Across all
  surfaces (`knowledge`, `remote`, `components`), `PreToolUse` `claim_bind` is an
  optional hint (supplying native `session`/`cwd` and rejecting duplicate `call_id`
  replay when bound) that falls back cleanly to verified `_meta`/env session IDs
  or `_CONNECTION_SESSION_ID` for subagents, forks, and non-hooked MCP calls.
- `knowledge_*` tools work out of the box without manual slash-command activation,
  auto-provisioning a default session lease (`not_before=0.0`) on demand when
  `mindie_knowledge` requires an active lease (`knowledge_distill` and scoped
  `knowledge_feedback`). Read-only calls never create `admission.sqlite3`.
- Receipt Honesty: once a primary operation succeeds, local SQLite accounting
  failures keep `isError=False` and attach `receipt_warning` +
  `receipt_unconfirmed=True` (plus `bind_warning`/`diagnostic_ref` for `finish_bind`).

[Timeouts & Locks]
- Uses `read_pool` (`ThreadPoolExecutor`) for `readOnlyHint=True` tools
  (`knowledge_query`, `knowledge_explain`, `mindie_status`, `diagnostics_status`,
  and read-only `remote_*` tools) so reads never block behind slow mutations.

[Side-Effect Policy]
- Dispatches authorized calls to shared engines and explicit component CLIs.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait as wait_futures
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
import uuid

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from identity import (
    KNOWLEDGE_TOOLS, MissingBind, SESSION_RE, bind_storage_facts,
    canonical_native_name, claim_bind, finish_bind, is_knowledge_tool, meta_tool_use_id,
)
from mcp_catalog import KNOWLEDGE_TOOL_DEFS, READONLY_KNOWLEDGE_TOOLS, is_readonly_tool
from paths import state_dir
from product_contract import installed_version
import rsi_explore

MAX_YIELD_TIME_MS = 30000
KNOWLEDGE_RPC_TIMEOUT = 30.0
KNOWLEDGE_SYNC_TIMEOUT = 120.0
READ_POOL_WORKERS = 16
MUTATION_POOL_WORKERS = 8
EOF_DRAIN_SECONDS = 3.0
REMOTE_ERROR_CATEGORIES = frozenset({
    "internal", "caller", "validation", "permission", "remote_execution",
    "cancelled", "connection_capacity", "connection_timeout",
    "connection_unavailable", "rpc_disconnected", "rpc_send", "rpc_timeout",
    "command_exit", "command_protocol", "command_timeout", "remote_worker",
    "worker_capacity",
})
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,95}$")
_CONNECTION_SESSION_ID = (
    os.environ.get("MINDIE_MCP_CONNECTION_ID")
    or f"cc-mcp-{os.getpid()}-{uuid.uuid4().hex[:12]}"
)
_SEND_LOCK = threading.Lock()
_REMOTE_CALL_LOCK = threading.Lock()
_CLAIMED_LOCK = threading.Lock()
_CLAIMED_CALL_IDS: dict[str, None] = {}
_MAX_CLAIMED_CACHE = 4096


def _remember_claimed(call_id: str) -> None:
    with _CLAIMED_LOCK:
        if len(_CLAIMED_CALL_IDS) >= _MAX_CLAIMED_CACHE:
            oldest = next(iter(_CLAIMED_CALL_IDS))
            _CLAIMED_CALL_IDS.pop(oldest, None)
        _CLAIMED_CALL_IDS[call_id] = None


def _fallback_session(params: dict) -> str:
    meta = params.get("_meta")
    if isinstance(meta, dict):
        for key in ("claudecode/sessionId", "sessionId", "session_id"):
            val = meta.get(key)
            if isinstance(val, str) and SESSION_RE.fullmatch(val.strip()):
                return val.strip()
    for env_key in ("CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        val = os.environ.get(env_key, "").strip()
        if val and SESSION_RE.fullmatch(val):
            return val
    return _CONNECTION_SESSION_ID


def _effective_cwd(bound_cwd: str | None) -> str:
    if isinstance(bound_cwd, str) and os.path.isabs(bound_cwd):
        return bound_cwd
    for env_key in ("CLAUDE_PROJECT_DIR", "MINDIE_HOST_CWD"):
        val = os.environ.get(env_key, "").strip()
        if val and os.path.isabs(val):
            return val
    return os.getcwd()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def send(message):
    with _SEND_LOCK:
        print(canonical(message), flush=True)


def failure(exc):
    text = f"Unavailable: {exc}. Continue independently."[:500]
    from diagnostic_support import attach

    result = dict(content=[dict(type="text", text=text)], isError=True)
    return attach(result, getattr(exc, "mindie_diagnostic", None))


def _validated_job_ref(args):
    if not isinstance(args, dict):
        return None, None
    for key in ("job_id", "session_id"):
        value = args.get(key)
        if isinstance(value, str) and JOB_ID_RE.fullmatch(value):
            return key, value
    return None, None


def remote_stage_failure(args, name, stage, exc=None):
    structured = {"stage": stage, "remote_outcome": "unconfirmed"}
    if exc is not None:
        from remote_dev.core.errors import AUTH_ERROR_HINTS, error_details, safe_auth_error_details

        details = error_details(exc)
        category = details.get("category")
        structured["category"] = (
            category if isinstance(category, str) and category in REMOTE_ERROR_CATEGORIES | AUTH_ERROR_HINTS.keys()
            else "internal"
        )
        certainty = details.get("submission_state")
        if isinstance(certainty, str) and certainty in {"not_sent", "uncertain", "acknowledged"}:
            structured["submission_state"] = certainty
        if type(details.get("retryable")) is bool:
            structured["retryable"] = details["retryable"]
        auth = safe_auth_error_details(details)
        if auth is not None:
            structured.update(auth)
        if structured.get("submission_state") == "not_sent":
            structured["remote_outcome"] = "not_sent"
        repair = details.get("input_repair")
        if (structured["category"] == "caller"
                and structured.get("submission_state") == "not_sent"
                and isinstance(repair, dict)):
            structured["input_repair"] = repair
            structured["remote_outcome"] = "not_sent"
    key, value = _validated_job_ref(args)
    if key:
        structured[key] = value
    repair = structured.get("input_repair")
    if isinstance(repair, dict):
        missing = [item for item in repair.get("missing", ()) if isinstance(item, str)]
        action = repair.get("action") if isinstance(repair.get("action"), str) else None
        text = f"Remote request was not sent ({stage})."
        if missing:
            text += f" Missing required argument(s): {', '.join(missing)}."
        if action:
            text += f" {action}"
    elif structured.get("hint"):
        text = (
            f"Remote request was not sent ({stage})."
            if structured.get("submission_state") == "not_sent"
            else f"Remote outcome unconfirmed ({stage}); inspect the original operation before repeating it."
        )
        text += " " + structured["hint"]
    elif key:
        text = (
            f"Remote outcome unconfirmed ({stage}). "
            f"Inspect or stop original {key} {value} before a deliberate repeat."
        )
    elif name == "remote_bash":
        if structured.get("submission_state") == "not_sent":
            structured["remote_outcome"] = "not_sent"
            text = f"Remote request was not sent ({stage})."
        else:
            text = (
                f"Remote submission unconfirmed ({stage}). "
                "Inspect local remote-dev job records before a deliberate repeat."
            )
    else:
        text = f"Unavailable ({stage}). Continue independently."
    for key in ("category", "submission_state"):
        if key in structured:
            text += f" {key}={structured[key]}."
    from diagnostic_support import attach

    result = dict(
        content=[dict(type="text", text=text[:500])],
        isError=True,
        structuredContent=structured,
    )
    return attach(result, getattr(exc, "mindie_diagnostic", None))


def _timeout_failure(surface, args, name, exc):
    """Timeout evidence stays with the surface that performed the operation."""
    if surface in {"knowledge", "components"}:
        from diagnostic_support import attach

        result = dict(
            content=[dict(type="text", text="Unavailable: timeout. Continue independently.")],
            isError=True,
            structuredContent=dict(stage="timeout"),
        )
        if surface == "knowledge":
            result["structuredContent"]["retryable"] = True
        return attach(result, getattr(exc, "mindie_diagnostic", None))
    result = remote_stage_failure(args, name, "timeout", exc)
    structured = result.setdefault("structuredContent", {})
    structured["stage"] = "timeout"
    structured["retryable"] = True
    return result


def clamp_remote_args(args):
    body = dict(args)
    if "yield_time_ms" in body:
        yield_ms = body["yield_time_ms"]
        if type(yield_ms) is not int:
            raise ValueError("yield_time_ms must be an integer")
        body["yield_time_ms"] = min(yield_ms, MAX_YIELD_TIME_MS)
    return body


_TERMINAL_COMMAND_STATES = {"succeeded", "failed", "timeout", "cancelled"}
_COMMAND_TOOLS = {
    "remote_Bash", "remote_bash", "remote_BashOutput", "remote_TaskOutput",
    "remote_exec_command", "remote_write_stdin", "remote_shell",
}


def _completed_command(name, result):
    """A native command that really ran may exit nonzero; keep its output."""
    if name not in _COMMAND_TOOLS or not isinstance(result, dict):
        return False
    error_details = result.get("error_details")
    if result.get("unknown") or (
            isinstance(error_details, dict)
            and error_details.get("submission_state") == "uncertain"):
        return False
    state = result.get("state") or result.get("status")
    if state not in _TERMINAL_COMMAND_STATES:
        return False
    exit_code = result.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        return True
    task = result.get("task")
    if isinstance(task, dict) and isinstance(task.get("exitCode"), int) \
            and not isinstance(task.get("exitCode"), bool):
        return True
    return False


def remote_result(name, payload):
    """Preserve native content blocks and a completed nonzero command."""
    result_payload = payload.get("result", payload) if isinstance(payload, dict) else payload
    text = payload.get("text") if isinstance(payload, dict) else None
    blocks = [dict(
        type="text",
        text=text if isinstance(text, str) else canonical(payload),
    )]
    native = payload.get("content") if isinstance(payload, dict) else None
    if isinstance(native, list):
        blocks.extend(item for item in native
                      if isinstance(item, dict) and isinstance(item.get("type"), str))
    if isinstance(result_payload, dict):
        error_details = result_payload.get("error_details")
        uncertain = (
            result_payload.get("status") == "submission_uncertain"
            or result_payload.get("unknown")
            or (isinstance(error_details, dict)
                and error_details.get("submission_state") == "uncertain")
        )
    else:
        uncertain = False
    if uncertain:
        is_error = True
    elif _completed_command(name, result_payload):
        is_error = False
    elif isinstance(result_payload, dict):
        outcome = result_payload.get("outcome")
        if result_payload.get("status") == "local_recording_failed":
            completed_locally = (
                result_payload.get("operation_completed") is True
                or (isinstance(result_payload.get("error_details"), dict)
                    and result_payload["error_details"].get("operation_completed") is True)
            )
            if completed_locally:
                outcome = result_payload.get("execution_outcome", outcome)
        is_error = outcome not in {None, "success", "cancelled"}
    else:
        is_error = False
    if is_error or any(block.get("type") != "text" for block in blocks):
        return dict(content=blocks, structuredContent=result_payload, isError=is_error)
    return dict(content=blocks, isError=is_error)


def _public_domain(args):
    """Read the optional public domain without accepting any path or mapping."""
    if not isinstance(args, dict) or "domain" not in args:
        return None
    value = args["domain"]
    if not isinstance(value, str):
        raise ValueError("domain must match [a-z][a-z0-9-]{0,63}")
    return value


def _distill_timeout():
    """The paired distill RPC budget: scanners, identity, worker, ledger and cleanup."""
    from mindie_knowledge.loop import summary_budget
    return summary_budget.distill_rpc_timeout()


def _maybe_provision_feedback_lease(session: str, project_root: str, target_eng) -> None:
    from admission import ensure_default_lease, knowledge_lease
    from knowledge_service import engine_config
    from mindie_knowledge.loop.settings import from_engine_config

    if knowledge_lease(session, engine=target_eng) is not None:
        return
    cfg = target_eng if target_eng is not None else engine_config()
    settings = from_engine_config(cfg)
    if settings.allows_capture() and settings.in_scope(project_root):
        ensure_default_lease(
            session, project_root=project_root, not_before=0.0, engine=target_eng
        )


def _knowledge_distill_call(args, session, ensure_service, rpc, *, cwd=None):
    """Forward one paid C6 request, auto-provisioning a session lease on demand."""
    allowed = {"inputs", "task_state"}
    if isinstance(args, dict):
        for optional in ("domain", "related_refs", "proposal"):
            if optional in args:
                allowed.add(optional)
    if not isinstance(args, dict) or set(args) != allowed:
        raise ValueError(
            "knowledge_distill accepts only inputs, task_state, related_refs, proposal "
            "and optional domain"
        )
    if "related_refs" in args:
        related_refs = args["related_refs"]
        if not isinstance(related_refs, list) or not all(
                isinstance(item, str) and item for item in related_refs):
            raise ValueError(
                "related_refs must be an array of nonempty stable unit IDs or graded references"
            )
    if "proposal" in args and not isinstance(args["proposal"], dict):
        raise ValueError("proposal must be a mindie-distill-decision/1 object")
    domain = _public_domain(args)
    try:
        from mindie_knowledge.graded import distill
    except ImportError as exc:
        raise ValueError(
            "knowledge_distill is unavailable in the installed knowledge runtime"
        ) from exc
    if not callable(getattr(distill, "prepare_request", None)) or not callable(
            getattr(distill, "value_gate", None)):
        raise ValueError("knowledge_distill is unavailable in the installed knowledge runtime")
    if args["task_state"] not in distill.TASK_STATES:
        raise ValueError("task_state must be in_flight, verified_milestone or completed")
    if not isinstance(args["inputs"], list) or not args["inputs"]:
        raise ValueError("inputs must be a nonempty selected-evidence array")
    from admission import ensure_default_lease
    from knowledge_service import engine_config, select_engine

    selected, routed_domain = select_engine(domain=domain)
    target_eng = engine_config(str(selected)) if selected is not None else None
    lease = ensure_default_lease(
        session,
        project_root=_effective_cwd(cwd),
        not_before=0.0,
        engine=target_eng,
    )
    try:
        timeout = _distill_timeout()
    except ImportError as exc:
        raise ValueError(
            "knowledge_distill is unavailable in the installed knowledge runtime"
        ) from exc
    connection = ensure_service() if selected is None else ensure_service(selected)
    payload = dict(args, _session_id=session, _activation=lease["token"])
    if routed_domain is not None:
        payload["domain"] = routed_domain
    return rpc(connection, "distill", payload, timeout=timeout)


def _read_or_feedback_call(name, args, session, ensure_service, rpc, *, cwd=None):
    from admission import is_deactivated
    from knowledge_service import engine_config, select_engine_for_refs

    body = rsi_explore.forwarded(args) if name == "knowledge_explain" else dict(args)
    selected, routed_domain = select_engine_for_refs(
        body.get("refs"), ref=body.get("ref"))
    target_eng = engine_config(str(selected)) if selected is not None else None
    if is_deactivated(session, engine=target_eng):
        raise ValueError("knowledge session lease is inactive")
    if name == "knowledge_feedback":
        _maybe_provision_feedback_lease(session, _effective_cwd(cwd), target_eng)
    payload = dict(body, _session_id=session, _session_verified=True)
    if routed_domain is not None and "domain" not in payload:
        payload["domain"] = routed_domain
    connection = ensure_service() if selected is None else ensure_service(selected)
    return rpc(
        connection, name.removeprefix("knowledge_"), payload,
        timeout=KNOWLEDGE_RPC_TIMEOUT,
    )


def knowledge_call(name, args, session, *, cwd=None):
    from admission import is_deactivated
    from knowledge_service import engine_config, ensure_service, select_engine
    from mindie_knowledge.loop.cli import rpc

    if name == "knowledge_sync":
        from maintenance import sync_public_feed
        from paths import configured_python, engine_config_path

        if set(args) - {"domain"}:
            raise ValueError("knowledge_sync accepts only the optional domain")
        selected, _ = select_engine(domain=_public_domain(args))
        target = selected or engine_config_path()
        if is_deactivated(session, engine=engine_config(str(target))):
            raise ValueError("knowledge session lease is inactive")
        return sync_public_feed(configured_python(), target, timeout=KNOWLEDGE_SYNC_TIMEOUT)

    if name == "knowledge_query":
        from mindie_knowledge.materials.provenance import query_request

        body = rsi_explore.forwarded(args)
        query_request(body.get("query"), body.get("limit", 5),
                      body.get("conditions"), body.get("continuation"))
        domain = _public_domain(body)
        selected, routed_domain = select_engine(domain=domain)
        target_eng = engine_config(str(selected)) if selected is not None else None
        if is_deactivated(session, engine=target_eng):
            raise ValueError("knowledge session lease is inactive")
        payload = dict(body, _session_id=session, _session_verified=True)
        if routed_domain is not None:
            payload["domain"] = routed_domain
        connection = ensure_service() if selected is None else ensure_service(selected)
        return rpc(connection, "query", payload, timeout=KNOWLEDGE_RPC_TIMEOUT)
    if name == "knowledge_distill":
        return _knowledge_distill_call(args, session, ensure_service, rpc, cwd=cwd)
    if name in {"knowledge_explain", "knowledge_feedback"}:
        return _read_or_feedback_call(name, args, session, ensure_service, rpc, cwd=cwd)
    if is_deactivated(session):
        raise ValueError("knowledge session lease is inactive")
    method = name.removeprefix("knowledge_")
    connection = ensure_service()
    return rpc(
        connection,
        method,
        dict(args, _session_id=session, _session_verified=True),
        timeout=KNOWLEDGE_RPC_TIMEOUT,
    )


def remote_tools():
    from remote_dev.mcp.tools import list_tools

    tools = []
    for item in list_tools():
        schema = dict(item.get("inputSchema") or {"type": "object", "properties": {}})
        tools.append(
            dict(
                name=item["name"],
                description=item.get("description", item["name"]),
                inputSchema=schema,
            )
        )
    return tools


def remote_call(name, args, session):
    with _REMOTE_CALL_LOCK:
        os.environ["REMOTE_DEV_HARNESS_PROFILE"] = "claude"
        from remote_dev.core.errors import RemoteExecutionError
        from remote_dev.mcp.schemas import ALIASES
        from remote_dev.mcp.tools import call_tool
        from receipt_layout import prepare_layout

        base = state_dir() / "remote"
        try:
            with prepare_layout(base):
                root = base
                root.mkdir(parents=True, exist_ok=True)
        except TimeoutError:
            raise
        except (ValueError, OSError) as exc:
            raise RemoteExecutionError(
                "remote state preparation failed; existing receipts preserved",
                category="internal", submission_state="not_sent",
            ) from exc
        for inherited in (
            "CLAUDE_SESSION_ID",
            "CLAUDE_CODE_SESSION_ID",
            "CODEX_SESSION_ID",
            "CODEX_RUN_ID",
        ):
            os.environ.pop(inherited, None)
        os.environ["REMOTE_DEV_SESSION_ID"] = session
        os.environ["REMOTE_DEV_STATE_DIR"] = str(root)
        canonical_name = ALIASES.get(name, name)
        return call_tool(canonical_name, clamp_remote_args(args))


def _bind_warning(result, exc):
    """Receipt storage cannot change a completed tool's primary outcome."""
    from diagnostic_support import failure as record_failure

    diagnostic = record_failure("mcp.call", "finish_bind", "local_storage", exception=exc)
    warning = "Local bind receipt could not be saved. The tool result is unchanged; do not repeat the call."
    fields = dict(
        bind_warning=warning, bind_error_type=type(exc).__name__, diagnostic_ref=diagnostic,
    )
    if result.get("isError") is not True:
        fields["receipt_warning"] = warning
        fields["receipt_unconfirmed"] = True
    facts = bind_storage_facts(exc)
    if "errno" in facts:
        fields["bind_errno"] = facts["errno"]
    if "path" in facts:
        fields["bind_path"] = facts["path"]
    result["structuredContent"] = dict(result.get("structuredContent") or {}, **fields)
    result["content"] = [*result.get("content", []), dict(type="text", text=warning)]


def handle(surface, message):
    if "id" not in message:
        return None
    method = message.get("method")
    ident = message["id"]
    if method == "initialize":
        return dict(jsonrpc="2.0", id=ident, result=dict(
            protocolVersion="2025-11-25", capabilities={"tools": {}},
            serverInfo={"name": f"mindie-cc-{surface}", "version": installed_version()},
        ))
    if method == "ping":
        return dict(jsonrpc="2.0", id=ident, result={})
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        if surface == "knowledge":
            tools = KNOWLEDGE_TOOL_DEFS
        elif surface == "components":
            from component_tools import catalog
            tools = catalog()
        else:
            tools = remote_tools()
        return dict(jsonrpc="2.0", id=ident, result=dict(tools=tools))
    if method != "tools/call":
        return dict(jsonrpc="2.0", id=ident, error=dict(code=-32601, message="Method not found"))
    params = message.get("params") or {}
    name = params.get("name")
    args = params.get("arguments") or {}
    call_id = None
    claimed = False
    succeeded = False
    result = {}
    local = None
    try:
        if not isinstance(args, dict):
            raise ValueError("invalid tool arguments")
        if surface == "knowledge" and (
            any(key in args for key in (
                "session_id", "sessionId", "thread_id",
                "_session_id", "_activation", "_session_verified",
            ))
            or any(isinstance(key, str) and key.startswith("_") for key in args)
        ):
            raise ValueError("knowledge tool arguments must not include native session identity")
        native_name = canonical_native_name(surface, name)
        bound = {}
        meta = params.get("_meta")
        has_meta_id = isinstance(meta, dict) and meta.get("claudecode/toolUseId") is not None
        call_id = meta_tool_use_id(params) if has_meta_id else None
        if call_id is not None:
            with _CLAIMED_LOCK:
                replayed = call_id in _CLAIMED_CALL_IDS
            if replayed:
                raise ValueError("toolUseId has already been used; replay rejected")
            try:
                bound = claim_bind(call_id, native_name, args)
                claimed = True
                _remember_claimed(call_id)
                session = bound["session"]
            except MissingBind:
                session = _fallback_session(params)
        else:
            session = _fallback_session(params)
        local = name.rsplit("__", 1)[-1] if isinstance(name, str) else name
        if surface == "knowledge" and local == "mindie_status":
            from entry import status_facts

            payload = status_facts(session)
            result = dict(
                content=[dict(type="text", text=canonical(payload))],
                structuredContent=payload,
                isError=False,
            )
        elif surface == "knowledge":
            if local not in KNOWLEDGE_TOOLS and not is_knowledge_tool(native_name, surface):
                raise ValueError("unknown knowledge tool")
            bound_cwd = bound.get("cwd")
            if bound_cwd is not None:
                payload = knowledge_call(local, args, session, cwd=bound_cwd)
            else:
                payload = knowledge_call(local, args, session)
            is_error = False
            if local == "knowledge_distill":
                from mindie_knowledge.graded.distill import result_view
                payload, is_error = result_view(payload)
            elif local == "knowledge_sync":
                is_error = payload.get("result") != "ok"
                if not is_error and payload.get("cleanup_failed"):
                    payload = dict(payload)
                    warning = (
                        "sync completed but owned-process cleanup failed; "
                        "inspect the completed feed results before repeating"
                    )
                    payload["cleanup_warning"] = warning
                    payload["receipt_warning"] = warning
                    payload["receipt_unconfirmed"] = True
            result = dict(
                content=[dict(type="text", text=canonical(payload))],
                structuredContent=payload if isinstance(payload, dict) else {"items": payload},
                isError=is_error,
            )
        elif surface == "components":
            from component_tools import execute
            result = execute(local, args, session, cwd=bound.get("cwd"))
        else:
            try:
                payload = remote_call(local, args, session)
            except TimeoutError as exc:
                result = _timeout_failure("remote", args, local, exc)
                return dict(jsonrpc="2.0", id=ident, result=result)
            except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
                result = remote_stage_failure(args, local, "helper_failed", exc)
                return dict(jsonrpc="2.0", id=ident, result=result)
            result = remote_result(local, payload)
        succeeded = result.get("isError") is not True
        return dict(jsonrpc="2.0", id=ident, result=result)
    except TimeoutError as exc:
        result = _timeout_failure(surface, args, local or name, exc)
        return dict(jsonrpc="2.0", id=ident, result=result)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        result = failure(exc)
        return dict(jsonrpc="2.0", id=ident, result=result)
    finally:
        if claimed:
            try:
                finish_bind(call_id, succeeded)
            except TimeoutError as exc:
                _bind_warning(result, exc)
            except (OSError, sqlite3.Error) as exc:
                _bind_warning(result, exc)


def _read_line(stdin):
    line = stdin.readline()
    return line.rstrip(b"\r\n") if line else False


def _resident_binding():
    from install_state import read_binding

    try:
        return read_binding()
    except TimeoutError:
        raise
    except (OSError, ValueError, RuntimeError):
        return None


def serve(surface):
    from storage_lifecycle import runtime_leases

    read_pool = ThreadPoolExecutor(max_workers=READ_POOL_WORKERS, thread_name_prefix="mindie-mcp-read")
    mut_pool = ThreadPoolExecutor(max_workers=MUTATION_POOL_WORKERS, thread_name_prefix="mindie-mcp-mut")
    futures = set()
    futures_lock = threading.Lock()

    def _run_and_send(msg):
        response = handle(surface, msg)
        if response is not None:
            send(response)

    with runtime_leases(_resident_binding()):
        stdin = sys.stdin.buffer
        try:
            while True:
                line = _read_line(stdin)
                if line is False:
                    break
                if line is None or not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("method") == "tools/call" and "id" in message:
                    params = message.get("params") if isinstance(message.get("params"), dict) else {}
                    pool = read_pool if is_readonly_tool(surface, params.get("name")) else mut_pool
                    fut = pool.submit(_run_and_send, message)
                    with futures_lock:
                        futures.add(fut)
                    fut.add_done_callback(lambda done: futures.discard(done))
                    continue
                _run_and_send(message)
        finally:
            with futures_lock:
                pending = {f for f in futures if not f.done()}
            if pending:
                wait_futures(pending, timeout=EOF_DRAIN_SECONDS)
            read_pool.shutdown(wait=True, cancel_futures=True)
            mut_pool.shutdown(wait=True, cancel_futures=True)


def serve_once(surface):
    raw = sys.stdin.buffer.read()
    line = raw.partition(b"\n")[0].strip() if raw else b""
    if not line:
        return
    try:
        message = json.loads(line)
    except ValueError:
        return
    if isinstance(message, dict):
        response = handle(surface, message)
        if response is not None:
            send(response)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    once = "--once" in argv
    args = [item for item in argv if item != "--once"]
    surface = args[0] if args else "knowledge"
    if surface not in {"knowledge", "remote", "components"}:
        raise SystemExit("surface must be knowledge, remote or components")
    if once:
        serve_once(surface)
        return
    serve(surface)


if __name__ == "__main__":
    main()
