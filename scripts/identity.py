"""Native Claude Code identity: hook payload and one-shot toolUseId binds.

Claude 2.1.269 MCP tools/call has no session metadata. PreToolUse supplies
session_id, tool_use_id, tool_name, tool_input. The MCP request carries the
same id in params._meta['claudecode/toolUseId']. Bind that host-owned call
id to the verified task, tool, and argument digest; consume it once.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from paths import state_dir

SESSION_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
CALL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
MAX_HOOK_BYTES = 128 * 1024

COMMANDS = {
    "mindie-agent:init": "init",
    "mindie-agent:status": "status",
    "mindie-agent:deactivate": "deactivate",
    "mindie-agent:sharing-enable": "sharing-enable",
    "mindie-agent:sharing-disable": "sharing-disable",
    "mindie-agent:recover": "recover",
    "mindie-agent:reporting-status": "reporting-status",
    "mindie-agent:reporting-enable": "reporting-enable",
    "mindie-agent:reporting-disable": "reporting-disable",
}

KNOWLEDGE_TOOLS = {
    "mindie_status",
    "knowledge_query",
    "knowledge_explain",
    "knowledge_feedback",
}

# Native Claude 2.1.269 plugin MCP names from `claude mcp list`
# `plugin:mindie-agent:knowledge` -> mcp__plugin_mindie-agent_knowledge__TOOL
NATIVE_TOOL_RE = re.compile(
    r"^mcp__plugin_mindie-agent_(knowledge|remote)__[A-Za-z0-9_.]+$"
)


def require_session(value) -> str:
    if not isinstance(value, str) or not SESSION_RE.fullmatch(value):
        raise ValueError("native session identity is missing or invalid")
    return value


def require_call_id(value) -> str:
    if not isinstance(value, str) or not CALL_RE.fullmatch(value):
        raise ValueError("native tool_use_id is missing or invalid")
    return value


def parse_hook(raw: bytes) -> dict:
    if len(raw) > MAX_HOOK_BYTES:
        raise ValueError("hook input exceeds limit")
    event = json.loads(raw)
    if not isinstance(event, dict):
        raise ValueError("hook payload must be one JSON object")
    return event


def session_from_hook(event: dict) -> str:
    return require_session(event.get("session_id"))


def is_subagent(event: dict) -> bool:
    agent = event.get("agent_id")
    return isinstance(agent, str) and bool(agent.strip())


def require_absolute(value, name) -> str:
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ValueError(f"native {name} is missing or not absolute")
    return value


def slash_command(event: dict) -> str:
    if event.get("expansion_type") != "slash_command":
        raise ValueError("not a slash-command expansion")
    if event.get("command_source") != "plugin":
        raise ValueError("slash command is not from this plugin")
    name = event.get("command_name")
    if name not in COMMANDS:
        raise ValueError("slash command is not a MindIE entry")
    return COMMANDS[name]


def local_tool_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    if "__" in name:
        return name.rsplit("__", 1)[-1]
    return name


def parse_native_tool(name):
    if not isinstance(name, str) or not NATIVE_TOOL_RE.fullmatch(name):
        return None
    rest = name[len("mcp__plugin_mindie-agent_") :]
    surface, _, local = rest.partition("__")
    if surface not in {"knowledge", "remote"} or not local:
        return None
    return surface, local


def canonical_native_name(surface: str, request_name: str) -> str:
    if surface not in {"knowledge", "remote"}:
        raise ValueError("unknown MCP surface")
    if not isinstance(request_name, str) or not request_name:
        raise ValueError("invalid tool name")
    parsed = parse_native_tool(request_name)
    if parsed is not None:
        if parsed[0] != surface:
            raise ValueError("tool surface does not match this MCP server")
        return request_name
    if not re.fullmatch(r"[A-Za-z0-9_.]+", request_name):
        raise ValueError("invalid local tool name")
    return f"mcp__plugin_mindie-agent_{surface}__{request_name}"


def is_mindie_mcp_tool(name) -> bool:
    return parse_native_tool(name) is not None


def is_knowledge_tool(name, surface=None) -> bool:
    parsed = parse_native_tool(name)
    if parsed is not None:
        return parsed[0] == "knowledge" and parsed[1] in KNOWLEDGE_TOOLS
    return surface == "knowledge" and name in KNOWLEDGE_TOOLS


def canonical_args(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def args_digest(arguments) -> str:
    return hashlib.sha256(
        canonical_args(arguments if isinstance(arguments, dict) else {}).encode("utf-8")
    ).hexdigest()


def bind_digest(tool_name, arguments, tool_use_id) -> str:
    payload = dict(
        tool=tool_name,
        arguments=arguments if isinstance(arguments, dict) else {},
        tool_use_id=tool_use_id or "",
    )
    return hashlib.sha256(canonical_args(payload).encode("utf-8")).hexdigest()


def bind_db_path(config=None) -> Path:
    return state_dir(config) / "mcp-binds.sqlite3"


def _connect(path: Path, *, create: bool):
    if not path.exists() and not create:
        raise FileNotFoundError(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=0.4)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return db


def _ensure_binds(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS binds("
        "tool_use_id TEXT PRIMARY KEY, session TEXT NOT NULL, tool TEXT NOT NULL, "
        "local_tool TEXT NOT NULL, args_digest TEXT NOT NULL, call_digest TEXT NOT NULL, "
        "cwd TEXT, transcript TEXT, status TEXT NOT NULL, "
        "created REAL NOT NULL, updated REAL NOT NULL)"
    )


def publish_bind(
    session_id: str,
    tool_use_id: str,
    tool_name: str,
    arguments,
    *,
    cwd=None,
    transcript_path=None,
    config=None,
):
    """PreToolUse: bind native tool_use_id to exact tool + arguments + task."""
    session_id = require_session(session_id)
    tool_use_id = require_call_id(tool_use_id)
    if parse_native_tool(tool_name) is None:
        raise ValueError("tool is not this plugin's MCP tool")
    if not isinstance(arguments, dict):
        raise ValueError("tool_input must be an object")
    digest = bind_digest(tool_name, arguments, tool_use_id)
    adigest = args_digest(arguments)
    local = local_tool_name(tool_name)
    now = time.time()
    db = _connect(bind_db_path(config), create=True)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            _ensure_binds(db)
            row = db.execute(
                "SELECT session, tool, args_digest, call_digest, status "
                "FROM binds WHERE tool_use_id=?",
                (tool_use_id,),
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO binds VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        tool_use_id,
                        session_id,
                        tool_name,
                        local,
                        adigest,
                        digest,
                        cwd if isinstance(cwd, str) else None,
                        transcript_path if isinstance(transcript_path, str) else None,
                        "bound",
                        now,
                        now,
                    ),
                )
                return "bound"
            other, tool, stored_args, stored_digest, status = row
            if parse_native_tool(tool_name) is None:
                raise ValueError("tool is not this plugin's MCP tool")
            same = (
                other == session_id
                and tool == tool_name
                and stored_args == adigest
                and stored_digest == digest
            )
            if not same:
                db.execute(
                    "UPDATE binds SET status='ambiguous', updated=? WHERE tool_use_id=?",
                    (now, tool_use_id),
                )
                return "ambiguous"
            return status
    finally:
        db.close()


def claim_bind(tool_use_id: str, tool_name: str, arguments, *, config=None) -> dict:
    """MCP: consume a matching PreToolUse bind. Persist the attempt first."""
    tool_use_id = require_call_id(tool_use_id)
    if parse_native_tool(tool_name) is None:
        raise ValueError("tool is not this plugin's MCP tool")
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    now = time.time()
    path = bind_db_path(config)
    if not path.is_file():
        raise ValueError(
            "no native PreToolUse bind exists for this toolUseId; "
            "the host did not authorize this call"
        )
    adigest = args_digest(arguments)
    db = _connect(path, create=False)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            _ensure_binds(db)
            row = db.execute(
                "SELECT session, tool, local_tool, args_digest, status, created, cwd, transcript "
                "FROM binds WHERE tool_use_id=?",
                (tool_use_id,),
            ).fetchone()
            if row is None:
                raise ValueError("toolUseId was not bound by a native PreToolUse hook")
            session, tool, bound_local, stored_args, status, created, cwd, transcript = row
            if status == "ambiguous":
                raise ValueError("toolUseId is bound to contradictory native calls")
            if status in {"claimed", "succeeded", "failed"}:
                raise ValueError("toolUseId has already been used; replay rejected")
            if status != "bound":
                raise ValueError("toolUseId is not authorized")
            if tool != tool_name or stored_args != adigest:
                raise ValueError("toolUseId does not match this tool and arguments")
            db.execute(
                "UPDATE binds SET status='claimed', updated=? WHERE tool_use_id=?",
                (now, tool_use_id),
            )
            return dict(
                session=session,
                cwd=cwd,
                transcript_path=transcript,
                tool=tool,
                local_tool=bound_local,
            )
    finally:
        db.close()


def finish_bind(tool_use_id: str, succeeded: bool, *, config=None) -> None:
    try:
        tool_use_id = require_call_id(tool_use_id)
    except ValueError:
        return
    path = bind_db_path(config)
    if not path.is_file():
        return
    db = _connect(path, create=False)
    try:
        with db:
            db.execute(
                "UPDATE binds SET status=?, updated=? WHERE tool_use_id=? AND status='claimed'",
                ("succeeded" if succeeded else "failed", time.time(), tool_use_id),
            )
    except sqlite3.Error:
        pass
    finally:
        db.close()


def meta_tool_use_id(params) -> str:
    if not isinstance(params, dict):
        raise ValueError("MCP params are missing")
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        raise ValueError("MCP request lacks native toolUseId metadata")
    value = meta.get("claudecode/toolUseId")
    return require_call_id(value)
