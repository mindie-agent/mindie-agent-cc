#!/usr/bin/env python3
"""Adapter-owned MCP: knowledge tools and general remote-dev.

Identity is never taken from tool arguments. Native PreToolUse binds
claudecode/toolUseId; this process consumes it once. Public knowledge reads
and remote-dev require the native bind, independently of contribution.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from identity import (
    KNOWLEDGE_TOOLS,
    canonical_native_name,
    claim_bind,
    finish_bind,
    is_knowledge_tool,
    meta_tool_use_id,
)
from paths import state_dir

MAX_YIELD_TIME_MS = 30000
REMOTE_ERROR_CATEGORIES = frozenset({
    "internal", "caller", "validation", "permission", "remote_execution",
    "cancelled", "connection_capacity", "connection_timeout",
    "connection_unavailable", "rpc_disconnected", "rpc_send", "rpc_timeout",
    "command_exit", "command_protocol", "command_timeout", "remote_worker",
    "worker_capacity",
})
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,95}$")

KNOWLEDGE_TOOL_DEFS = [
    dict(
        name="mindie_status",
        description=(
            "Read MindIE status and first-use choices for the current bound task. "
            "Includes safe failure categories and this task's contribution batch IDs "
            "for recovery inspection. Does not activate, enable sharing or retry work."
        ),
        inputSchema=dict(type="object", properties={}, additionalProperties=False),
    ),
    dict(
        name="knowledge_query",
        description=(
            "Search domain knowledge. Each match has a readable block ref and a "
            "feedback_ref. For more related matches, pass related_next as continuation "
            "without query. A changed corpus rejects the continuation. References are advisory."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                query={"type": "string"},
                limit={"type": "integer", "minimum": 1, "maximum": 20},
                conditions={"type": "object"},
                continuation={"type": "string"},
            ),
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_explain",
        description=(
            "Read one domain reference: a task ref returns the current navigation and "
            "first_block_ref; a block ref returns that immutable block with "
            "previous_block_ref/next_block_ref for walking the task. "
            "The content is a reference slice, not the full case or a verified report."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                ref={"type": "string"},
            ),
            required=["ref"],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_feedback",
        description="Optional up/down vote for a consulted reference. Never required.",
        inputSchema=dict(
            type="object",
            properties=dict(
                ref={"type": "string"},
                rating={"type": "string", "enum": ["up", "down"]},
                reason={"type": "string"},
            ),
            required=["ref", "rating"],
            additionalProperties=False,
        ),
    ),
]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def send(message):
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
        from remote_dev.core.errors import error_details

        details = error_details(exc)
        category = details.get("category")
        structured["category"] = (
            category if isinstance(category, str) and category in REMOTE_ERROR_CATEGORIES
            else "internal"
        )
        certainty = details.get("submission_state")
        if isinstance(certainty, str) and certainty in {"not_sent", "uncertain", "acknowledged"}:
            structured["submission_state"] = certainty
        if type(details.get("retryable")) is bool:
            structured["retryable"] = details["retryable"]
    key, value = _validated_job_ref(args)
    if key:
        structured[key] = value
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


def clamp_remote_args(args):
    body = dict(args)
    if "yield_time_ms" in body:
        yield_ms = body["yield_time_ms"]
        if type(yield_ms) is not int:
            raise ValueError("yield_time_ms must be an integer")
        body["yield_time_ms"] = min(yield_ms, MAX_YIELD_TIME_MS)
    return body


def knowledge_call(name, args, session):
    from knowledge_service import ensure_service
    from mindie_knowledge.loop.cli import rpc
    from paths import engine_config_path

    if name == "knowledge_query":
        # Keep the native schema a plain object; enforce the shared argument
        # contract before any service/storage operation.
        from mindie_knowledge.materials.provenance import query_request

        query_request(args.get("query"), args.get("limit", 5),
                      args.get("conditions"), args.get("continuation"))
    # Identity comes from the consumed native toolUseId bind. Public reads
    # use the native tool binding and do not require a capture lease.
    method = name.removeprefix("knowledge_")
    # Core's ready fast path verifies exact configuration, retirement and
    # worker health before reusing the authenticated connection.
    connection = ensure_service(engine_config_path())
    return rpc(
        connection,
        method,
        dict(args, _session_id=session, _session_verified=True),
        timeout=None,
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
    from remote_dev.mcp.schemas import ALIASES
    from remote_dev.mcp.tools import call_tool
    from remote_dev.core.errors import RemoteExecutionError
    from receipt_layout import prepare_layout

    base = state_dir() / "remote"
    try:
        with prepare_layout(base):
            root = base / session
            root.mkdir(parents=True, exist_ok=True)
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


def handle(surface, message):
    if "id" not in message:
        return None
    method = message.get("method")
    ident = message["id"]
    if method == "initialize":
        return dict(
            jsonrpc="2.0",
            id=ident,
            result=dict(
                protocolVersion="2025-11-25",
                capabilities={"tools": {}},
                serverInfo={"name": f"mindie-cc-{surface}", "version": "0.1.0"},
            ),
        )
    if method == "ping":
        return dict(jsonrpc="2.0", id=ident, result={})
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        tools = KNOWLEDGE_TOOL_DEFS if surface == "knowledge" else remote_tools()
        return dict(jsonrpc="2.0", id=ident, result=dict(tools=tools))
    if method != "tools/call":
        return dict(
            jsonrpc="2.0",
            id=ident,
            error=dict(code=-32601, message="Method not found"),
        )
    params = message.get("params") or {}
    name = params.get("name")
    args = params.get("arguments") or {}
    call_id = None
    succeeded = False
    try:
        if not isinstance(args, dict):
            raise ValueError("invalid tool arguments")
        if surface == "knowledge" and (
            "session_id" in args or "sessionId" in args or "thread_id" in args
        ):
            raise ValueError("knowledge tool arguments must not include native session identity")
        call_id = meta_tool_use_id(params)
        native_name = canonical_native_name(surface, name)
        bound = claim_bind(call_id, native_name, args)
        session = bound["session"]
        local = name.rsplit("__", 1)[-1] if isinstance(name, str) else name
        if surface == "knowledge" and local == "mindie_status":
            from entry import status_payload

            payload = status_payload(session)
            result = dict(
                content=[dict(type="text", text=canonical(payload))],
                structuredContent=payload,
                isError=False,
            )
        elif surface == "knowledge":
            if local not in KNOWLEDGE_TOOLS and not is_knowledge_tool(native_name, surface):
                raise ValueError("unknown knowledge tool")
            payload = knowledge_call(local, args, session)
            result = dict(
                content=[dict(type="text", text=canonical(payload))],
                structuredContent=payload,
                isError=False,
            )
        else:
            try:
                payload = remote_call(local, args, session)
            except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
                return dict(
                    jsonrpc="2.0",
                    id=ident,
                    result=remote_stage_failure(args, local, "helper_failed", exc),
                )
            text = payload.get("text") if isinstance(payload, dict) else canonical(payload)
            result = dict(
                content=[
                    dict(
                        type="text",
                        text=text if isinstance(text, str) else canonical(payload),
                    )
                ],
                structuredContent=(
                    payload.get("result", payload) if isinstance(payload, dict) else payload
                ),
                isError=isinstance(payload, dict)
                and payload.get("result", {}).get("outcome")
                not in {None, "success", "cancelled"},
            )
        succeeded = result.get("isError") is not True
        return dict(jsonrpc="2.0", id=ident, result=result)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        return dict(jsonrpc="2.0", id=ident, result=failure(exc))
    finally:
        if call_id is not None:
            try:
                finish_bind(call_id, succeeded)
            except Exception:
                pass


def _read_line(stdin):
    line = stdin.readline()
    return line.rstrip(b"\r\n") if line else False


def serve(surface):
    stdin = sys.stdin.buffer
    while True:
        line = _read_line(stdin)
        if line is False:
            return
        if line is None or not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        response = handle(surface, message)
        if response is not None:
            send(response)


def serve_once(surface):
    raw = sys.stdin.buffer.read()
    if not raw:
        return
    line, _, _ = raw.partition(b"\n")
    if not line.strip():
        return
    try:
        message = json.loads(line)
    except ValueError:
        return
    if not isinstance(message, dict):
        return
    response = handle(surface, message)
    if response is not None:
        send(response)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    once = "--once" in argv
    args = [item for item in argv if item != "--once"]
    surface = args[0] if args else "knowledge"
    if surface not in {"knowledge", "remote"}:
        raise SystemExit("surface must be knowledge or remote")
    if once:
        serve_once(surface)
        return
    serve(surface)


if __name__ == "__main__":
    main()
