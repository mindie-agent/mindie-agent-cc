"""Bounded reclamation of legacy generations and expired session state.

[Role]
Sweeps legacy `runtimes/cc`, `adapter-state/installations` and merged
`component-runtimes` generations through `mindie_knowledge.storage`, then
prunes terminal `mcp-binds.sqlite3` rows and dead `remote/<session>` roots.

[Invariants]
- Cleanup is additive accounting only: the caller's completed result and status
  never change because a tree is busy or a budget expired.
- A pending bind (`bound`/`claimed`), a held lease/lock, a live process, or a
  persisted `submission_state` of `uncertain`/`unknown` is never removed.
- Every directory deletion goes through `retire_directory`, so Windows never
  holds an entry handle across its NTFS rename and a failed rename leaves the
  source tree byte-for-byte intact (no half-delete retries).

[Timeouts & Locks]
- One absolute monotonic deadline covers enumeration, process inspection and
  each retirement attempt. Walks are iterative and capped; SQLite uses WAL, a
  bounded busy timeout and an immediate transaction. Expiry reports `partial`.

[Side-Effect Policy]
- Mutates only disposable generations and dead session state under the caller's
  absolute `state_dir`; never consent, community or knowledge content.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import time

from storage_common import (
    MAX_JSON_BYTES,
    MAX_SESSION_ENTRIES,
    MAX_WARNINGS,
    MAX_WALK_FILES,
    PENDING_BIND_STATUSES,
    RESOLVED_SUBMISSION_STATES,
    SCHEMA,
    SESSION_BUDGET_SECONDS,
    SESSION_RETENTION_SECONDS,
    SWEEP_BUDGET_SECONDS,
    TERMINAL_BIND_STATUSES,
    _TRASH_PREFIX,
    _binding,
    _bounded_deadline,
    _expired,
    _json_safe,
    _remaining,
    _roots,
    _state_dir,
    _warn,
)

# --------------------------------------------------------------------------- #
# Legacy installations and component runtimes
# --------------------------------------------------------------------------- #

def _keep_paths(binding, previous_python=None):
    values = []
    for candidate in (sys.executable, binding.get("python"), previous_python):
        if isinstance(candidate, str) and candidate:
            values.append(candidate)
    keep = []
    try:
        from mindie_knowledge.storage import owning_dir

        for value in values:
            owner = owning_dir(value)
            if owner is not None:
                keep.append(owner)
    except ImportError:
        pass
    for value in values:
        path = Path(value).resolve()
        keep.extend((path, path.parent, path.parent.parent))
    return keep


def _live_snapshot(deadline):
    try:
        from mindie_knowledge.storage import live_process_prefixes
    except ImportError:
        return None, False
    remaining = _remaining(deadline)
    try:
        return live_process_prefixes(timeout=min(1.5, max(0.1, remaining or 1.5)))
    except TimeoutError:
        return None, False
    except (OSError, RuntimeError):
        return None, False


def sweep_installations(binding=None, *, previous_python=None,
                        budget_seconds=SWEEP_BUDGET_SECONDS, deadline=None,
                        force=False, data_home=None, config_home=None):
    """Reclaim legacy generations with the shared storage authority.

    ``sweep_legacy_installations`` handles ``runtimes/cc``,
    ``adapter-state/installations`` and cross-harness baggage;
    ``prune_component_runtimes`` ranks the merged component generations. The
    current interpreter, the caller's binding and one previous interpreter are
    explicit keep pins on top of the knowledge-owned global protection.
    """
    account = {"schema": SCHEMA, "status": "completed", "partial": False,
               "warnings": [], "reclaimed_dirs": 0, "deferred_dirs": 0,
               "reclaimed_trash": 0}
    binding = _binding(binding)
    try:
        state_dir, data_home, config_home = _roots(
            binding, data_home=data_home, config_home=config_home)
        from mindie_knowledge.storage import (
            prune_component_runtimes, sweep_legacy_installations,
        )
    except ImportError as exc:
        account.update(status="unavailable", reason=type(exc).__name__)
        return account
    except TimeoutError:
        raise
    except (OSError, ValueError) as exc:
        account.update(status="skipped", reason=type(exc).__name__)
        return account
    limit = _bounded_deadline(budget_seconds, deadline)
    live_paths, complete = _live_snapshot(limit)
    try:
        legacy = sweep_legacy_installations(
            config_home=str(config_home), data_home=str(data_home),
            budget_seconds=max(0.0, _remaining(limit) or 0.0),
            force=force, deadline=limit)
    except TimeoutError:
        _warn(account, "legacy sweep budget exhausted")
        legacy = {}
    except (OSError, ValueError, RuntimeError) as exc:
        _warn(account, "legacy sweep: " + type(exc).__name__)
        legacy = {}
    account["legacy"] = _json_safe(legacy)
    for name in ("reclaimed_dirs", "deferred_dirs", "kept_dirs", "reclaimed_trash"):
        value = legacy.get(name)
        if type(value) is int:
            account[name] = account.get(name, 0) + value
    if legacy.get("partial") or legacy.get("status") in {"partial", "inspection_incomplete"}:
        _warn(account, "legacy sweep partial")
    component_root = state_dir / "component-runtimes"
    if component_root.is_dir() and not _expired(limit):
        try:
            pruned = prune_component_runtimes(
                component_root,
                keep_paths=_keep_paths(binding, previous_python),
                keep_recent_verified=2,
                budget_seconds=max(0.0, _remaining(limit) or 0.0),
                live_paths=live_paths, inspection_complete=complete,
                include_global_roots=True, deadline=limit)
        except TimeoutError:
            _warn(account, "component runtime prune budget exhausted")
            pruned = {}
        except (OSError, ValueError, RuntimeError) as exc:
            _warn(account, "component runtime prune: " + type(exc).__name__)
            pruned = {}
        account["component_runtimes"] = _json_safe(pruned)
        for name in ("reclaimed_dirs", "deferred_dirs", "kept_dirs", "reclaimed_trash"):
            value = pruned.get(name)
            if type(value) is int:
                account[name] = account.get(name, 0) + value
        if pruned.get("partial") or pruned.get("status") in {"partial", "inspection_incomplete"}:
            _warn(account, "component runtime prune partial")
    if account["partial"]:
        account["status"] = "partial"
    return account


# --------------------------------------------------------------------------- #
# Expired session state
# --------------------------------------------------------------------------- #

def _probe_lock(path):
    """True when a lock file is currently held, or cannot be inspected."""
    try:
        descriptor = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return False
    except TimeoutError:
        raise
    except OSError:
        return True
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                return False
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except TimeoutError:
                raise
            except OSError:
                return True
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            except TimeoutError:
                raise
            except OSError:
                pass
            return False
        import fcntl

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except TimeoutError:
            raise
        except OSError:
            return True
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except TimeoutError:
            raise
        except OSError:
            pass
        return False
    finally:
        os.close(descriptor)


def _is_link(entry):
    """Symlink or NTFS junction; never traverse it for cleanup inspection."""
    if entry.is_symlink():
        return True
    try:
        from mindie_knowledge.filesystem import is_junction
    except ImportError:
        return False
    return is_junction(entry)


# Terminal remote-dev job states plus the legacy spellings older workers wrote.
_TERMINAL_STATES = frozenset({
    "completed", "succeeded", "failed", "cancelled", "canceled", "stopped",
    "killed", "exited", "aborted", "timed_out", "timeout", "error", "absent",
    "done",
})
_JOB_ID_KEYS = frozenset({"job_id", "jobId"})
# A persisted job record pairs its id with job facts; `authorization` only
# carries `token` + `job_id` and must never be mistaken for a pre-reply record.
_JOB_RECORD_KEYS = frozenset({
    "remote_dir", "command_preview", "command", "cancel_token", "cancelToken",
    "remote_pid",
})
_JOB_SCHEMA = "remote-dev.job.v1"


def _walk_bounded(root, deadline, *, limit=MAX_WALK_FILES, incomplete=None):
    """Yield paths below ``root`` without recursion and with a hard cap."""
    root = Path(root)
    stack = [root]
    seen = 0
    while stack:
        if _expired(deadline):
            if incomplete is not None:
                incomplete[0] = True
            return
        directory = stack.pop()
        try:
            with os.scandir(directory) as iterator:
                for item in iterator:
                    if _expired(deadline):
                        if incomplete is not None:
                            incomplete[0] = True
                        return
                    seen += 1
                    if seen > limit:
                        if incomplete is not None:
                            incomplete[0] = True
                        return
                    entry = directory / item.name
                    yield entry
                    try:
                        if item.is_dir(follow_symlinks=False) and not _is_link(entry):
                            stack.append(entry)
                    except TimeoutError:
                        raise
                    except OSError:
                        if incomplete is not None:
                            incomplete[0] = True
                        continue
        except TimeoutError:
            raise
        except OSError:
            if incomplete is not None:
                incomplete[0] = True
            continue


def _newest_mtime(root, deadline):
    try:
        newest = os.stat(root).st_mtime
    except TimeoutError:
        raise
    except OSError:
        return float("inf")
    incomplete = [False]
    for entry in _walk_bounded(root, deadline, limit=MAX_WALK_FILES, incomplete=incomplete):
        try:
            info = os.stat(entry, follow_symlinks=False)
        except TimeoutError:
            raise
        except OSError:
            return float("inf")
        newest = max(newest, info.st_mtime)
    return float("inf") if incomplete[0] else newest


def _active_locks(session_dir, deadline):
    try:
        from mindie_knowledge.storage import is_lease_held

        if is_lease_held(session_dir):
            return True
    except TimeoutError:
        raise
    except (ImportError, OSError, RuntimeError):
        return True
    incomplete = [False]
    for entry in _walk_bounded(session_dir, deadline, limit=MAX_WALK_FILES, incomplete=incomplete):
        if entry.name.endswith(".lock") and _probe_lock(entry):
            return True
    return incomplete[0]


def _parse_json_records(entry, raw):
    text = raw.decode("utf-8")
    if entry.suffix.lower() in {".jsonl", ".ndjson"}:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return [json.loads(text)]


def _unresolved_write_state(session_dir, deadline):
    incomplete = [False]
    for entry in _walk_bounded(session_dir, deadline, incomplete=incomplete):
        if entry.suffix.lower() not in {".json", ".jsonl", ".ndjson"}:
            continue
        try:
            info = os.stat(entry)
        except TimeoutError:
            raise
        except OSError:
            return True
        if info.st_size > MAX_JSON_BYTES:
            return True
        try:
            raw = entry.read_bytes()
        except TimeoutError:
            raise
        except OSError:
            return True
        try:
            records = _parse_json_records(entry, raw)
        except (UnicodeDecodeError, ValueError):
            return True
        if any(_contains_unresolved(record) for record in records):
            return True
    return incomplete[0]


def _is_job_record(value):
    """A persisted `remote-dev` job record, not its nested authorization block."""
    schema = value.get("schema_version")
    if isinstance(schema, str) and schema.casefold() == _JOB_SCHEMA:
        return True
    keys = value.keys()
    return bool(_JOB_ID_KEYS & keys) and bool(_JOB_RECORD_KEYS & keys)


def _contains_unresolved(value, depth=0):
    if depth > 8:
        return True
    if isinstance(value, dict):
        if ("state" not in value and "status" not in value
                and "submission_state" not in value and "submissionState" not in value
                and _is_job_record(value)):
            # Saved before submission: a lost reply keeps its recovery id.
            return True
        for key, item in value.items():
            if key in {"submission_state", "submissionState"}:
                # `acknowledged`/`not_sent`/`not_executed` are resolved replies;
                # pending, in-flight, unknown and non-string values still defer.
                if not isinstance(item, str) \
                        or item.casefold() not in RESOLVED_SUBMISSION_STATES:
                    return True
            if key == "state":
                if not isinstance(item, str) or item.casefold() not in _TERMINAL_STATES:
                    return True
            if _contains_unresolved(item, depth + 1):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_unresolved(item, depth + 1) for item in value)
    return False


def _prune_binds(state_dir, cutoff, deadline):
    account = {"status": "absent", "removed": 0, "protected_sessions": set()}
    path = state_dir / "mcp-binds.sqlite3"
    try:
        if not os.path.lexists(path):
            return account
        if not path.is_file():
            account.update(status="deferred", reason="NonRegularBindStore")
            return account
    except TimeoutError:
        raise
    except OSError as exc:
        account.update(status="deferred", reason=type(exc).__name__)
        return account
    remaining = _remaining(deadline)
    busy = max(0.05, min(2.0, remaining if remaining is not None else 2.0))
    connection = None
    try:
        connection = sqlite3.connect(str(path), timeout=busy, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=" + str(int(busy * 1000)))
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "binds" not in tables:
            account["status"] = "empty"
            return account
        rows = connection.execute(
            "SELECT DISTINCT session FROM binds WHERE status IN (?,?) OR updated >= ?",
            (*PENDING_BIND_STATUSES, cutoff)).fetchall()
        account["protected_sessions"] = {row[0] for row in rows
                                         if isinstance(row[0], str)}
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            "DELETE FROM binds WHERE updated < ? AND status IN (?,?,?)",
            (cutoff, *TERMINAL_BIND_STATUSES))
        account["removed"] = max(0, cursor.rowcount)
        connection.execute("COMMIT")
        account["status"] = "completed"
    except sqlite3.Error as exc:
        try:
            if connection is not None:
                connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        account.update(status="deferred", reason=type(exc).__name__)
    except TimeoutError:
        raise
    except OSError as exc:
        account.update(status="deferred", reason=type(exc).__name__)
    finally:
        if connection is not None:
            connection.close()
    return account


def _prune_remote(state_dir, cutoff, deadline, protected_sessions):
    account = {"status": "completed", "removed": 0, "deferred": 0,
               "warnings": [], "partial": False}
    remote = state_dir / "remote"
    if not remote.is_dir():
        account["status"] = "absent"
        return account
    try:
        from mindie_knowledge.storage import (
            is_path_used_by_live_process, live_process_prefixes, retire_directory,
        )
    except ImportError as exc:
        account.update(status="unavailable", reason=type(exc).__name__)
        return account
    remaining = _remaining(deadline)
    try:
        live_paths, complete = live_process_prefixes(
            timeout=min(1.0, max(0.1, remaining or 1.0)))
    except TimeoutError:
        account.update(status="partial", partial=True)
        return account
    except (OSError, RuntimeError):
        live_paths, complete = None, False
    try:
        entries = sorted(remote.iterdir(), key=lambda item: item.name)
    except TimeoutError:
        raise
    except OSError as exc:
        account.update(status="deferred", reason=type(exc).__name__)
        return account
    inspected = 0
    for entry in entries:
        if _expired(deadline) or inspected >= MAX_SESSION_ENTRIES:
            account["partial"] = True
            account["warnings"].append("budget exhausted")
            break
        if entry.name.startswith(_TRASH_PREFIX) or not entry.is_dir() \
                or _is_link(entry):
            continue
        inspected += 1
        if entry.name in protected_sessions:
            continue
        try:
            if _newest_mtime(entry, deadline) >= cutoff:
                continue
            if not complete:
                # A truncated process table cannot prove a session is dead.
                account["deferred"] += 1
                account["partial"] = True
                if (len(account["warnings"]) < MAX_WARNINGS
                        and "incomplete process snapshot" not in account["warnings"]):
                    account["warnings"].append("incomplete process snapshot")
                continue
            if live_paths is not None and is_path_used_by_live_process(entry, live_paths):
                continue
            if _active_locks(entry, deadline):
                continue
            if _unresolved_write_state(entry, deadline):
                continue
            outcome = retire_directory(
                entry, deadline=deadline,
                recheck=lambda: not _unresolved_write_state(entry, deadline))
        except TimeoutError:
            account["status"] = "partial"
            account["partial"] = True
            account["warnings"].append("budget exhausted")
            break
        except PermissionError:
            account["deferred"] += 1
            continue
        except (OSError, RuntimeError) as exc:
            account["deferred"] += 1
            if len(account["warnings"]) < MAX_WARNINGS:
                account["warnings"].append(type(exc).__name__)
            continue
        if outcome.get("status") in {"retired", "trashed_partial"}:
            account["removed"] += 1
        elif outcome.get("status") == "deferred_in_use":
            account["deferred"] += 1
        if outcome.get("partial"):
            account["partial"] = True
    if account["partial"]:
        account["status"] = "partial"
    return account


def prune_sessions(binding=None, *, retention_seconds=SESSION_RETENTION_SECONDS,
                   budget_seconds=SESSION_BUDGET_SECONDS, deadline=None,
                   now=None):
    """Prune terminal bind rows and dead remote session roots, bounded."""
    binding = _binding(binding)
    account = {"schema": SCHEMA, "status": "completed", "partial": False,
               "warnings": [], "reclaimed_dirs": 0, "deferred_in_use": 0,
               "binds_removed": 0}
    try:
        state_dir = _state_dir(binding)
    except ValueError as exc:
        account.update(status="skipped", reason=type(exc).__name__)
        return account
    limit = _bounded_deadline(budget_seconds, deadline)
    cutoff = (time.time() if now is None else float(now)) - max(0.0, float(retention_seconds))
    try:
        binds = _prune_binds(state_dir, cutoff, limit)
    except TimeoutError:
        _warn(account, "bind prune budget exhausted")
        binds = {"status": "deferred", "reason": "TimeoutError", "protected_sessions": set()}
    except (OSError, ValueError) as exc:
        _warn(account, "bind prune: " + type(exc).__name__)
        binds = {"status": "deferred", "reason": type(exc).__name__, "protected_sessions": set()}
    if binds.get("status") == "deferred":
        _warn(account, "bind prune deferred")
    account["binds"] = {key: value for key, value in binds.items()
                        if key != "protected_sessions"}
    account["binds_removed"] = binds.get("removed", 0) if isinstance(
        binds.get("removed"), int) else 0
    if binds.get("status") not in {"absent", "empty", "completed"}:
        account["remote"] = {"status": "deferred", "reason": "binds_unavailable",
                             "removed": 0, "deferred": 0, "partial": True}
        account["partial"] = True
        account["status"] = "partial"
        return account
    protected = binds.get("protected_sessions") or set()
    try:
        remote = _prune_remote(state_dir, cutoff, limit, protected)
    except TimeoutError:
        _warn(account, "remote session prune budget exhausted")
        remote = {}
    except (OSError, ValueError) as exc:
        _warn(account, "remote session prune: " + type(exc).__name__)
        remote = {}
    account["remote"] = _json_safe(remote)
    account["reclaimed_dirs"] += remote.get("removed", 0) if isinstance(
        remote.get("removed"), int) else 0
    account["deferred_in_use"] += remote.get("deferred", 0) if isinstance(
        remote.get("deferred"), int) else 0
    if remote.get("partial"):
        _warn(account, str(remote.get("warnings") or ["remote partial"]))
    if account["partial"]:
        account["status"] = "partial"
    return account

