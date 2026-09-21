#!/usr/bin/env python3
"""Adapter-owned MCP: knowledge tools and general remote-dev.

Identity is never taken from tool arguments. Native PreToolUse binds
claudecode/toolUseId; this process consumes it once. Knowledge calls
require a shared active lease. Remote-dev requires the bind only.
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
from paths import load_engine_config, state_dir

MAX_LINE = 128 * 1024
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
            "Does not activate the task or enable sharing."
        ),
        inputSchema=dict(type="object", properties={}, additionalProperties=False),
    ),
    dict(
        name="knowledge_query",
        description="Search the selected domain's knowledge and experience. References are advisory.",
        inputSchema=dict(
            type="object",
            properties=dict(
                query={"type": "string"},
                limit={"type": "integer", "minimum": 1, "maximum": 20},
                conditions={"type": "object"},
            ),
            required=["query"],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_explain",
        description="Read one domain reference. offset/limit are character positions.",
        inputSchema=dict(
            type="object",
            properties=dict(
                ref={"type": "string"},
                offset={"type": "integer", "minimum": 0},
                limit={"type": "integer", "minimum": 1, "maximum": 65536},
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
    return dict(content=[dict(type="text", text=text)], isError=True)


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
    return dict(
        content=[dict(type="text", text=text[:500])],
        isError=True,
        structuredContent=structured,
    )


def clamp_remote_args(args):
    body = dict(args)
    if "yield_time_ms" in body:
        yield_ms = body["yield_time_ms"]
        if type(yield_ms) is not int:
            raise ValueError("yield_time_ms must be an integer")
        body["yield_time_ms"] = min(yield_ms, MAX_YIELD_TIME_MS)
    return body


def knowledge_call(name, args, session):
    from knowledge_service import ensure_service, existing_service
    from mindie_knowledge.loop.activation import Admission
    from mindie_knowledge.loop.cli import rpc
    from paths import admission_path, engine_config_path

    engine = load_engine_config()
    if Admission(admission_path(engine)).active_lease(session) is None:
        raise ValueError("session is not manually activated")
    method = name.removeprefix("knowledge_")
    try:
        connection = existing_service(engine_config_path())
    except (OSError, ValueError, RuntimeError):
        connection = ensure_service(engine_config_path())
    return rpc(
        connection,
        method,
        dict(args, _session_id=session, _session_verified=True),
        timeout=5,
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

    root = state_dir() / "remote" / session
    root.mkdir(parents=True, exist_ok=True)
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


def _read_line(stdin, limit: int):
    line = stdin.readline(limit + 1)
    if line == b"":
        return False
    if len(line) > limit and not line.endswith(b"\n"):
        while True:
            chunk = stdin.readline(limit + 1)
            if not chunk or chunk.endswith(b"\n"):
                break
        return None
    if line.endswith(b"\n"):
        line = line[:-1]
    if len(line) > limit:
        return None
    return line


def serve(surface):
    stdin = sys.stdin.buffer
    while True:
        line = _read_line(stdin, MAX_LINE)
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
    raw = sys.stdin.buffer.read(MAX_LINE + 1)
    if not raw or len(raw) > MAX_LINE:
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
