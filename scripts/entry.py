"""Slash-command operations invoked from UserPromptExpansion.

Activation is this hook only. MCP status cannot activate or enable sharing.
Sharing-enable destination is the native command_args, not model arguments.
"""

from __future__ import annotations

import json
import os
import shlex
import stat
from pathlib import Path

from entry_state import consume_prompt, first_use, set_first_use, three_choices
from identity import is_subagent, require_absolute, require_session, slash_command
from paths import config_path, engine_config_path, load_adapter_config


def _configured() -> bool:
    return config_path().is_file()


def _parse_sharing(text):
    try:
        argv = shlex.split(text or "")
    except ValueError:
        raise ValueError("could not parse sharing-enable arguments")
    repository = None
    roots = []
    branch = "main"
    visibility = None
    account = None
    fork = None
    args = list(argv)
    while args:
        item = args.pop(0)
        if item in {"--repository", "--community-repository"} and args:
            repository = args.pop(0)
        elif item in {"--project-root", "--community-project-root"} and args:
            roots.append(str(Path(args.pop(0)).expanduser().resolve()))
        elif item in {"--branch", "--community-branch"} and args:
            branch = args.pop(0)
        elif item in {"--visibility", "--community-visibility"} and args:
            visibility = args.pop(0)
        elif item in {"--account", "--community-account"} and args:
            account = args.pop(0)
        elif item in {"--fork", "--community-fork"} and args:
            fork = args.pop(0)
        elif item.startswith("--"):
            raise ValueError("unknown sharing flag: " + item)
    if not repository or not roots or visibility != "public" or not account:
        raise ValueError(
            "sharing-enable requires --repository owner/repo, "
            "--account USER, --project-root /absolute/path, and --visibility public"
        )
    return dict(
        repository=repository,
        project_roots=roots,
        branch=branch,
        visibility=visibility,
        account=account,
        fork=fork,
    )


def _prompt_id(event) -> str:
    value = event.get("prompt_id")
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("native prompt_id is missing")
    return value


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
    except Exception:
        token = None
    if token is None:
        return consume_prompt(ident)
    return gate().claim(session, "plugin_command", ident[:256], token=token) is True


def _diagnostics(session):
    """One bounded read of existing state; never activate or repair."""
    try:
        from mindie_knowledge.loop.diagnostics import snapshot

        return snapshot(str(engine_config_path()), session=session)
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
    """Small JSON object from a regular file.

    Missing is "missing". FIFO, socket, device, and malformed files are
    "unavailable" and are not read as a blocking stream. A symlink is
    followed only when its target is a regular file.
    """
    try:
        descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unavailable", None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _META_LIMIT:
            return "unavailable", None
        blob = os.read(descriptor, _META_LIMIT + 1)
    except OSError:
        return "unavailable", None
    finally:
        os.close(descriptor)
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


def _launcher_command(adapter, operation, current):
    """Absolute retained launcher command from an already-read current tuple."""
    try:
        from genstate import launch_dir

        if not isinstance(current, dict):
            return None
        sha = current.get("sha") if isinstance(current.get("sha"), str) and current.get("sha") else "bootstrap"
        python = current.get("python")
        if not isinstance(python, str) or not python:
            return None
        launcher = launch_dir(adapter) / sha / "mindie_launch.py"
        if not stat.S_ISREG(launcher.stat().st_mode):
            return None
        config_value = adapter.get("base_config") if isinstance(adapter.get("base_config"), str) else str(config_path())
        config_file = str(Path(config_value).expanduser().resolve())
        return shlex.join([python, str(launcher.resolve()), "--config", config_file, "updater", operation])
    except OSError:
        return None
    except Exception:
        return None


def _updater_view():
    """Read updater failure state. Does not check the network or mutate it."""
    try:
        from genstate import current_path, failed_path, status_path

        adapter = load_adapter_config()
        if not isinstance(adapter, dict):
            return {"status": "unavailable"}
        failed_state, failed = _read_regular_json(failed_path(adapter))
        status_state, status = _read_regular_json(status_path(adapter))
        current_state, current = _read_regular_json(current_path(adapter))
    except Exception:
        return {"status": "unavailable"}
    if "unavailable" in {failed_state, status_state, current_state}:
        return {"status": "unavailable"}
    failed = failed or {}
    status = status or {}
    current = current or {}
    if not isinstance(failed, dict):
        failed = {}
    if not isinstance(status, dict):
        status = {}
    klass = failed.get("failure_class") if isinstance(failed.get("failure_class"), str) else None
    if klass is None and isinstance(status.get("resolve_failure_class"), str):
        klass = status.get("resolve_failure_class")
    phase = failed.get("phase") if isinstance(failed.get("phase"), str) else None
    retryable = klass in {"temporary_network", "rate_limited"} and phase != "package-refresh"
    certificate = klass == "certificate"
    actionable = klass in {"authentication", "permission", "hook_trust", "certificate"}
    quarantined = klass in {"resolver", "bad_content"} or failed.get("quarantined") is True
    unknown = bool(failed.get("error")) and not retryable and not actionable and not quarantined
    nxt = _number(failed.get("next_retry_at"))
    if nxt is None:
        nxt = _number(status.get("next_retry_at"))
    active = bool(
        failed.get("error") or klass or failed.get("quarantined") or phase or nxt is not None
        or status.get("error") or status.get("resolve_wait")
        or status.get("result") in {
            "failed", "check-failed", "action-required",
            "suppressed-known-failed", "retry-waiting",
        }
    )
    if not active:
        if isinstance(failed.get("sha"), str) or failed.get("history") or failed.get("count"):
            view = {
                "status": "recovered",
                "recovery": "recovered",
                "active_failure": False,
                "note": "no active updater failure; history remains in updater status",
            }
            if isinstance(status.get("result"), str):
                view["result"] = status["result"][:80]
            if isinstance(status.get("current_sha"), str):
                view["current_sha"] = status["current_sha"][:64]
            return view
        if not isinstance(status.get("result"), str):
            return None
        view = {"result": status["result"][:80]}
        if isinstance(status.get("current_sha"), str):
            view["current_sha"] = status["current_sha"][:64]
        command = _launcher_command(adapter, "status", current)
        if command:
            view["command"] = command
        return view
    view = {"active_failure": True}
    if isinstance(status.get("result"), str):
        view["result"] = status["result"][:80]
    if isinstance(status.get("current_sha"), str):
        view["current_sha"] = status["current_sha"][:64]
    if isinstance(failed.get("sha"), str):
        view["failed_sha"] = failed["sha"][:64]
    error = _bounded_text(failed.get("error"), 200) or _bounded_text(status.get("error"), 200)
    if error:
        view["error"] = error
    if klass:
        view["failure_class"] = klass[:40]
    if nxt is not None:
        view["next_retry_at"] = nxt
    if isinstance(failed.get("count"), int) and not isinstance(failed.get("count"), bool):
        view["attempts"] = failed["count"]
    if isinstance(failed.get("first_failure_at"), (int, float)) and not isinstance(failed.get("first_failure_at"), bool):
        view["first_failure_at"] = failed["first_failure_at"]
    if phase:
        view["phase"] = phase[:40]
    if retryable:
        view["recovery"] = "automatic"
        view["note"] = "scheduled checks retry this temporary failure"
        operation = "status"
    elif certificate:
        view["recovery"] = "action_required"
        view["note"] = "certificate trust needs repair; scheduled checks retry after backoff"
        operation = "status"
    elif actionable:
        view["recovery"] = "action_required"
        view["note"] = "credentials or host permission need attention"
        operation = "status"
    elif quarantined or unknown or phase == "package-refresh":
        view["recovery"] = "manual"
        view["note"] = "manual recovery is required for this revision"
        operation = "recover"
    else:
        operation = "status"
    command = _launcher_command(adapter, operation, current)
    if command:
        view["command"] = command
    return view


def _knowledge_status_payload(session=None):
    if not _configured():
        payload = three_choices()
        if payload["first_use"] in {"read-only", "later", "contribute"}:
            payload["repeat"] = True
            payload["choices"] = []
        return payload
    from sharing import public_status

    try:
        sharing_view, choice = public_status(), first_use()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return dict(
            configured=None,
            sharing=dict(enabled=None, status="unavailable"),
            error=dict(stage="local_settings", type=type(exc).__name__),
            config=str(config_path()),
            diagnostics=_diagnostics(session),
            hint="Inspect the existing adapter and community configuration. Native tools and independent SSH remain available; no setup or retry was started.",
        )
    payload = dict(configured=True, sharing=sharing_view, first_use=choice)
    diagnostics = _diagnostics(session)
    payload["diagnostics"] = diagnostics
    if session:
        admission = diagnostics.get("admission") or {}
        state = admission.get("status", "unavailable")
        payload["this_session"] = dict(
            status=state,
            activated=state in {"active", "paused"},
            enabled=state == "active" and admission.get("enabled") is True,
            failures=admission.get("failures"),
            project_root=admission.get("project_root"),
            paused=state == "paused",
        )
    stored = payload.get("first_use")
    if stored in {"read-only", "later", "contribute"}:
        payload["repeat"] = True
        payload["choices"] = []
    elif not payload["sharing"].get("enabled"):
        extra = three_choices()
        payload["choices"] = extra["choices"]
        payload["note"] = extra["note"]
        payload["setup"] = extra["setup"]
        payload["hint"] = (
            "Sharing is off: no Stop capture or organizer. "
            "Knowledge retrieval works after /mindie-agent:init."
        )
    update = _updater_view()
    if update:
        payload["update"] = update
    return payload


def status_payload(session=None):
    """Read-only. Reporting is independent of knowledge setup and activation."""
    import diagnostic_support

    payload = dict(_knowledge_status_payload(session))
    payload["reporting"] = diagnostic_support.reporting_status()
    if payload["reporting"].get("status") == "not_configured":
        payload["reporting_choice"] = diagnostic_support.reporting_hint()
    return payload


def _project_root(cwd):
    if isinstance(cwd, str) and os.path.isabs(cwd):
        return str(Path(cwd).resolve())
    raise ValueError("native cwd is required to activate; env session vars are not used")


def _init_choice_from_native(arguments):
    text = (arguments or "").strip()
    if not text:
        return None
    token = text.split()[0]
    if token in {"read-only", "later"}:
        return token
    return None


def _apply_native_choice(payload, native_choice):
    if native_choice is None:
        return payload
    stored = set_first_use(native_choice)
    payload["first_use"] = stored
    payload["choices"] = []
    payload["repeat"] = True
    return payload


def _sharing_on() -> bool:
    try:
        from sharing import public_status

        return public_status().get("enabled") is True
    except Exception:
        return False


def _prepare_capture_service():
    """Contribution-on only. Detached, model-free, not waited in the Hook."""
    if not _sharing_on():
        return "off"
    try:
        from knowledge_service import prepare_service

        return prepare_service()
    except Exception as exc:
        return f"prepare-failed:{type(exc).__name__}"


def _configured_init_activation(session, cwd, ident):
    from admission import activate, gate

    root = _project_root(cwd)
    try:
        lease = activate(session, project_root=root, root_session=session)
    except ValueError as exc:
        text = str(exc)
        if "paused" not in text.lower():
            raise
        payload = status_payload(session)
        payload["activation"] = dict(
            session=session,
            enabled=False,
            paused=True,
            hint=text[:300],
        )
        return payload
    claimed = gate().claim(session, "plugin_command", ident[:256], token=lease["token"]) is True
    payload = status_payload(session)
    if not claimed:
        payload["already"] = True
    paused = bool(lease.get("paused"))
    service = "off"
    if not paused and _sharing_on():
        service = _prepare_capture_service()
    payload["activation"] = dict(
        session=lease["session"],
        enabled=bool(lease.get("enabled")) and not paused,
        failures=lease.get("failures", 0),
        project_root=lease["project_root"],
        paused=paused,
        service=service,
    )
    return payload


def op_init(session, event):
    slash_command(event)
    native_choice = _init_choice_from_native(event.get("command_args"))
    ident = f"init:{_prompt_id(event)}"
    cwd = event.get("cwd")
    if not _configured():
        if not consume_prompt(ident):
            payload = status_payload(session)
            payload["already"] = True
            return payload
        if native_choice is None:
            payload = status_payload(session)
            payload["runtime"] = "unconfigured"
            return payload
        return _apply_native_choice(status_payload(session), native_choice)
    payload = _configured_init_activation(session, cwd, ident)
    return _apply_native_choice(payload, native_choice)


def op_status(session, event):
    consume_slash(session, event, "status")
    return status_payload(session)


def op_deactivate(session, event):
    consume_slash(session, event, "deactivate")
    if not _configured():
        return dict(session=session, deactivated=False, configured=False)
    from admission import deactivate

    return dict(session=session, deactivated=bool(deactivate(session)))


def op_sharing_enable(session, event):
    first = consume_slash(session, event, "sharing-enable")
    if not first:
        payload = status_payload(session)
        payload["already"] = True
        return payload
    if not _configured():
        raise ValueError("MindIE is not configured; run scripts/setup.py first")
    import sharing as sharing_mod

    parsed = _parse_sharing(event.get("command_args") or "")
    result = sharing_mod.write_enabled(**parsed)
    set_first_use("contribute")
    result = dict(result)
    result["service"] = _prepare_capture_service()
    return result


def op_sharing_disable(session, event):
    consume_slash(session, event, "sharing-disable")
    if not _configured():
        return dict(configured=False, enabled=False)
    import sharing as sharing_mod

    return sharing_mod.write_disabled()


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
    """Native slash only. Does not ensure the reporter inside the Hook."""
    first = consume_slash(session, event, command)
    import diagnostic_support

    if command == "reporting-status":
        return diagnostic_support.reporting_status()
    if not first:
        return dict(diagnostic_support.reporting_status(), already=True)
    if not _configured():
        raise ValueError("MindIE is not configured; run scripts/setup.py first")
    python = load_adapter_config()["python"]
    return diagnostic_support.configure_reporting(command == "reporting-enable", python)


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
