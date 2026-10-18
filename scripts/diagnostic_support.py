"""Thin stdlib adapter to shared diagnostics. Import performs no I/O.

[Role]
Bridges Claude Code adapter failures to `mindie_diagnostics` (or
`diagnostic_fallback` when the runtime is unavailable) and formats user-visible
`diagnostic_ref` warnings.

[Invariants]
- Import performs zero I/O.
- If diagnostic storage fails, emits a single stderr warning and preserves the
  original operation outcome unchanged.

[Timeouts & Locks]
- Synchronous, non-blocking local incident recording.

[Side-Effect Policy]
- Writes redacted diagnostic records under the MindIE state directory.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import sys
from pathlib import Path

COMPONENT = "mindie-agent-cc"
HOST = "claude-code"
REPOSITORY = "mindie-agent/mindie-agent"
_STORAGE = "MindIE diagnostic storage unavailable; original outcome unchanged."
_warned = False


def _hex(value, length):
    if isinstance(value, str) and len(value) == length:
        if all(char in "0123456789abcdef" for char in value):
            return value
    return None


def _read_small(path):
    """Read bounded regular metadata."""
    try:
        path = Path(path)
        if not path.is_file():
            return None
        with path.open("rb") as stream:
            raw = stream.read(2049)
        if len(raw) > 2048:
            return None
        return raw.decode()
    except Exception:
        return None


def build_metadata():
    """Use package metadata, or the same generation's older install records."""
    here = Path(__file__).resolve().parent
    try:
        raw = _read_small(here / "diagnostic-build.json")
        if raw is not None:
            data = json.loads(raw)
        elif (here / "resources/plugin.json").is_file():
            manifest = json.loads(_read_small(here / "resources/plugin.json") or "null")
            data = {"version": manifest.get("version")} if isinstance(manifest, dict) else {}
        else:
            # Old updaters do not emit diagnostic-build.json. Their completed
            # Git generation and stamped host manifest are existing facts.
            generation = here.parent
            if here.name != "scripts" or generation.parent.name != "generations":
                return {}
            revision = _hex(generation.name, 40)
            marker = _read_small(generation / ".mindie-generation-complete")
            if not revision or marker is None or marker.strip() != revision:
                return {}
            manifest = json.loads(_read_small(generation / "host-package" / ".claude-plugin" / "plugin.json") or "null")
            data = {"revision": revision}
            if isinstance(manifest, dict) and manifest.get("name") == "mindie-agent":
                data["version"] = manifest.get("version")
        if not isinstance(data, dict):
            return {}
        revision = _hex(data.get("revision"), 40)
        version = data.get("version")
        result = {"revision": revision} if revision else {}
        if isinstance(version, str) and re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,79}", version):
            result["version"] = version
        return result
    except Exception:
        return {}


def _warn():
    global _warned
    if _warned:
        return
    _warned = True
    try:
        descriptor = sys.stderr.fileno()
        if not os.get_blocking(descriptor):
            os.write(descriptor, (_STORAGE + "\n").encode("ascii"))
    except Exception:
        pass


def _record():
    try:
        from mindie_diagnostics.integration import record_failure
    except ImportError:
        from diagnostic_fallback import record_failure
    return record_failure


def failure(operation, stage, category, *, exception=None, revision=None,
            elapsed_ms=None, exit_code=None, reportable=True, incident_id=None):
    """Only static product metadata; a trusted inner incident is not recorded twice."""
    trusted = _hex(incident_id, 32)
    if trusted:
        return {"incident_id": trusted, "logging_failed": False}
    inner = reference({"diagnostic": getattr(exception, "mindie_diagnostic", None)})
    if inner is not None:
        return inner
    build = build_metadata()
    explicit = _hex(revision, 40)
    try:
        raw = _record()(
            COMPONENT, operation, stage=stage, category=category,
            exception=exception, host=HOST,
            revision=explicit or build.get("revision"),
            version=build.get("version"),
            elapsed_ms=elapsed_ms, exit_code=exit_code, reportable=reportable,
        )
        if not isinstance(raw, dict):
            raise TypeError("invalid diagnostic result")
        if type(raw.get("recorded")) is not bool or type(raw.get("logging_failed")) is not bool:
            raise TypeError("invalid diagnostic result")
        result = {"recorded": raw["recorded"], "logging_failed": raw["logging_failed"]}
        incident = _hex(raw.get("incident_id"), 32)
        if incident:
            result["incident_id"] = incident
        return result
    except Exception:
        _warn()
        return {"logging_failed": True}


def _ref(diagnostic):
    if not isinstance(diagnostic, dict):
        return None
    failed = diagnostic.get("logging_failed", False)
    if type(failed) is not bool:
        return None
    incident = _hex(diagnostic.get("incident_id"), 32)
    if incident:
        return {"incident_id": incident, "logging_failed": failed}
    if failed is True:
        return {"logging_failed": True}
    return None


def reference(result):
    """Inspect only structured references returned by a trusted inner boundary."""
    if not isinstance(result, dict):
        return None
    found = _ref(result.get("diagnostic"))
    if found is not None:
        return found
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return _ref(structured.get("diagnostic"))
    return None


def attach(result, diagnostic):
    """Preserve result semantics; expose only the projected incident reference."""
    if not isinstance(result, dict) or reference(result) is not None:
        return result
    projected = _ref(diagnostic)
    if projected is None:
        return result
    result = dict(result, diagnostic=projected)
    incident = projected.get("incident_id")
    text = (f"MindIE incident {incident}; read-only local status: /mindie-agent:reporting-status. Upload remains a separate opt-in."
            if incident else _STORAGE)
    content = result.get("content")
    if isinstance(content, list):
        result["content"] = [*content, {"type": "text", "text": text}]
    elif "content" not in result:
        result["content"] = [{"type": "text", "text": text}]
    return result


def reporting_config_path():
    explicit = os.environ.get("MINDIE_DIAGNOSTICS_CONFIG")
    if explicit:
        return Path(explicit).expanduser().resolve()
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return (base / "mindie-agent" / "diagnostics.json").expanduser().resolve()


def _unavailable(stage, error_type):
    return {
        "enabled": None, "status": "unavailable",
        "error": {"stage": stage, "type": error_type},
        "hint": "Inspect the selected MindIE runtime; no repair or retry was started.",
    }


def reporting_status():
    try:
        from mindie_diagnostics.integration import reporting_status as upstream
        result = upstream(config=reporting_config_path())
        if not isinstance(result, dict):
            raise TypeError("invalid reporting status")
        return result
    except Exception as exc:
        return _unavailable("reporting_status", type(exc).__name__)


def ensure_argv(python):
    if not isinstance(python, str) or not Path(python).is_absolute():
        raise ValueError("selected runtime requires an absolute interpreter")
    return [python, "-m", "mindie_diagnostics.cli", "reporting", "ensure",
            "--config", str(reporting_config_path())]


def ensure_command_line(command):
    if os.name == "nt":
        return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in command)
    return shlex.join(command)


def configure_reporting(enabled, python):
    if type(enabled) is not bool:
        return _unavailable("reporting_configure", "TypeError")
    try:
        command = ensure_argv(python)
        from mindie_diagnostics.integration import configure_reporting as upstream
        result = upstream(enabled, repository=REPOSITORY, config=reporting_config_path())
        if not isinstance(result, dict) or result.get("enabled") is not enabled:
            return _unavailable("reporting_configure", "ConfigurationUnconfirmed")
        result = dict(result, run_outside_hook=False)
        if enabled:
            result.update(
                run_outside_hook=True, command=command,
                command_line=ensure_command_line(command),
                command_shell="powershell" if os.name == "nt" else "posix",
                note=("Reporting settings are enabled. Execute this command once outside "
                      "the Hook and inspect its result before claiming reporter readiness. "
                      "Report any failure; do not automatically retry."),
            )
        return result
    except Exception as exc:
        return _unavailable("reporting_configure", type(exc).__name__)


def effective_reporting(view, saved_reporting):
    """Expose a stale enabled policy without treating it as a saved choice."""
    if not isinstance(view, dict) or view.get("enabled") is not True:
        return view
    if saved_reporting == "enabled":
        return view
    return dict(view, enabled=False, policy_enabled=True,
                status="consent_mismatch",
                detail=("The reporter policy is enabled but the saved reporting choice is "
                        f"{saved_reporting or 'absent'}. Inspect reporting status and explicitly "
                        "choose reporting-enable or reporting-disable to reconcile them."))


def reporting_hint():
    return {
        "optional": True, "recommended": True,
        "independent_of_knowledge_contribution": True,
        "enable": "/mindie-agent:reporting-enable",
        "disable": "/mindie-agent:reporting-disable",
        "later": "/mindie-agent reporting-later",
        "status": "/mindie-agent:reporting-status",
        "repository": REPOSITORY,
        "note": ("Optionally report sanitized product fault code metadata to the public "
                 "repository. Prompts, transcripts, commands, environment and credentials "
                 "are excluded. This is a separate shared user choice decided once; the "
                 "entry also accepts reporting-enable/reporting-disable/reporting-later. "
                 "Enabled, disabled and later all persist and are never re-asked. "
                 "Consult its status anytime."),
    }
