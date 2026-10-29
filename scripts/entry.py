"""Slash-command operations invoked from UserPromptExpansion.

[Role]
Handles `/mindie-agent` slash commands (`activate`, `deactivate`, `status`,
`sharing-enable`, `sharing-disable`, `sharing-status`) from the Claude Code
`UserPromptExpansion` hook. An optional native `--domain DOMAIN` activates one
existing configured domain through the shared domain-routing helper.

[Invariants]
- Activation occurs exclusively through this hook; MCP `status` never activates
  a lease or enables sharing.
- `--domain` uses only the verified native session/cwd/root-session binding; it
  activates the target Admission and never overwrites the default host token or
  consent authority.
- If lease activation succeeds but background service/maintenance fails,
  returns `status="degraded"` (preserving the active lease).

[Timeouts & Locks]
- Runs within the `mindie_launch.py` hook deadline (`HOOK_TOTAL = 10.0s`).

[Side-Effect Policy]
- Mutates session leases (`admission.py`), explicit consent (`entry_state.py`),
  and community sharing settings (`sharing.py`) only on explicit slash commands.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from entry_state import consume_prompt, set_choice, configuration_required
from entry_arguments import (
    _parse_sharing, _prompt_id, _project_root, _init_choice_from_native,
    _domain_from_native, _reporting_token_from_native,
)
from identity import is_subagent, require_absolute, require_session, slash_command
from paths import config_path, load_adapter_config


def _configured() -> bool:
    return config_path().is_file()


def consume_slash(session, event, command):
    found = slash_command(event)
    if found != command:
        raise ValueError("current slash command does not match this operation")
    ident = f"{command}:{_prompt_id(event)}"
    if not _configured():
        return consume_prompt(ident)
    from admission import gate

    token = None
    try:
        lease = gate().active_lease(session)
        if lease and isinstance(lease.get("token"), str):
            token = lease["token"]
    except TimeoutError:
        raise
    except Exception as exc:
        raise ValueError(
            f"admission is unavailable: {type(exc).__name__}: {exc}"
        ) from exc
    if token is None:
        return consume_prompt(ident)
    return gate().claim(session, "plugin_command", ident[:256], token=token) is True


def _diagnostics(session):
    """One bounded read of existing state; never activate or repair."""
    try:
        from mindie_knowledge.loop.diagnostics import snapshot
        from paths import engine_config_path

        return snapshot(str(engine_config_path()), session=session)
    except TimeoutError:
        raise
    except Exception as exc:
        return dict(
            status="unavailable",
            error=dict(stage="runtime_diagnostics", type=type(exc).__name__),
            hints=["Inspect the selected MindIE runtime. Native tools and independent SSH remain available; no recovery or retry was started."],
        )


def _bounded_text(value, limit):
    if not isinstance(value, str) or not value:
        return None
    return value[:limit]


_META_LIMIT = 65536


def _read_regular_json(path):
    """Small JSON object from a regular file."""
    path = Path(path)
    try:
        if not path.exists():
            return "missing", None
        if not path.is_file():
            return "unavailable", None
        with path.open("rb") as stream:
            blob = stream.read(_META_LIMIT + 1)
    except TimeoutError:
        raise
    except OSError:
        return "unavailable", None
    if len(blob) > _META_LIMIT:
        return "unavailable", None
    try:
        value = json.loads(blob.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, ValueError):
        return "unavailable", None
    if not isinstance(value, dict):
        return "unavailable", None
    return None, value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _installation_view():
    """Read maintenance receipts without recovering or selecting code."""
    try:
        adapter = load_adapter_config()
        state, maintenance = _read_regular_json(
            Path(adapter["state_dir"]) / "session-maintenance.json")
        result = {"code_updates": "native-marketplace"}
        if state == "unavailable":
            result["maintenance"] = {"status": "unavailable"}
        elif maintenance is not None:
            result["maintenance"] = maintenance
        return result
    except TimeoutError:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        return {"status": "unavailable", "code_updates": "native-marketplace"}


def _knowledge_status_payload(session=None):
    if not _configured():
        payload = configuration_required()
        if payload["first_use"] in {"read-only", "later", "contribute"}:
            payload["repeat"] = True
            payload["choices"] = []
        return payload
    import consent as consent_mod
    from sharing import public_status

    try:
        sharing_view, saved = public_status(), consent_mod.load()
    except TimeoutError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return dict(
            configured=None,
            sharing=dict(enabled=None, status="unavailable"),
            error=dict(stage="local_settings", type=type(exc).__name__),
            config=str(config_path()),
            diagnostics=_diagnostics(session),
            hint="Inspect the existing adapter and community configuration. Native tools and independent SSH remain available; no setup or retry was started.",
        )
    choice = saved["choice"] if saved["state"] == "ok" else None
    payload = dict(configured=True, sharing=sharing_view, first_use=choice,
                   consent_state=saved["state"])
    diagnostics = _diagnostics(session)
    payload["diagnostics"] = diagnostics
    if session:
        admission = diagnostics.get("admission") or {}
        state = admission.get("status", "unavailable")
        payload["this_session"] = dict(
            status=state,
            bound=state == "active",
            enabled=state == "active" and admission.get("enabled") is True,
            failures=admission.get("failures"),
            project_root=admission.get("project_root"),
        )
    payload["choices"] = []
    payload["repeat"] = bool(choice or saved["state"] in {"corrupt", "unreadable"}
                             or sharing_view.get("state") in {"corrupt", "unreadable"})
    # Saved preferences and runtime configuration are distinct facts. A prior
    # choice must not hide an incomplete or failed experience loop.
    if saved["state"] in {"corrupt", "unreadable"}:
        payload["experience"] = "unavailable"
        payload["consent_error"] = dict(state=saved["state"], error=saved["error"])
        payload["hint"] = "Saved setup state is damaged; experience capture is unavailable. Preserve the file for diagnosis."
    elif sharing_view.get("state") in {"corrupt", "unreadable"}:
        payload["experience"] = "unavailable"
        payload["hint"] = "Community configuration is damaged; experience capture is unavailable."
    elif choice in {"read-only", "later", "disabled"}:
        payload["experience"] = "disabled"
        payload["hint"] = "Experience capture is explicitly disabled; the saved setting is preserved."
    elif not sharing_view.get("enabled") or not choice:
        payload["experience"] = "needs-configuration"
        extra = configuration_required()
        payload.update(required=extra["required"], note=extra["note"])
        payload["hint"] = "Configure the missing destination and scope through the native mindie-agent entry."
    else:
        from sharing import load
        settings = load()
        task = payload.get("this_session") or {}
        if not settings.allows_capture():
            payload["experience"] = "unavailable"
        elif not task.get("bound"):
            payload["experience"] = "task-unbound"
        elif not settings.in_scope(task.get("project_root")):
            payload["experience"] = "out-of-scope"
        else:
            payload["experience"] = "configured"
        payload["hint"] = "Configuration and task binding are prerequisites; inspect captures and contributions for actual processing receipts."
    update = _installation_view()
    if update:
        payload["update"] = update
    return payload


def status_payload(session=None):
    """Read-only status of capture and reporting under one contribution choice."""
    import consent as consent_mod
    import diagnostic_support

    payload = dict(_knowledge_status_payload(session))
    try:
        consent_mod.load()
    except TimeoutError:
        raise
    except (OSError, ValueError) as exc:
        payload["reporting"] = dict(status="unavailable",
                                    error=dict(stage="consent_path", type=type(exc).__name__))
        payload["experience"] = "unavailable"
        return payload
    payload["reporting"] = diagnostic_support.reporting_status()
    return payload


def status_facts(session=None):
    """Deterministic control-plane facts, without knowledge setup guidance."""
    payload = dict(configured=_configured(), knowledge_enabled=False)
    if payload["configured"] and session:
        from admission import gate
        lease = gate().knowledge_lease(session)
        payload["knowledge_enabled"] = lease is not None
        payload["this_session"] = dict(bound=lease is not None, enabled=lease is not None,
                                       status="active" if lease else "inactive",
                                       project_root=lease["project_root"] if lease else None)
    if payload["configured"]:
        diagnostics = _diagnostics(session)
        payload["diagnostics"] = {key: value for key, value in diagnostics.items() if key != "hints"}
        if session:
            payload["this_session"]["failures"] = (diagnostics.get("admission") or {}).get("failures")
    import diagnostic_support
    payload["reporting"] = diagnostic_support.reporting_status()
    return payload


def _apply_native_choice(payload, native_choice, session=None):
    if native_choice is None:
        return payload
    import consent as consent_mod

    try:
        if native_choice == "disabled" and _configured():
            from sharing import write_disabled
            write_disabled()
        stored = set_choice(native_choice)
        from diagnostic_support import activate_reporting
        payload["reporting"] = activate_reporting(ensure=False)
    except consent_mod.ConsentError as exc:
        # A damaged saved authority is never silently cleared: the fault
        # payload shows the real state instead of recording over it.
        payload = status_payload(session)
        payload["consent_error"] = dict(state=exc.state, error=str(exc))
        return payload
    payload["first_use"] = stored
    payload["choices"] = []
    payload["repeat"] = True
    payload["experience"] = "disabled"
    return payload


def _capture_permitted() -> bool:
    """The whole capture chain (schema, enabled, generation, scope, and the
    consent authority when the config carries consent_config) as the shared
    core gate reads it. The service is only woken when capture can persist."""
    try:
        from sharing import load

        return bool(load().allows_capture())
    except TimeoutError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"capture authority is unreadable: {type(exc).__name__}: {exc}"
        ) from exc


def _prepare_capture_service():
    """Contribution-on only. Detached, model-free, not waited in the Hook."""
    if not _capture_permitted():
        return "off"
    try:
        from knowledge_service import prepare_service

        return prepare_service()
    except TimeoutError:
        raise
    except Exception as exc:
        return f"prepare-failed:{type(exc).__name__}"


def _finish_contribution(result: dict) -> dict:
    """Commit one explicit choice, then prepare capture and a reporter handoff."""
    import consent as consent_mod
    import diagnostic_support

    try:
        consent_mod.record_choice("contribute")
    except consent_mod.ConsentError as exc:
        return dict(result, status="degraded", experience="unavailable",
                    consent_error=dict(state=exc.state, error=str(exc)))
    result["service"] = _prepare_capture_service()
    try:
        result["reporting"] = diagnostic_support.activation_handoff(load_adapter_config()["python"])
    except TimeoutError:
        raise
    except Exception as exc:
        result["reporting"] = dict(status="unavailable", error_type=type(exc).__name__)
    result["status"] = "reporter_pending"
    if (str(result["service"]).startswith("prepare-failed:")
            or result["reporting"].get("status") != "reporter_pending"):
        result["status"] = "degraded"
        result["experience"] = "unavailable"
        result["warning"] = "Contribution was saved, but capture or fault reporting preparation is incomplete."
    return result


def _apply_contribute(payload, session, event, configured):
    """First contribution through the unified entry itself: the user picks
    contribute and names the public destination in the same invocation —
    no separate sharing command to learn. Native slash origin is still the
    trust boundary; model arguments never enable sharing."""
    if not configured:
        payload["contribute"] = dict(
            recorded=False,
            note=(
                "Contribution requires an installed runtime first: "
                "Start a new Claude session with the MindIE native plugin enabled"
            ),
        )
        return payload
    import sharing as sharing_mod

    try:
        parsed = _parse_sharing(event.get("command_args") or "")
    except ValueError as exc:
        payload["contribute"] = dict(recorded=False, error=str(exc))
        return payload
    import consent as consent_mod
    if consent_mod.load()["state"] in {"corrupt", "unreadable"}:
        payload["contribute"] = dict(recorded=False, error="saved setup state is damaged")
        payload["experience"] = "unavailable"
        return payload
    try:
        result = sharing_mod.write_enabled(**parsed)
    except TimeoutError:
        raise
    except (ValueError, OSError) as exc:
        payload["contribute"] = dict(recorded=False, error=str(exc)[:200])
        return payload
    payload.update(first_use="contribute", choices=[], repeat=True)
    payload["sharing"] = result
    payload = _finish_contribution(payload)
    refreshed = status_payload(session)
    refreshed.pop("reporting", None)
    refreshed.pop("status", None)
    payload.update(refreshed)
    if str(payload.get("service", "")).startswith("prepare-failed:") or payload.get("consent_error"):
        payload["status"] = "degraded"
        payload["experience"] = "unavailable"
        payload["warning"] = f"knowledge capture service could not be prepared ({payload.get('service')})"
    return payload


def _reporting_guidance():
    return dict(
        status="contribution_choice_required",
        detail="Knowledge sharing and sanitized fault Issues share one contribution switch.",
        enable="/mindie-agent contribute --repository OWNER/REPO --account USER --project-root PATH --visibility public",
        disable="/mindie-agent disabled",
    )


def _apply_reporting_token(payload, token, configured):
    payload["reporting"] = _reporting_guidance()
    return payload


def _configured_domain_activation(session, root, ident, domain):
    """Activate one explicitly selected domain without touching the default.

    The shared helper validates the trusted target engine config, target
    settings/consent and project scope, then calls that target Admission. The
    returned target lease is never written into the default host binding.
    """
    try:
        from mindie_knowledge.loop.domain_routes import activate_selected_domain
    except ModuleNotFoundError as exc:
        if exc.name != "mindie_knowledge.loop.domain_routes":
            raise
        raise ValueError(
            "multi-domain native activation is unavailable in the installed knowledge runtime"
        ) from exc
    from admission import gate
    from mindie_knowledge.loop.cli import config_at
    from paths import engine_config_path

    adapter = load_adapter_config()
    selected = activate_selected_domain(
        engine_config_path(adapter),
        domain=domain,
        domain_configs=adapter.get("domain_configs"),
        session=session,
        project_root=root,
        root_session=session,
    )
    lease = selected["lease"]
    target_gate = gate(config_at(selected["engine_config"]))
    claimed = target_gate.claim(
        session, "plugin_command", ident[:256], token=lease["token"]) is True
    payload = status_payload(session)
    if not claimed:
        payload["already"] = True
    payload["domain"] = selected["domain"]
    payload["engine_config"] = selected["engine_config"]
    payload["generation"] = selected["generation"]
    payload["binding"] = dict(
        session=lease["session"],
        enabled=bool(lease.get("enabled")),
        project_root=lease["project_root"],
        root_session=lease.get("root_session"),
        domain=selected["domain"],
        engine_config=selected["engine_config"],
        generation=selected["generation"],
    )
    return payload


def _configured_init_activation(session, cwd, ident, domain=None):
    """Entry binding for a configured task: automatic, idempotent, and never
    a consent prompt. The persistent install-level choice is untouched."""
    from admission import activate, gate
    root = _project_root(cwd)
    if domain is not None:
        return _configured_domain_activation(session, root, ident, domain)
    lease = activate(session, project_root=root, root_session=session)
    claimed = gate().claim(session, "plugin_command", ident[:256], token=lease["token"]) is True
    payload = status_payload(session)
    if not claimed:
        payload["already"] = True
    service = _prepare_capture_service()
    payload["binding"] = dict(
        session=lease["session"],
        enabled=bool(lease.get("enabled")),
        project_root=lease["project_root"],
        service=service,
    )
    if str(service).startswith("prepare-failed:"):
        payload["status"] = "degraded"
        payload["experience"] = "unavailable"
        payload["warning"] = f"knowledge capture service could not be prepared ({service})"
    return payload


def op_init(session, event):
    slash_command(event)
    native_choice = _init_choice_from_native(event.get("command_args"))
    reporting_token = _reporting_token_from_native(event.get("command_args"))
    domain = _domain_from_native(event.get("command_args"))
    ident = f"init:{_prompt_id(event)}"
    cwd = event.get("cwd")
    if not _configured():
        if not consume_prompt(ident):
            payload = status_payload(session)
            payload["already"] = True
            return payload
        if native_choice == "contribute":
            payload = _apply_contribute(status_payload(session), session, event, False)
        elif native_choice is None:
            payload = status_payload(session)
            payload["runtime"] = "unconfigured"
        else:
            payload = _apply_native_choice(status_payload(session), native_choice, session)
        if reporting_token is not None and domain is None:
            payload = _apply_reporting_token(payload, reporting_token, False)
        return payload
    payload = _configured_init_activation(session, cwd, ident, domain)
    if native_choice == "contribute":
        payload = _apply_contribute(payload, session, event, True)
    else:
        payload = _apply_native_choice(payload, native_choice, session)
    if reporting_token is not None and domain is None:
        payload = _apply_reporting_token(payload, reporting_token, True)
    return payload


def op_status(session, event):
    consume_slash(session, event, "status")
    return status_payload(session)


def _deactivate_domains(session, adapter, mappings):
    try:
        from mindie_knowledge.loop.domain_routes import deactivate_registered_domains
    except ModuleNotFoundError as exc:
        if exc.name != "mindie_knowledge.loop.domain_routes":
            raise
        raise ValueError(
            "multi-domain native deactivation is unavailable in the installed knowledge runtime"
        ) from exc
    from paths import engine_config_path

    return deactivate_registered_domains(
        engine_config_path(adapter), domain_configs=mappings, session=session)


def op_deactivate(session, event):
    found = slash_command(event)
    if found != "deactivate":
        raise ValueError("current slash command does not match this operation")
    _prompt_id(event)
    if (event.get("command_args") or "").strip():
        raise ValueError("deactivate takes no arguments; it revokes this session across registered domains")
    if not _configured():
        return dict(session=session, deactivated=False, configured=False)
    adapter = load_adapter_config()
    mappings = adapter.get("domain_configs")
    if mappings is None:
        mappings = {}
    elif not isinstance(mappings, dict):
        raise ValueError("domain_configs must be a mapping")
    if not mappings:
        from admission import deactivate

        return dict(session=session, deactivated=bool(deactivate(session)))
    result = _deactivate_domains(session, adapter, mappings)
    status = result["status"]
    payload = dict(
        session=session,
        deactivated=status == "inactive",
        status=status,
        changed=bool(result["changed"]),
        domains=result["domains"],
    )
    if status == "partial":
        payload["partial"] = True
        payload["warning"] = (
            "Deactivation was partial; some registered domain scopes could not be confirmed inactive."
        )
    return payload


def op_sharing_enable(session, event):
    first = consume_slash(session, event, "sharing-enable")
    if not first:
        payload = status_payload(session)
        payload["already"] = True
        return payload
    if not _configured():
        raise ValueError("MindIE is not configured; start a new Claude session with the native plugin enabled")
    import sharing as sharing_mod

    parsed = _parse_sharing(event.get("command_args") or "")
    result = sharing_mod.write_enabled(**parsed)
    return _finish_contribution(dict(result))


def op_sharing_disable(session, event):
    consume_slash(session, event, "sharing-disable")
    if not _configured():
        return dict(configured=False, enabled=False)
    import consent as consent_mod
    import sharing as sharing_mod

    result = sharing_mod.write_disabled()
    try:
        consent_mod.record_choice("disabled")
        from diagnostic_support import activate_reporting
        result = dict(result, reporting=activate_reporting(ensure=False))
    except consent_mod.ConsentError as exc:
        result = dict(result)
        result["consent_error"] = dict(state=exc.state, error=str(exc))
    return result


_RECOVER_OPS = {
    "inspect": "contribution-inspect",
    "contribution-inspect": "contribution-inspect",
    "reconcile": "contribution-reconcile",
    "contribution-reconcile": "contribution-reconcile",
    "retry": "contribution-retry",
    "contribution-retry": "contribution-retry",
    "compact": "contribution-compact",
    "contribution-compact": "contribution-compact",
}


def op_recover(session, event):
    """Hook only names the exact core CLI. No network inside the Hook budget."""
    consume_slash(session, event, "recover")
    if not _configured():
        raise ValueError("MindIE is not configured")
    from paths import engine_config_path
    engine = str(engine_config_path())
    python = load_adapter_config()["python"]
    argv = shlex.split(event.get("command_args") or "")
    batch = None
    operation = "contribution-inspect"
    args = list(argv)
    while args:
        item = args.pop(0)
        if item in {"--batch", "--batch-id"} and args:
            batch = args.pop(0)
        elif item in _RECOVER_OPS:
            operation = _RECOVER_OPS[item]
        elif item.startswith("--") and item[2:] in _RECOVER_OPS:
            operation = _RECOVER_OPS[item[2:]]
    if not batch:
        return dict(
            run_outside_hook=True,
            hint="Use a batch_id from diagnostics.contributions with --batch ID and one of inspect|reconcile|retry|compact; no recovery was started.",
            diagnostics=_diagnostics(session),
            note=(
                "Unknown writes: contribution-reconcile. "
                "Retry only a proven failed batch. Compact only a confirmed batch."
            ),
        )
    command = [
        python,
        "-m",
        "mindie_knowledge.loop.cli",
        operation,
        "--config",
        engine,
        "--batch",
        batch,
    ]
    notes = {
        "contribution-inspect": "Read-only. Does not write or retry.",
        "contribution-reconcile": "Bounded remote inspect. Unknown stays unknown.",
        "contribution-retry": "Only a proven failed batch. Reconcile unknown writes first.",
        "contribution-compact": "Only an already confirmed batch.",
    }
    return dict(
        run_outside_hook=True,
        operation=operation,
        command=command,
        command_line=" ".join(shlex.quote(item) for item in command),
        note=notes[operation],
    )


def op_reporting(session, event, command):
    """Compatibility entrypoints never make a separate reporting choice."""
    consume_slash(session, event, command)
    if command == "reporting-status":
        from diagnostic_support import reporting_status
        return reporting_status()
    return _reporting_guidance()


def dispatch_event(event: dict) -> dict:
    if is_subagent(event):
        raise ValueError("subagent tasks are not activated")
    session = require_session(event.get("session_id"))
    require_absolute(event.get("cwd"), "cwd")
    require_absolute(event.get("transcript_path"), "transcript_path")
    command = slash_command(event)
    if command in {"reporting-status", "reporting-enable", "reporting-disable"}:
        return op_reporting(session, event, command)
    if command == "init":
        return op_init(session, event)
    if command == "status":
        return op_status(session, event)
    if command == "deactivate":
        return op_deactivate(session, event)
    if command == "sharing-enable":
        return op_sharing_enable(session, event)
    if command == "sharing-disable":
        return op_sharing_disable(session, event)
    if command == "recover":
        return op_recover(session, event)
    raise ValueError("unknown MindIE entry operation")


def render_context(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
