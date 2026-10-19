"""Thin stdlib adapter to shared diagnostics. Import performs no I/O.

[Role]
Bridges Claude Code adapter failures to `mindie_diagnostics` and formats
user-visible `diagnostic_ref` warnings. An unavailable runtime emits only a
static stderr warning; it never maintains a second diagnostic store.

[Invariants]
- Import performs zero I/O.
- If diagnostic storage fails, emits a single stderr warning and preserves the
  original operation outcome unchanged.

[Timeouts & Locks]
- Fault recording uses local I/O only. Explicit reporter activation has a
  75-second shared budget within a 90-second caller, never inside short Hooks.

[Side-Effect Policy]
- Writes redacted diagnostic records under the MindIE state directory.
"""

from __future__ import annotations

import json
import os
import re
import shlex
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
        sys.stderr.write(_STORAGE + "\n")
    except Exception:
        pass


def _record():
    from mindie_diagnostics.integration import record_failure
    os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(reporting_config_path())
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
    text = (f"MindIE incident {incident}; read-only local status: /mindie-agent:reporting-status. Fault uploads follow the contribution switch."
            if incident else _STORAGE)
    content = result.get("content")
    if isinstance(content, list):
        result["content"] = [*content, {"type": "text", "text": text}]
    elif "content" not in result:
        result["content"] = [{"type": "text", "text": text}]
    return result


def reporting_config_path(config_file=None):
    explicit = os.environ.get("MINDIE_DIAGNOSTICS_CONFIG")
    if explicit:
        return Path(explicit).expanduser().absolute()
    from paths import base_config_path, config_path
    profile = Path(config_file) if config_file is not None else base_config_path()
    adapter_path = Path(config_file) if config_file is not None else config_path()
    if adapter_path.is_file():
        adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
        selected = adapter.get("diagnostics_context", {}).get("MINDIE_DIAGNOSTICS_CONFIG")
        if selected:
            return Path(selected).expanduser().absolute()
    return profile.expanduser().absolute().parent / "diagnostics.json"


def _unavailable(stage, error_type):
    return {
        "enabled": None, "status": "unavailable",
        "error": {"stage": stage, "type": error_type},
        "hint": "Inspect the selected MindIE runtime; no repair or retry was started.",
    }


def reporting_status():
    try:
        from mindie_diagnostics.integration import reporting_status as upstream
        import consent
        result = upstream(config=reporting_config_path())
        return effective_reporting(result, consent.load())
    except Exception as exc:
        return _unavailable("reporting_status", type(exc).__name__)


def activate_reporting(config_file=None, *, ensure=True):
    """Derive policy from the existing authority; only long-lived callers ensure."""
    from mindie_knowledge.consent_store import activate_reporting as activate
    import consent
    policy = reporting_config_path(config_file)
    authority = consent.consent_path(config_file)
    return activate(authority, config=policy, repository=REPOSITORY, ensure=ensure)


def activation_handoff(python):
    """The native ten-second Hook configures; the same Skill finishes outside it."""
    from paths import base_config_path
    result = activate_reporting(ensure=False)
    if result.get("status") == "configured" and result.get("enabled"):
        command = [python, str(Path(__file__).resolve()), "activate",
                   "--diagnostics-config", str(reporting_config_path()),
                   "--config", str(base_config_path())]
        if os.environ.get("MINDIE_DIAGNOSTICS_ROOT"):
            command.extend(["--diagnostics-root", os.environ["MINDIE_DIAGNOSTICS_ROOT"]])
        result = dict(result, status="reporter_pending", run_outside_hook=True,
                      command=command, command_line=ensure_command_line(command),
                      command_shell="powershell" if os.name == "nt" else "posix",
                      note="Execute this command now in the same contribution setup flow; verify worker readiness before reporting completion.")
    return result


def ensure_command_line(command):
    if os.name == "nt":
        return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in command)
    return shlex.join(command)


def effective_reporting(view, saved):
    """The authority gates status too; a legacy choice cannot widen consent."""
    if saved.get("state") in {"corrupt", "unreadable"}:
        return dict(view, enabled=False, status="unavailable", consent=saved)
    if not saved.get("choice_revision"):
        return dict(view, enabled=False, status="needs_choice", consent=saved,
                    detail="Choose contribution again to authorize knowledge sharing and sanitized fault Issues together.")
    if saved.get("choice") != "contribute":
        return dict(view, enabled=False, status="disabled", consent=saved)
    return dict(view, consent=saved)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Activate fault reporting from the saved contribution choice.")
    parser.add_argument("operation", choices=["activate", "status"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--diagnostics-config", type=Path)
    parser.add_argument("--diagnostics-root", type=Path)
    args = parser.parse_args(argv)
    os.environ["MINDIE_CC_CONFIG"] = str(args.config.expanduser().absolute())
    if args.diagnostics_config:
        os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(args.diagnostics_config.expanduser().absolute())
    if args.diagnostics_root:
        os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(args.diagnostics_root.expanduser().absolute())
    try:
        result = reporting_status() if args.operation == "status" else activate_reporting()
    except Exception as exc:
        result = _unavailable("reporting_activation", type(exc).__name__)
    print(json.dumps(result))
    return int(result.get("status") not in {"running", "disabled"}
               if args.operation == "activate" else result.get("status") == "unavailable")


if __name__ == "__main__":
    raise SystemExit(main())
