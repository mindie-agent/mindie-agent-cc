"""Structured failure envelopes for owned `setup.py` child processes.

[Role]
Defines the single-line `mindie.setup.failure/1` transport emitted by an owned
`setup.py` child under `MINDIE_SETUP_ERROR_ENVELOPE=1` and rebuilds the typed
parent error from that envelope. It also snapshots read-only consent state for
`setup.py sharing --status`.

[Invariants]
- Only static identifiers, an integer errno, validated bounded evidence and
  build metadata cross the child boundary; messages, paths, argv and stderr do
  not. `child_error` returns the caller's exception unchanged when no valid
  envelope is present, so an ordinary child keeps its existing behavior.
- A deferred envelope (`lock_busy`) exits zero; a failed envelope exits one.

[Timeouts & Locks]
- Pure in-memory projection; acquires no lock, subprocess, socket or deadline.

[Side-Effect Policy]
- Writes one stdout document plus a debug traceback on stderr only when the
  caller enabled the envelope; never writes a file or mutates host state.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import re
import sys
import traceback

SCHEMA = "mindie.setup.failure/1"
MAX_CHILD_OUTPUT = 64 * 1024
# A tuple keeps membership total for untrusted JSON values (no hashing).
CATEGORIES = (
    "temporary_network", "rate_limited", "certificate", "authentication",
    "resolver", "permission", "hook_trust", "busy", "deferred",
    "prerequisite", "cancelled",
)
_COMPONENT = "mindie-agent-cc"
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_LABEL = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,119}\Z")
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_CONSENT_CHOICES = frozenset({"contribute", "read-only", "later", "disabled"})


def enabled():
    """True only for an owned child that opted into the structured transport."""
    return os.environ.get("MINDIE_SETUP_ERROR_ENVELOPE") == "1"


def _token(value, pattern):
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _build():
    try:
        from diagnostic_support import build_metadata

        value = build_metadata()
    except Exception:  # noqa: BLE001 - envelope transport never changes the outcome
        return {}
    return value if isinstance(value, dict) else {}


def _fallback_evidence(exception, source_root):
    frames, traceback = [], exception.__traceback__
    while traceback is not None and len(frames) < 24:
        frame = traceback.tb_frame
        module = frame.f_globals.get("__name__")
        filename = frame.f_code.co_filename
        if source_root is not None and isinstance(filename, str) and os.path.isabs(filename):
            try:
                owned = os.path.commonpath((str(source_root), filename)) == str(source_root)
            except ValueError:
                owned = False
            if owned and filename.endswith(".py"):
                parts = [p for p in os.path.relpath(filename, source_root)[:-3]
                         .replace("\\", "/").split("/") if p and p != "__init__"]
                module = ".".join(["mindie_agent_cc"] + parts)
        if isinstance(module, str):
            frames.append(dict(module=module, function=frame.f_code.co_name,
                               line=traceback.tb_lineno))
        traceback = traceback.tb_next
    return {"stack_frames": frames, "exception_chain": [type(exception).__name__]} if frames else {}


def _evidence(exception, source_root):
    if source_root is None:
        return {}
    try:
        from mindie_diagnostics.evidence import exception_evidence

        value = exception_evidence(_COMPONENT, exception, source_root=source_root)
    except Exception:  # noqa: BLE001 - a missing runtime still emits an envelope
        return _fallback_evidence(exception, source_root)
    return value if isinstance(value, dict) else {}


def failure_envelope(exception, *, source_root=None):
    """Project one exception onto the static child-failure envelope."""
    failure = getattr(exception, "failure", None)
    failure = failure if isinstance(failure, dict) else {}
    category = getattr(exception, "category", None)
    number = getattr(exception, "errno", None)
    stage = _token(failure.get("stage"), _LABEL) or _token(getattr(exception, "substage", None), _LABEL) or "setup"
    code = _token(failure.get("code"), _CODE) or _token(getattr(exception, "reason_code", None), _CODE)
    payload = {
        "schema": SCHEMA, "status": "failed", "returncode": 1,
        "type": type(exception).__name__, "stage": stage,
        "substage": _token(getattr(exception, "substage", None), _LABEL) or stage,
        "code": code,
        "reason_code": _token(getattr(exception, "reason_code", None), _CODE) or code,
        "category": category if category in CATEGORIES else None,
        "errno": number if isinstance(exception, OSError)
        and not isinstance(exception, TimeoutError) and type(number) is int else None,
        "evidence": _evidence(exception, source_root), "build": _build(),
    }
    return {key: value for key, value in payload.items() if value is not None}


def emit_failure(exception, *, source_root=None):
    try:
        traceback.print_exception(type(exception), exception, exception.__traceback__,
                                  file=sys.stderr)
    except Exception:  # noqa: BLE001 - debug output never blocks the envelope
        pass
    print(json.dumps(failure_envelope(exception, source_root=source_root), sort_keys=True))


def emit_deferred(stage="lock_busy"):
    print(json.dumps({"status": "deferred", "stage": stage, "switched": False}, sort_keys=True))


def consent_snapshot(path):
    """Read the saved consent choice without importing the consent runtime."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(state="missing", choice=None, choice_revision=None)
    except TimeoutError:
        raise
    except OSError:
        return dict(state="unreadable", choice=None, choice_revision=None)
    except ValueError:
        return dict(state="corrupt", choice=None, choice_revision=None)
    choice = data.get("choice") if isinstance(data, dict) else None
    revision = data.get("choice_revision") if isinstance(data, dict) else None
    if (not isinstance(data, dict) or data.get("schema") != "mindie-consent/1"
            or (choice is not None and choice not in _CONSENT_CHOICES)
            or (revision is not None
                and not (isinstance(revision, str) and _HEX32.fullmatch(revision)))):
        return dict(state="corrupt", choice=None, choice_revision=None)
    return dict(state="ok", choice=choice, choice_revision=revision)


def _payload(exception):
    raw = getattr(exception, "mindie_output", None)
    if not isinstance(raw, str) or not raw[:MAX_CHILD_OUTPUT].strip():
        return None
    try:
        value = json.loads(raw[:MAX_CHILD_OUTPUT].strip())
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("schema") == SCHEMA else None


def _validated_evidence(value):
    if not isinstance(value, dict):
        return {}
    try:
        from mindie_diagnostics.evidence import validate_evidence

        result = validate_evidence(_COMPONENT, value)
    except Exception:  # noqa: BLE001 - untrusted transport without a validator is dropped
        return {}
    return result if isinstance(result, dict) else {}


def child_error(exception):
    """Rebuild the typed parent error from one validated child envelope."""
    payload = _payload(exception)
    if payload is None:
        return exception
    kind = payload.get("type")
    stage = _token(payload.get("stage"), _LABEL)
    code = _token(payload.get("code"), _CODE) or _token(payload.get("reason_code"), _CODE)
    number = payload.get("errno")
    number = number if type(number) is int else None
    message = (stage or "setup") + ": " + (code or _token(kind, _LABEL) or "failure")
    if kind == "TimeoutError":
        error = TimeoutError(message)
    elif kind == "PermissionError":
        error = PermissionError(number if number is not None else errno.EACCES, message)
    elif kind == "FileNotFoundError":
        error = FileNotFoundError(number if number is not None else errno.ENOENT, message)
    elif kind == "OSError" and number is not None:
        error = OSError(number, message)
    else:
        error = RuntimeError(message)
    build = payload.get("build") if isinstance(payload.get("build"), dict) else None
    returncode = payload.get("returncode")
    if type(returncode) is not int:
        returncode = getattr(exception, "returncode", None)
    for name, value in (
        ("returncode", returncode if type(returncode) is int else None),
        ("mindie_stage", stage),
        ("category", payload.get("category") if payload.get("category") in CATEGORIES else None),
        ("reason_code", _token(payload.get("reason_code"), _CODE) or code),
        ("substage", _token(payload.get("substage"), _LABEL)),
        ("mindie_failure_evidence", _validated_evidence(payload.get("evidence"))),
        ("mindie_failure_build", build),
    ):
        if value:
            setattr(error, name, value)
    return error
