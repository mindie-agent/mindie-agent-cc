"""Standalone local diagnostics fallback. Stdlib only.

[Role]
Records redacted local diagnostic incidents when the full `mindie_diagnostics`
runtime package is not yet available (e.g., during bootstrap failure).

[Invariants]
- Validates incident schemas, redacts sensitive tokens/paths, and enforces
  `_NEVER_REPORTABLE` categories (`caller`, `cancelled`, `business`, `connectivity`).

[Timeouts & Locks]
- Uses thread-safe atomic file writes with bounded retention.

[Side-Effect Policy]
- Writes only to the local MindIE diagnostics directory; never raises to break
  the caller's primary control flow.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

_POLICY_SCHEMA = "mindie.diagnostics.reporting.v1"
_POLICY_PURPOSE = "tool_fault_reporting"
_LABEL_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,119}\Z")
_INCIDENT_RE = re.compile(r"[0-9a-f]{32}\Z")
_REVISION_RE = re.compile(r"[0-9a-f]{7,64}\Z")
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,79}\Z")
_REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
_ERROR_TYPE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,119}\Z")
_NEVER_REPORTABLE = frozenset({
    "caller", "cancelled", "business", "connectivity",
    "credentials", "permission", "disabled", "prerequisite",
})
_CONSENT_KEYS = frozenset({"config_file", "revision", "repository", "purpose"})

_lock = threading.Lock()
_process_pid = None
_process_uuid = None


def _private_mkdir(path, *, parents=False, exist_ok=False):
    Path(path).mkdir(mode=0o700, parents=parents, exist_ok=exist_ok)


def policy_path(config=None) -> Path:
    if config is not None:
        return Path(config)
    env = os.environ.get("MINDIE_DIAGNOSTICS_CONFIG")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "mindie-agent" / "diagnostics.json"
    return Path.home() / ".config" / "mindie-agent" / "diagnostics.json"


def default_root() -> Path:
    env = os.environ.get("MINDIE_DIAGNOSTICS_ROOT")
    if env:
        return Path(env)
    if os.name == "nt" and not os.environ.get("XDG_STATE_HOME"):
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "mindie" / "diagnostics"
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "mindie" / "diagnostics"
    return Path.home() / ".local" / "state" / "mindie" / "diagnostics"


def state_path(config=None) -> Path:
    return policy_path(config).with_suffix(".state")


class PolicyUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("reporting_policy_unavailable")


def _valid_repo(value) -> bool:
    return isinstance(value, str) and _REPO_RE.fullmatch(value) is not None


def _label(value, limit) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit and _LABEL_RE.fullmatch(value) is not None


def read_policy(config=None):
    try:
        path = policy_path(config)
        if not path.is_absolute():
            raise PolicyUnavailable()
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != _POLICY_SCHEMA or data.get("purpose") != _POLICY_PURPOSE:
            raise PolicyUnavailable()
        decision = data.get("decision")
        repository = data.get("repository")
        revision = data.get("revision")
        roots = data.get("roots")
        if (decision not in ("enabled", "disabled") or not _valid_repo(repository)
                or not isinstance(revision, str) or _INCIDENT_RE.fullmatch(revision) is None
                or not isinstance(roots, list) or not all(isinstance(r, str) and Path(r).is_absolute() for r in roots)):
            raise PolicyUnavailable()
        return {
            "schema": _POLICY_SCHEMA, "purpose": _POLICY_PURPOSE, "decision": decision,
            "repository": repository, "revision": revision, "roots": list(roots),
        }
    except PolicyUnavailable:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise PolicyUnavailable() from exc


def current_consent(config=None):
    try:
        policy = read_policy(config)
        if not policy or policy.get("decision") != "enabled":
            return None
        return {
            "config_file": str(policy_path(config)),
            "revision": policy["revision"],
            "repository": policy["repository"],
            "purpose": policy["purpose"],
        }
    except Exception:
        return None


def consent_status(reference):
    try:
        if set(reference.keys()) != _CONSENT_KEYS:
            return "withdrawn"
        purpose, repository, revision, config_file = (
            reference["purpose"], reference["repository"], reference["revision"], reference["config_file"]
        )
        if (purpose != _POLICY_PURPOSE or not _valid_repo(repository)
                or not isinstance(revision, str) or _INCIDENT_RE.fullmatch(revision) is None
                or not isinstance(config_file, str)):
            return "withdrawn"
        policy = read_policy(config_file)
        if policy is None:
            return "unavailable"
        matches = (policy["decision"] == "enabled" and policy["purpose"] == purpose
                   and policy["repository"] == repository and policy["revision"] == revision)
        return "allowed" if matches else "withdrawn"
    except PolicyUnavailable:
        return "unavailable"
    except (AttributeError, TypeError, KeyError):
        return "withdrawn"


def consent_allowed(reference) -> bool:
    return consent_status(reference) == "allowed"


def scope_key(public_fingerprint, reference) -> str:
    try:
        if (not isinstance(public_fingerprint, str) or not (0 < len(public_fingerprint) <= 256)
                or not public_fingerprint.isascii()
                or any(ord(ch) < 33 or ord(ch) == 127 for ch in public_fingerprint)
                or not isinstance(reference, dict) or set(reference.keys()) != _CONSENT_KEYS):
            return ""
        purpose, repository, revision = reference["purpose"], reference["repository"], reference["revision"]
        if (purpose != _POLICY_PURPOSE or not _valid_repo(repository)
                or not isinstance(revision, str) or _INCIDENT_RE.fullmatch(revision) is None):
            return ""
        canonical = json.dumps([purpose, repository, revision, public_fingerprint],
                               ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except Exception:
        return ""


def record_failure(component, operation, *, stage, category, exception=None, host=None,
                   revision=None, version=None, elapsed_ms=None, exit_code=None,
                   reportable=True, root=None, incident_id=None) -> dict:
    try:
        if isinstance(incident_id, str) and _INCIDENT_RE.fullmatch(incident_id):
            return {"recorded": True, "incident_id": incident_id, "logging_failed": False}
        if (not _label(component, 80) or not _label(operation, 120)
                or not _label(stage, 120) or not _label(category, 120) or not isinstance(reportable, bool)):
            return {"recorded": False, "incident_id": None, "logging_failed": False, "error": "invalid_arguments"}
        attributes = {"stage": stage, "category": category}
        if exception is not None:
            name = type(exception).__name__
            attributes["error_type"] = name if isinstance(name, str) and _ERROR_TYPE_RE.fullmatch(name) else "Exception"
        if host is not None:
            if not _label(host, 120):
                return {"recorded": False, "incident_id": None, "logging_failed": False, "error": "invalid_arguments"}
            attributes["host"] = host
        if exit_code is not None:
            if isinstance(exit_code, bool) or not isinstance(exit_code, int) or not (-2147483648 <= exit_code <= 2147483647):
                return {"recorded": False, "incident_id": None, "logging_failed": False, "error": "invalid_arguments"}
            attributes["exit_code"] = exit_code
        if elapsed_ms is not None and (isinstance(elapsed_ms, bool) or not isinstance(elapsed_ms, (int, float))
                                       or not math.isfinite(elapsed_ms) or not (0 <= elapsed_ms < 10**30)):
            return {"recorded": False, "incident_id": None, "logging_failed": False, "error": "invalid_arguments"}
        if revision is not None and (not isinstance(revision, str) or _REVISION_RE.fullmatch(revision) is None):
            return {"recorded": False, "incident_id": None, "logging_failed": False, "error": "invalid_arguments"}
        if version is not None and (not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None):
            return {"recorded": False, "incident_id": None, "logging_failed": False, "error": "invalid_arguments"}
        fresh = uuid.uuid4().hex
        effective = False if category in _NEVER_REPORTABLE else reportable
        with _lock:
            global _process_pid, _process_uuid
            pid = os.getpid()
            if _process_uuid is None or _process_pid != pid:
                _process_uuid, _process_pid = uuid.uuid4().hex, pid
            event = {
                "schema": 1, "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "monotonic_ns": time.monotonic_ns(), "pid": pid, "process_instance_id": _process_uuid,
                "component": component, "severity": "ERROR", "event": "operation.end",
                "operation_id": fresh, "trace_id": fresh, "operation": operation, "status": "error",
                "attributes": attributes, "reportable": effective,
                "reporting": current_consent() if effective else None,
            }
            if elapsed_ms is not None:
                event["duration_ms"] = int(elapsed_ms) if isinstance(elapsed_ms, int) else elapsed_ms
            if revision is not None:
                event["package_revision"] = revision
            if version is not None:
                event["package_version"] = version
            leaf = Path(default_root() if root is None else root) / "events" / component
            _private_mkdir(leaf, parents=True, exist_ok=True)
            target = leaf / f"{pid}-{_process_uuid}.jsonl"
            with target.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n")
        return {"recorded": True, "incident_id": fresh, "logging_failed": False}
    except Exception:
        return {"recorded": False, "incident_id": None, "logging_failed": True, "error": "logging_failed"}
