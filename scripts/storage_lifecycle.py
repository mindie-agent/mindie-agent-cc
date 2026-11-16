"""Bounded storage lifecycle: legacy reclamation, session pruning, release entries.

[Role]
Owns the adapter-side storage lifecycle that `mindie_knowledge.storage` does not
already know about: refreshing `$CLAUDE_PLUGIN_DATA/releases/<version>/entry.py`
so a merged runtime is not pinned to a retired baseline venv, retiring superseded
release directories while keeping the current and one rollback generation,
pruning expired `mcp-binds.sqlite3` rows and dead `remote/<session>` state, and
holding a shared runtime lease for a resident MCP process.

[Invariants]
- Cleanup never changes a completed business result: callers fold the returned
  accounting into `cleanup_warning`/`deferred_in_use` instead of raising.
- Every directory deletion is delegated to
  `mindie_knowledge.storage.retire_directory`, so POSIX locks stay inside the
  target while Windows never holds a handle inside a directory it renames.
- A pending bind (`bound`/`claimed`) and any persisted `submission_state` of
  `uncertain`/`unknown` is never removed, regardless of age.
- The active generation, one rollback generation, live leases/processes and
  cross-plugin borrowed roots are protected.

[Timeouts & Locks]
- Every entry point takes an absolute monotonic deadline. Expiry is reported as
  a partial cleanup, never a business failure. Lease acquisition is
  non-blocking and the kernel releases it on process exit.

[Side-Effect Policy]
- Writes only inside MindIE-owned state: the maintenance marker, the refreshed
  release entry, and disposable generations/session directories. Never writes
  `~/.claude`, consent, community or knowledge content.
"""

from __future__ import annotations

from contextlib import contextmanager
import sys
import time

from storage_common import (
    MAINTENANCE_INTERVAL_SECONDS,
    MARKER_NAME,
    SCHEMA,
    SESSION_RETENTION_SECONDS,
    SWEEP_BUDGET_SECONDS,
    _binding,
    _bounded_deadline,
    _expired,
    _identity,
    _json_safe,
    _recent_marker,
    _remaining,
    _state_dir,
    _warn,
    _write_json_atomic,
    attach_cleanup,
    cleanup_fields,
)
from storage_reclaim import prune_sessions, sweep_installations
from storage_releases import (
    active_version,
    prune_release_entries,
    refresh_release_entry,
    render_entry,
)

@contextmanager
def runtime_leases(binding=None):
    """Hold a shared lease on every generation that owns this process.

    The lease is kernel-released on crash, so a later sweep cannot treat a
    crashed MCP process as a permanent pin. Acquisition failures are ordinary
    liveness facts (an unmanaged interpreter has no owning generation); the
    context never blocks MCP startup.
    """
    leases = []
    try:
        try:
            from mindie_knowledge.storage import acquire_runtime_lease, owning_dir
        except ImportError:
            yield ()
            return
        candidates = [sys.executable]
        python = binding.get("python") if isinstance(binding, dict) else None
        if isinstance(python, str) and python:
            candidates.append(python)
        seen = set()
        for value in candidates:
            owner = owning_dir(value)
            if owner is None or not owner.is_dir():
                continue
            key = _identity(owner)
            if key in seen:
                continue
            seen.add(key)
            try:
                lease = acquire_runtime_lease(owner)
            except TimeoutError:
                raise
            except (OSError, RuntimeError):
                continue
            leases.append(lease)
        yield tuple(leases)
    finally:
        for lease in reversed(leases):
            try:
                lease.release()
            except TimeoutError:
                raise
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def maintenance_tick(binding=None, *, force=False, now=None,
                     budget_seconds=SWEEP_BUDGET_SECONDS,
                     include_sessions=True, include_installations=True,
                     include_releases=True, previous_python=None):
    """One bounded maintenance pass, throttled by an atomic marker.

    Callers run this after a switch or from a background maintenance cycle.
    A partial result is returned for `cleanup_fields`; only programming errors
    escape, so a successful business path is never turned into a failure.
    """
    binding = _binding(binding)
    try:
        state_dir = _state_dir(binding)
    except ValueError as exc:
        return {"schema": SCHEMA, "status": "skipped", "reason": type(exc).__name__}
    marker = state_dir / MARKER_NAME
    if not force and _recent_marker(marker, MAINTENANCE_INTERVAL_SECONDS):
        return {"schema": SCHEMA, "status": "skipped_cooldown"}
    limit = _bounded_deadline(budget_seconds)
    account = {"schema": SCHEMA, "status": "completed", "partial": False,
               "warnings": [], "at": time.time() if now is None else float(now)}
    if include_sessions:
        sessions = prune_sessions(
            binding, budget_seconds=max(0.0, (_remaining(limit) or 0.0) * 0.35),
            deadline=limit, now=now)
        account["sessions"] = _json_safe(sessions)
        account["reclaimed_dirs"] = sessions.get("reclaimed_dirs", 0)
        account["deferred_in_use"] = sessions.get("deferred_in_use", 0)
        if sessions.get("partial"):
            _warn(account, "session prune partial")
    if include_releases and not _expired(limit):
        releases = prune_release_entries(
            binding, budget_seconds=max(0.0, (_remaining(limit) or 0.0) * 0.3),
            deadline=limit)
        account["releases"] = _json_safe(releases)
        account["reclaimed_dirs"] = account.get("reclaimed_dirs", 0) \
            + releases.get("reclaimed_dirs", 0)
        account["deferred_in_use"] = account.get("deferred_in_use", 0) \
            + releases.get("deferred_in_use", 0)
        if releases.get("partial"):
            _warn(account, "release prune partial")
    if include_installations and not _expired(limit):
        installations = sweep_installations(
            binding, previous_python=previous_python,
            budget_seconds=max(0.0, _remaining(limit) or 0.0), deadline=limit,
            force=force)
        account["installations"] = _json_safe(installations)
        account["reclaimed_dirs"] = account.get("reclaimed_dirs", 0) \
            + installations.get("reclaimed_dirs", 0)
        account["deferred_in_use"] = account.get("deferred_in_use", 0) \
            + installations.get("deferred_dirs", 0)
        if installations.get("partial"):
            _warn(account, "installation sweep partial")
    if account["partial"]:
        account["status"] = "partial"
    try:
        _write_json_atomic(marker, {
            "schema": SCHEMA, "at": account["at"], "status": account["status"],
            "reclaimed_dirs": account.get("reclaimed_dirs", 0),
        })
    except TimeoutError:
        _warn(account, "marker write budget exhausted")
    except OSError as exc:
        _warn(account, "marker: " + type(exc).__name__)
    return account


def silent_tick(binding=None, *, budget_seconds=1.5, previous_python=None,
                force=False):
    """Maintenance result for business paths that must keep their own status."""
    try:
        return maintenance_tick(binding, budget_seconds=budget_seconds,
                                previous_python=previous_python, force=force)
    except TimeoutError:
        return {"schema": SCHEMA, "status": "partial", "partial": True,
                "warnings": ["budget exhausted"]}
    except (OSError, ValueError, RuntimeError) as exc:
        return {"schema": SCHEMA, "status": "deferred",
                "warnings": [type(exc).__name__]}
    except Exception as exc:  # noqa: BLE001 - cleanup boundary never fails a build
        return {"schema": SCHEMA, "status": "deferred",
                "warnings": [type(exc).__name__]}


def post_switch(binding=None, *, previous_python=None,
                budget_seconds=SWEEP_BUDGET_SECONDS):
    """Refresh the active release entry, then run one bounded maintenance pass."""
    binding = _binding(binding)
    limit = _bounded_deadline(budget_seconds)
    account = {"schema": SCHEMA, "status": "completed", "partial": False,
               "warnings": []}
    try:
        entry = refresh_release_entry(binding, previous_python=previous_python,
                                      deadline=limit)
        account["release_entry"] = _json_safe(entry)
        if entry.get("warnings"):
            _warn(account, "release entry: " + str(entry["warnings"][0]))
        tick = maintenance_tick(
            binding, force=True, budget_seconds=max(0.0, _remaining(limit) or 0.0),
            previous_python=previous_python)
        account["maintenance"] = _json_safe(tick)
        account["reclaimed_dirs"] = tick.get("reclaimed_dirs", 0)
        account["deferred_in_use"] = tick.get("deferred_in_use", 0)
        if tick.get("partial"):
            _warn(account, "maintenance partial")
    except TimeoutError:
        account["status"] = "partial"
        account["partial"] = True
        account["warnings"].append("budget exhausted")
    except (OSError, ValueError, RuntimeError) as exc:
        account["status"] = "deferred"
        account["warnings"].append(type(exc).__name__)
    except Exception as exc:  # noqa: BLE001 - post-switch cleanup stays additive
        account["status"] = "deferred"
        account["warnings"].append(type(exc).__name__)
    return account


def pre_build_reclaim(binding=None, *, budget_seconds=1.5, previous_python=None):
    """Reclaim disposable generations before a large runtime build starts."""
    return silent_tick(binding, budget_seconds=budget_seconds,
                       previous_python=previous_python, force=True)


__all__ = [
    "MAINTENANCE_INTERVAL_SECONDS",
    "SESSION_RETENTION_SECONDS",
    "active_version",
    "attach_cleanup",
    "cleanup_fields",
    "maintenance_tick",
    "post_switch",
    "pre_build_reclaim",
    "prune_release_entries",
    "prune_sessions",
    "refresh_release_entry",
    "render_entry",
    "runtime_leases",
    "silent_tick",
    "sweep_installations",
]
