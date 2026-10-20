#!/usr/bin/env python3
"""Adapter-owned MCP: knowledge tools and general remote-dev.

[Role]
Serves stdio JSON-RPC MCP requests for MindIE knowledge tools and `remote_dev`
tools in Claude Code.

[Invariants]
- Session identity is never taken from model-supplied tool arguments; `PreToolUse`
  binds `claudecode/toolUseId` and this server consumes it at most once via
  `claim_bind` / `finish_bind`.
- Once a remote or knowledge tool call succeeds, a local SQLite bind-accounting
  failure (`finish_bind`) attaches `bind_warning` + `diagnostic_ref` and never
  flips `isError=True` on the succeeded result.

[Timeouts & Locks]
- Consumes one-shot SQLite WAL binds (`identity.py`) and prepares remote receipt
  layouts (`receipt_layout.py`) before external execution.

[Side-Effect Policy]
- Dispatches authorized tool calls to `mindie_knowledge` and `remote_dev.mcp.server`.
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
from product_contract import installed_version

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
            "Search graded rules and task material in the selected domain. Each match has a readable ref "
            "and feedback_ref for its observed revision. Pass known task conditions to filter graded rules. "
            "Graded corpora return bounded cards plus deduplicated foundation_cards; include_archived "
            "also discovers archived or superseded units when investigating old conclusions. "
            "For related task blocks, pass related_next as continuation without query. "
            "A changed corpus rejects the continuation. References are advisory."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                query={"type": "string"},
                limit={"type": "integer", "minimum": 1, "maximum": 20},
                conditions={"type": "object"},
                continuation={"type": "string"},
                domain={
                    "type": "string",
                    "description": "Optional configured domain name; omit for the installation default.",
                },
                domain_path={"type": "string"},
                token_budget={"type": "integer", "minimum": 1, "maximum": 16000},
                include_unknown={"type": "boolean"},
                include_archived={"type": "boolean"},
            ),
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_explain",
        description=(
            "Read one domain reference or a batch of exact unit references. A single ref keeps the "
            "historical contract: a graded-unit ref returns its exact current revision and conditions; "
            "a task ref returns the current navigation and first_block_ref; a block ref returns that "
            "immutable block with previous_block_ref/next_block_ref for walking the task. "
            "Batch refs return the shared dependency closure once in requires and full unit bodies "
            "in units, using conditions and known_refs (only exact id@revision units still fully "
            "visible) to avoid re-sending visible bodies; unresolved reports condition or budget "
            "exclusions instead of truncated steps. The content is a reference slice, not the full "
            "case or a verified report."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                ref={"type": "string"},
                refs={"type": "array", "items": {"type": "string"}},
                conditions={"type": "object"},
                known_refs={"type": "array", "items": {"type": "string"}},
                token_budget={"type": "integer", "minimum": 1, "maximum": 16000},
            ),
            required=[],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_feedback",
        description=(
            "Record optional usage evidence for an exact observed ref: the historical up/down rating, "
            "or a 4-stage usage event (discovered, loaded, applied, evaluated). evaluated requires "
            "run_id and outcome. Copy the exact feedback_ref returned by query/explain into ref. "
            "Never required."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                ref={"type": "string"},
                rating={"type": "string", "enum": ["up", "down"]},
                reason={"type": "string"},
                event_type={
                    "type": "string",
                    "enum": ["discovered", "loaded", "applied", "evaluated"],
                },
                run_id={"type": "string"},
                step_id={"type": "string"},
                outcome={
                    "type": "string",
                    "enum": ["supported", "refuted", "inconclusive"],
                },
            ),
            required=["ref"],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_distill",
        description=(
            "Distill only explicitly selected evidence into C5 graded units. The "
            "shared runtime redacts the selected inputs before the model call. This "
            "requires the current active capture lease and saved contribution "
            "authorization. task_state is explicit; an ordinary Stop does not mean "
            "completion. Pass related_refs to compare the new evidence against existing units, "
            "or a foreground mindie-distill-decision/1 proposal for deterministic validation "
            "and application without another model call. This is one paid native model call "
            "when no proposal is supplied: it never discovers transcripts, grants publication, "
            "or retries an uncertain result."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                inputs=dict(
                    type="array",
                    minItems=1,
                    items=dict(
                        type="object",
                        properties=dict(
                            kind={"enum": [
                                "experiment_ledger", "run_manifest",
                                "authorized_transcript", "ecosystem_repo",
                                "hardware_spec", "ablation_receipt",
                            ]},
                            outcome={"enum": [
                                "observed", "verified", "compile_failed",
                                "infra_failed", "measurement_invalid", "in_flight",
                            ]},
                            source_locator={"type": "string", "minLength": 1},
                            content={"type": "string", "minLength": 1},
                            evidence_refs={
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            repetitions={"type": "integer", "minimum": 1},
                        ),
                        required=[
                            "kind", "outcome", "source_locator", "content",
                            "evidence_refs",
                        ],
                        additionalProperties=False,
                    ),
                ),
                task_state={"enum": [
                    "in_flight", "verified_milestone", "completed",
                ]},
                domain={
                    "type": "string",
                    "description": "Optional configured domain name; omit for the installation default.",
                },
                related_refs={"type": "array", "items": {"type": "string"}},
                proposal={"type": "object"},
            ),
            required=["inputs", "task_state"],
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


def _public_domain(args):
    """Read the optional public domain without accepting any path or mapping."""
    if not isinstance(args, dict) or "domain" not in args:
        return None
    value = args["domain"]
    if not isinstance(value, str):
        raise ValueError("domain must match [a-z][a-z0-9-]{0,63}")
    return value


def _distill_timeout():
    """The paired distill RPC budget: scanners, identity, worker, ledger and cleanup.

    Mirrors shared ``cli.distill_selected`` so this synchronous forward never
    outlives the native call it bounds.
    """
    from mindie_knowledge.loop import summary_budget
    return summary_budget.distill_rpc_timeout()


def _knowledge_distill_call(args, session, ensure_service, rpc):
    """Forward one paid C6 request through the selected engine's own lease."""
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
    # The native toolUseId only proves the host call. Paid distillation also
    # requires the selected engine's current active capture lease. An explicit
    # domain must never inherit the default domain's activation.
    from admission import gate
    from knowledge_service import engine_config, select_engine

    selected, routed_domain = select_engine(domain=domain)
    if selected is None:
        lease = gate().check(session)
    else:
        lease = gate(engine_config(str(selected))).check(session)
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


def _read_or_feedback_call(name, args, session, ensure_service, rpc):
    from knowledge_service import select_engine_for_refs

    # A single ref keeps its URI authority. A batch routes by its first ref,
    # but two different mindie:// authorities must never share one response.
    selected, routed_domain = select_engine_for_refs(
        args.get("refs"), ref=args.get("ref"))
    payload = dict(args, _session_id=session, _session_verified=True)
    if routed_domain is not None and "domain" not in payload:
        payload["domain"] = routed_domain
    connection = ensure_service() if selected is None else ensure_service(selected)
    return rpc(connection, name.removeprefix("knowledge_"), payload, timeout=None)


def knowledge_call(name, args, session):
    from knowledge_service import ensure_service, select_engine
    from mindie_knowledge.loop.cli import rpc

    if name == "knowledge_query":
        # Keep the native schema a plain object; enforce the shared argument
        # contract before any service/storage operation. The newer graded
        # parameters are forwarded unchanged and validated by the shared Store.
        from mindie_knowledge.materials.provenance import query_request

        query_request(args.get("query"), args.get("limit", 5),
                      args.get("conditions"), args.get("continuation"))
        domain = _public_domain(args)
        selected, routed_domain = select_engine(domain=domain)
        payload = dict(args, _session_id=session, _session_verified=True)
        if routed_domain is not None:
            payload["domain"] = routed_domain
        connection = ensure_service() if selected is None else ensure_service(selected)
        return rpc(connection, "query", payload, timeout=None)
    if name == "knowledge_distill":
        return _knowledge_distill_call(args, session, ensure_service, rpc)
    if name in {"knowledge_explain", "knowledge_feedback"}:
        # The returned mindie://DOMAIN/... URI is the routing authority; there is
        # no caller-supplied path, map or separate domain parameter in the schema.
        return _read_or_feedback_call(name, args, session, ensure_service, rpc)
    # Identity comes from the consumed native toolUseId bind. Public reads
    # use the native tool binding and do not require a capture lease.
    method = name.removeprefix("knowledge_")
    # Core's ready fast path verifies exact configuration, retirement and
    # worker health before reusing the authenticated connection.
    connection = ensure_service()
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
                serverInfo={"name": f"mindie-cc-{surface}", "version": installed_version()},
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
            any(key in args for key in (
                "session_id", "sessionId", "thread_id",
                "_session_id", "_activation", "_session_verified",
            ))
            or any(isinstance(key, str) and key.startswith("_") for key in args)
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
            is_error = False
            if local == "knowledge_distill":
                from mindie_knowledge.graded.distill import result_view
                payload, is_error = result_view(payload)
            result = dict(
                content=[dict(type="text", text=canonical(payload))],
                structuredContent=payload,
                isError=is_error,
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
