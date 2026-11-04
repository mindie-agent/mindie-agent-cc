"""Native Claude Code identity: hook payload and one-shot toolUseId binds.

[Role]
Validates Claude Code hook payloads (`session_id`, `tool_use_id`, slash
commands) and manages the SQLite one-shot `claudecode/toolUseId` bind store
connecting `PreToolUse` to `mcp_server.py`.

[Invariants]
- Claude Code MCP `tools/call` carries `params._meta['claudecode/toolUseId']`;
  `PreToolUse` binds that ID to `(session_id, tool_name, input_sha256)` and
  `mcp_server.py` consumes it at most once within `BIND_TTL_SECONDS`.
- Never trusts session identity passed inside model-controlled tool arguments.
- Bind-store OSErrors keep errno and the fixed label `mcp-binds.sqlite3`.
  Absolute locations are not copied into the exception text.

[Timeouts & Locks]
- Uses SQLite WAL mode with `busy_timeout=5000` (`timeout=5.0`) and `BEGIN IMMEDIATE`
  transactions for atomic bind claim/finish.

[Side-Effect Policy]
- Mutates only `state_dir() / "mcp-binds.sqlite3"`.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from pathlib import Path

from paths import state_dir

SESSION_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
CALL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")

COMMANDS = {
    # The single user entry is the mindie-agent skill itself; Claude Code may
    # present a plugin skill bare or namespaced — both map to the entry op.
    "mindie-agent": "init",
    "mindie-agent:mindie-agent": "init",
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
    "knowledge_distill",
    "knowledge_sync",
}

# Native Claude 2.1.269 plugin MCP names from `claude mcp list`
# `plugin:mindie-agent:knowledge` -> mcp__plugin_mindie-agent_knowledge__TOOL
NATIVE_TOOL_RE = re.compile(
    r"^mcp__plugin_mindie-agent_(knowledge|remote|components)__[A-Za-z0-9_.]+$"
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
    if surface not in {"knowledge", "remote", "components"} or not local:
        return None
    return surface, local


def canonical_native_name(surface: str, request_name: str) -> str:
    if surface not in {"knowledge", "remote", "components"}:
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


_BIND_DB_NAME = "mcp-binds.sqlite3"
_SAFE_LABEL = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,80}\Z")


def _known_errnos(*names: str) -> frozenset:
    values = []
    for name in names:
        value = getattr(errno, name, None)
        if type(value) is int and value > 0:
            values.append(value)
    return frozenset(values)


_DISK_ERRNOS = _known_errnos("ENOSPC", "EROFS", "EIO", "EDQUOT")
_PATH_ERRNOS = _known_errnos("ENOENT", "ENOTDIR", "EISDIR", "ENAMETOOLONG", "ELOOP", "EEXIST")
_PERM_ERRNOS = _known_errnos("EACCES", "EPERM")
_MODE_UNSUPPORTED = _known_errnos("ENOTSUP", "EOPNOTSUPP", "EINVAL")


def _safe_store_label(path: Path) -> str:
    name = path.name
    if name == _BIND_DB_NAME and _SAFE_LABEL.fullmatch(name):
        return name
    return "bind-store"


def _storage_kind(exc) -> str:
    if isinstance(exc, TimeoutError) or not isinstance(exc, OSError):
        return "other"
    if isinstance(exc, PermissionError):
        return "permission"
    if isinstance(exc, (FileNotFoundError, NotADirectoryError, IsADirectoryError, FileExistsError)):
        return "path"
    number = exc.errno if type(exc.errno) is int else None
    if number in _DISK_ERRNOS:
        return "disk"
    if number in _PATH_ERRNOS:
        return "path"
    if number in _PERM_ERRNOS:
        return "permission"
    return "io"


def _annotate_storage(path: Path, exc: OSError) -> OSError:
    """Rebuild the OSError so its text cannot carry an absolute location."""
    number = exc.errno if type(exc.errno) is int else None
    label = _safe_store_label(path)
    detail = exc.strerror if isinstance(exc.strerror, str) else ""
    if not detail or any(mark in detail for mark in ("/", "\\", "\n")):
        detail = "bind store unavailable"
    if number is None:
        cloned = type(exc)(detail)
    else:
        cloned = type(exc)(number, detail, label)
    cloned.diagnostic_path = label
    return cloned


def _raise_storage(path: Path, exc: OSError) -> None:
    if isinstance(exc, TimeoutError):
        raise exc
    raised = _annotate_storage(path, exc)
    raise raised.with_traceback(exc.__traceback__) from None


def bind_storage_facts(exc) -> dict:
    """Errno and safe path label for a bind-store OSError. Empty for other errors."""
    if isinstance(exc, TimeoutError) or not isinstance(exc, OSError):
        return {}
    facts = {}
    number = exc.errno
    if type(number) is int:
        facts["errno"] = number
    label = getattr(exc, "diagnostic_path", None)
    if isinstance(label, str) and _SAFE_LABEL.fullmatch(label):
        facts["path"] = label
    return facts


def _probe_store(path: Path):
    """Stat the bind database. A symlink loop is not reported as a missing file."""
    try:
        return path.stat()
    except TimeoutError:
        raise
    except FileNotFoundError:
        return None
    except OSError as exc:
        _raise_storage(path, exc)


def _restrict_mode(path: Path) -> None:
    """Owner-only mode. Disk and path failures stay visible; mode metadata may not."""
    try:
        os.chmod(path, 0o600)
    except TimeoutError:
        raise
    except OSError as exc:
        # A filesystem that rejects chmod can still store the receipt. Disk and
        # path errors mean the write itself is not trustworthy.
        kind = _storage_kind(exc)
        if kind == "permission" or (type(exc.errno) is int and exc.errno in _MODE_UNSUPPORTED):
            return
        _raise_storage(path, exc)


def _connect(path: Path, *, create: bool):
    info = _probe_store(path)
    if info is None and not create:
        _raise_storage(
            path, FileNotFoundError(errno.ENOENT, "bind store is missing", _safe_store_label(path)),
        )
    if info is not None and not stat.S_ISREG(info.st_mode):
        number = errno.EISDIR if stat.S_ISDIR(info.st_mode) else errno.EINVAL
        _raise_storage(path, OSError(number, "bind store is not a regular file", _safe_store_label(path)))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except TimeoutError:
        raise
    except OSError as exc:
        _raise_storage(path, exc)
    db = None
    try:
        db = sqlite3.connect(path, timeout=5.0)
        db.execute("PRAGMA busy_timeout=5000;")
        if create:
            db.execute("PRAGMA journal_mode=WAL;")
    except TimeoutError:
        if db is not None:
            db.close()
        raise
    except OSError as exc:
        if db is not None:
            db.close()
        _raise_storage(path, exc)
    except sqlite3.Error:
        if db is not None:
            db.close()
        raise
    try:
        _restrict_mode(path)
    except OSError:
        db.close()
        raise
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
    """Persist the claimant's result; storage failures remain visible to MCP."""
    try:
        tool_use_id = require_call_id(tool_use_id)
    except ValueError:
        return
    path = bind_db_path(config)
    db = _connect(path, create=False)
    try:
        with db:
            db.execute(
                "UPDATE binds SET status=?, updated=? WHERE tool_use_id=? AND status='claimed'",
                ("succeeded" if succeeded else "failed", time.time(), tool_use_id),
            )
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
