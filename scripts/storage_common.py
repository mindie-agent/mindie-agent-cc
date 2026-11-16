"""Shared bounded-storage helpers, constants and truthful cleanup accounting.

[Role]
Provides the deadline arithmetic, MindIE path derivation, JSON-safe reporting
and additive `cleanup_warning` folding used by every lifecycle part.

[Invariants]
- A cleanup warning never changes a completed business result.
- Paths are derived only from one absolute installation binding; a scoped scan
  never falls back to an unrelated user root.

[Timeouts & Locks]
- Pure arithmetic plus atomic temp-file replacement; no lock is acquired here.

[Side-Effect Policy]
- Reads configuration paths and writes only the caller-named JSON marker.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import time

SCHEMA = "mindie-storage-lifecycle/1"
MARKER_NAME = "storage-maintenance.json"
SESSION_RETENTION_SECONDS = 7 * 24 * 60 * 60.0
MAINTENANCE_INTERVAL_SECONDS = 6 * 60 * 60.0
SWEEP_BUDGET_SECONDS = 3.0
SESSION_BUDGET_SECONDS = 1.0
RELEASE_BUDGET_SECONDS = 1.5
MAX_SESSION_ENTRIES = 12
MAX_WALK_FILES = 400
MAX_JSON_BYTES = 512 * 1024
MAX_WARNINGS = 6
TERMINAL_BIND_STATUSES = ("succeeded", "failed", "ambiguous")
PENDING_BIND_STATUSES = ("bound", "claimed")
UNRESOLVED_SUBMISSION_STATES = frozenset({"uncertain", "unknown"})
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?\Z")
_TRASH_PREFIX = ".trash-"


def _remaining(deadline):
    return None if deadline is None else max(0.0, deadline - time.monotonic())


def _expired(deadline):
    return deadline is not None and time.monotonic() >= deadline


def _warn(account, message):
    warnings = account.setdefault("warnings", [])
    if len(warnings) < MAX_WARNINGS:
        warnings.append(str(message)[:160])
    account["partial"] = True


def _bounded_deadline(budget_seconds, deadline=None):
    limit = time.monotonic() + max(0.0, float(budget_seconds))
    return limit if deadline is None else min(limit, deadline)


def _identity(path):
    try:
        from mindie_knowledge.filesystem import path_identity

        return path_identity(path)
    except TimeoutError:
        raise
    except (ImportError, OSError, RuntimeError):
        return os.path.normcase(os.path.abspath(os.fspath(path)))


def _binding(value=None):
    if value is not None:
        return dict(value)
    from paths import load_adapter_config, state_dir

    config = load_adapter_config()
    return dict(config, state_dir=str(state_dir(config)))


def _state_dir(binding):
    value = binding.get("state_dir")
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ValueError("storage lifecycle requires an absolute state_dir")
    return Path(value)


def _roots(binding, *, data_home=None, config_home=None):
    """Derive the MindIE data/config homes from one installation binding.

    ``state_dir`` is ``<data-home>/cc/adapter-state``; the adapter profile lives
    at ``<config-home>/cc.json``. Both are caller-supplied absolute paths, so a
    scoped sweep never scans an unrelated user root. Explicit homes win for a
    caller that owns a non-default layout.
    """
    state_dir = _state_dir(binding)
    if data_home is None and (
            state_dir.name.casefold() != "adapter-state"
            or state_dir.parent.name.casefold() != "cc"):
        # A custom --root is not the shared MindIE data home; refuse to guess a
        # parent scan root. An explicit data_home still opts in.
        raise ValueError("storage lifecycle requires the shared MindIE data home")
    data_home = Path(data_home) if data_home is not None else state_dir.parent.parent
    if config_home is None:
        base_config = binding.get("base_config")
        config_home = (Path(base_config).parent if isinstance(base_config, str)
                       and os.path.isabs(base_config) else state_dir.parent)
    else:
        config_home = Path(config_home)
    if data_home.parent == data_home:
        raise ValueError("storage lifecycle refuses a filesystem-root data home")
    return state_dir, data_home, config_home


def _json_safe(value, depth=0):
    if depth > 4:
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value if not isinstance(value, str) or len(value) <= 160 else value[:160]
    if isinstance(value, dict):
        return {str(key)[:40]: _json_safe(item, depth + 1)
                for key, item in list(value.items())[:12]}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth + 1) for item in list(value)[:8]]
    return type(value).__name__


def cleanup_fields(report, label="storage cleanup deferred; the main result is unchanged"):
    """Fold accounting into additive fields; never flip a succeeded result."""
    if not isinstance(report, dict) or not report:
        return {}
    noteworthy = (report.get("status") in {"partial", "inspection_incomplete"}
                  or report.get("partial") or report.get("warnings")
                  or report.get("deferred_dirs") or report.get("deferred_in_use"))
    if not noteworthy:
        return {}
    return {"cleanup_warning": label, "cleanup": _json_safe(report)}


def attach_cleanup(result, report, label=None):
    if isinstance(result, dict):
        fields = cleanup_fields(report) if label is None else cleanup_fields(report, label)
        result.update(fields)
    return result


def _write_json_atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(value, sort_keys=True, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _recent_marker(path, interval):
    value = _read_json(path)
    stamp = value.get("at") if isinstance(value, dict) else None
    return (isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
            and 0 <= time.time() - stamp < interval)


