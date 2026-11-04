#!/usr/bin/env python3
"""Prepare this native release's fixed runtime binding and existing shared state.

[Role]
Validates the candidate runtime, prepares the knowledge store and capture
configuration, and records the atomic `installation.json` binding for
`mindie-agent-cc`.

[Invariants]
- Plugin installation, update, and removal belong to Claude Code's Git
  marketplace; no mutable code-selection registry lives here.
- Preserves existing user consent and community sharing settings across setups.

[Timeouts & Locks]
- Serializes cold preparation separately from short profile admission locks.

[Side-Effect Policy]
- Writes MindIE-owned adapter, engine, community, and `installation.json` files;
  never touches `~/.claude`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
import time
import tempfile

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from install_state import (
    FACADE_PROTOCOL, INSTALLATION_SCHEMA, InstallationUnavailable, LockTimeout,
    OperationLock, atomic_write, read_binding,
)
from maintenance import sync_public_feed
from paths import config_path as default_config_path, service_launch_context
from product_contract import identity, official_feeds, publication_feed

def community_settings(args, parser):
    selected = any(
        getattr(args, key)
        for key in (
            "community_repository",
            "community_project_root",
            "community_account",
            "community_fork",
            "community_visibility",
        )
    )
    if not selected:
        if args.community_branch is not None:
            parser.error("--community-branch requires --community-repository")
        return None
    if not args.community_repository:
        parser.error("community sharing requires --community-repository owner/repo")
    if not args.community_project_root:
        parser.error("community sharing requires at least one --community-project-root")
    if args.community_visibility != "public":
        parser.error("community sharing requires --community-visibility public")
    roots = []
    for root in args.community_project_root:
        canonical = str(Path(root).expanduser().resolve())
        if not Path(canonical).is_dir():
            parser.error("community project root does not exist: " + canonical)
        if canonical not in roots:
            roots.append(canonical)
    data = dict(
        schema="mindie-community-config/1",
        enabled=True,
        generation=secrets.token_hex(16),
        enabled_at=time.time(),
        repository=args.community_repository,
        branch=args.community_branch or "main",
        project_roots=roots,
        idle_seconds=300,
        visibility="public",
    )
    if args.community_account:
        data["account"] = args.community_account
    if args.community_fork:
        data["fork"] = args.community_fork
    return data

def write_community(path, community, consent_config=None, python=None):
    from community_config import configure
    request = dict(community) if community is not None else None
    if request is not None:
        request['consent_config'] = consent_config or str(Path(path).with_name('mindie-consent.json').resolve())
    result = configure(path, request, python)
    if community is None and 'consent_config' not in result:
        result = configure(path, dict(consent_config=consent_config or str(Path(path).with_name('mindie-consent.json').resolve())), python)
    return 'enabled' if result['enabled'] else 'off'


def _read(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("installation configuration must be one object")
    return value


def component_manifest():
    path = Path(sys.prefix).parent.parent / "components.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def candidate_identity(source):
    return identity(source, components=component_manifest())


def validate_candidate(source):
    from candidate_validate import validate
    return validate(source, candidate_identity(source))


def prepare_binding(source, config=None, *, root=None, domain="vllm-ascend",
                    no_public_feed=False, claude_config_dir=None, community=None):
    """Prepare immutable candidate bytes; only an empty profile is activated here."""
    from bootstrap import runtime_key
    from capture_config import prepare, prepare_store
    from harness import require_engine_support
    from importlib.metadata import version
    from mindie_knowledge.component_bindings import managed_commands, rebind_commands, validate_command_floor

    source = Path(source).resolve()
    config = Path(config or default_config_path()).expanduser().resolve()
    os.environ["MINDIE_CC_CONFIG"] = str(config)
    key = runtime_key(source)
    components = component_manifest()
    if components:
        key = hashlib.sha256((key + components["lock_sha256"]).encode()).hexdigest()
    domain_root = Path(root or Path(os.environ.get("XDG_DATA_HOME")
                                  or Path.home() / ".local/share") / "mindie-agent/cc")
    domain_root = domain_root.expanduser().resolve()
    initial = dict(schema=INSTALLATION_SCHEMA, facade_protocol=FACADE_PROTOCOL,
                   state_dir=str(domain_root / "adapter-state"),
                   community_config=str(config.with_name("mindie-community.json")),
                   base_config=str(config))
    selected = read_binding(config) if config.exists() else None
    adapter = selected or initial
    if adapter["base_config"] != str(config):
        raise InstallationUnavailable("installation profile identity differs")
    # Native SessionStart and both MCP processes can arrive together. A
    # candidate is prepared once; no reader sees its store half initialized.
    with OperationLock(adapter).preparation(timeout=30):
        selected = read_binding(config) if config.exists() else None
        adapter = selected or initial
        commands = dict(adapter.get("component_commands", {}))
        command_versions = {name: {"version": version(name)}
                            for name, _python, _module in managed_commands(adapter).values()}
        validate_command_floor(adapter, command_versions)
        candidate_commands = rebind_commands(adapter, sys.executable, command_versions)
        binding = Path(adapter["state_dir"]) / "installations" / key
        adapter_file, engine_file = binding / "cc.adapter.json", binding / "cc.engine.json"
        if adapter_file.exists() and selected is not None:
            prepared = read_binding(adapter_file)
            if (prepared != selected and (
                    prepared.get('domain_source_configs') != (selected.get('domain_configs') or {})
                    or prepared.get('component_source_commands', {}) != commands)):
                routes = json.dumps(dict(domains=selected.get('domain_configs') or {},
                                         commands=commands), sort_keys=True).encode()
                binding = binding.with_name(key + '-' + hashlib.sha256(routes).hexdigest()[:16])
                adapter_file, engine_file = binding / 'cc.adapter.json', binding / 'cc.engine.json'
        if adapter_file.exists():
            value = read_binding(adapter_file)
            from candidate_validate import validate
            validate(source, candidate_identity(source), value["product_validation"])
            if (value["python"] != sys.executable or value["runtime_scripts"] != str(HERE)
                    or value["base_config"] != str(config)):
                raise InstallationUnavailable("prepared candidate identity differs")
            engine = _read(value["engine_config"])
            require_engine_support(engine, source=source)
        else:
            validation = validate_candidate(source)
            if selected is None:
                engine = dict(root=str(domain_root), domain=domain,
                              admission_path=str(domain_root / "admission.sqlite3"))
            else:
                engine = _read(selected["engine_config"])
            engine.update(transcript_adapter=str(HERE / "transcript.py"),
                          **prepare(sys.executable, HERE),
                          community_config=adapter["community_config"],
                          product_validation=validation,
                         component_source=str(source) if components else None)
            feed = publication_feed(validation, domain=domain)
            if selected is None and feed is not None and not no_public_feed:
                engine["feeds"] = [feed]
            value = dict(adapter, python=sys.executable, runtime_scripts=str(HERE),
                         adapter_config=str(adapter_file), engine_config=str(engine_file),
                         product_validation=validation,
                         component_source=str(source) if components else None)
            value['domain_source_configs'] = dict(adapter.get('domain_configs') or {})
            value["component_source_commands"] = commands
            if commands:
                value["component_commands"] = candidate_commands
            from diagnostic_support import reporting_config_path
            value["diagnostics_context"] = {
                "MINDIE_DIAGNOSTICS_CONFIG": str(reporting_config_path(config)),
            }
            if claude_config_dir:
                value["claude_config_dir"] = str(Path(claude_config_dir).expanduser().resolve())
            engine["service_launch_context"] = service_launch_context(adapter_file, value)
            value["claude_config_dir"] = engine["service_launch_context"]["paths"]["CLAUDE_CONFIG_DIR"]
            value["diagnostics_context"]["MINDIE_DIAGNOSTICS_ROOT"] = (
                engine["service_launch_context"]["paths"]["MINDIE_DIAGNOSTICS_ROOT"])
            require_engine_support(engine, source=source)
            if selected is None:
                prepare_store(sys.executable, HERE, engine)
            from capture_config import prepare_domains
            from consent import consent_path
            value['domain_configs'] = prepare_domains(
                sys.executable, value, engine, config_directory=str(binding / 'domains'),
                authority_directory=str(config.parent / 'knowledge-domains'),
                consent_config=str(consent_path(config)),
                public_feeds=not no_public_feed if selected is None else bool(engine.get('feeds')),
                domains=official_feeds())
            _publish_bytes(engine_file, (json.dumps(engine, indent=2) + "\n").encode())
            _publish_bytes(adapter_file, (json.dumps(value, indent=2) + "\n").encode())

        # Only publication changes take exclusive admission. SessionStart holds
        # shared admission during maintenance, so already prepared MCP entries
        # must not wait for that maintenance to finish.
        current = read_binding(config) if config.exists() else None
        if current is None or community is not None:
            with OperationLock(adapter).exclusive(timeout=30):
                current = read_binding(config) if config.exists() else None
                if current is None:
                    from release_update import disable_unfiltered_updates
                    disable_unfiltered_updates(value)
                    write_community(Path(value["community_config"]), community, python=sys.executable)
                    atomic_write(config, value)
                    current = value
                elif community is not None:
                    write_community(Path(current["community_config"]), community, python=sys.executable)
        status = "ready" if current == value else "prepared"
    os.environ["MINDIE_CC_CONFIG"] = str(adapter_file)
    result = dict(config=str(adapter_file), base_config=str(config), status=status)
    if community is not None:
        from consent import record_choice
        from diagnostic_support import activate_reporting
        record_choice("contribute", config)
        result["reporting"] = activate_reporting(config)
        if result["reporting"]["status"] != "running":
            result["configuration_status"] = result["status"]
            result["status"] = "degraded"
    return result


def activate_binding(candidate_file, *, observation_seconds=30.0):
    """Switch a native-installed candidate only after idle retirement and readiness.

    The caller verifies native installation facts. This operation never downloads,
    retries a native mutation, cancels business work, or rewrites saved choices.
    """
    from mindie_knowledge.loop.cli import config_at
    from mindie_knowledge.loop.handoff import _probe
    from mindie_knowledge.loop.lifecycle import inspect_retirement, retire_service

    candidate = read_binding(candidate_file)
    config = Path(candidate["base_config"])
    started = time.monotonic()
    deadline = started + observation_seconds

    def remaining():
        return max(0.0, deadline - time.monotonic())

    def incomplete(stage, **facts):
        return dict(status="incomplete", stage=stage, switched=False,
                    seconds=time.monotonic() - started, **facts)

    try:
        with OperationLock(candidate).exclusive(timeout=0):
            previous = read_binding(config)
            if previous == candidate:
                return dict(status="current", switched=False)
            if ('domain_source_configs' in candidate
                    and (previous.get('domain_configs') or {}) != candidate['domain_source_configs']):
                return dict(status='deferred', stage='domain_routes_changed', switched=False)
            if ('component_source_commands' in candidate
                    and previous.get('component_commands', {}) != candidate['component_source_commands']):
                return dict(status='deferred', stage='component_commands_changed', switched=False)
            for key in ("base_config", "state_dir", "community_config"):
                if previous[key] != candidate[key]:
                    raise InstallationUnavailable("candidate changes installation data identity")
            engine = config_at(candidate["engine_config"])
            if remaining() <= 0:
                return incomplete("new_readiness", service="not_observed")
            state = _probe(engine, remaining(), config_path=candidate["engine_config"])
            if remaining() <= 0:
                return incomplete("new_readiness", service=state)
            recorded = inspect_retirement(previous["engine_config"])
            if state == "ready" and recorded is not None and recorded["status"] == "retired":
                # A previous switch may have started this exact owner before
                # the canonical write failed. Its consumer is not the old one.
                retirement = dict(idle=True, retirement=recorded)
            else:
                retirement = retire_service(
                    previous["engine_config"],
                    control_timeout=remaining(),
                )
                if retirement.get("idle") is not True:
                    return dict(status="deferred", stage="old_owner", retirement=retirement)
                if retirement.get("cleanup_failed"):
                    return dict(status="incomplete", stage="old_retirement", retirement=retirement)
                if remaining() <= 0:
                    return incomplete("new_readiness", retirement=retirement)
                state = _probe(engine, remaining(), config_path=candidate["engine_config"])
            if remaining() <= 0:
                return incomplete("new_readiness", service=state, retirement=retirement)
            while True:
                if remaining() <= 0:
                    return incomplete("new_readiness", service=state, retirement=retirement)
                state = _probe(engine, remaining(), config_path=candidate["engine_config"])
                if remaining() <= 0:
                    return incomplete("new_readiness", service=state, retirement=retirement)
                if state in {"ready", "absent"}:
                    from mindie_knowledge.loop.domain_catalog import retire_changed, restore_changed
                    domains = retire_changed(previous, candidate, timeout=remaining())
                    if domains['status'] != 'retired':
                        return dict(status=domains['status'], stage='domain_retirement',
                                    domains=domains, switched=False)
                    try:
                        atomic_write(config, candidate)
                    except TimeoutError:
                        raise
                    except OSError as exc:
                        return dict(status="incomplete", stage="canonical_publish",
                                    service="ready", retirement=retirement,
                                    domain_restoration=restore_changed(domains, timeout=remaining()),
                                    error_type=type(exc).__name__, switched=False)
                    return dict(status="activated", switched=True,
                                seconds=time.monotonic() - started, retirement=retirement)
                if state not in {"absent", "starting"}:
                    return dict(status="incomplete", stage="new_owner", service=state,
                                retirement=retirement)
                time.sleep(min(0.05, remaining()))
    except LockTimeout:
        return dict(status="deferred", stage="active_operation", switched=False)


def _publish_bytes(path, value):
    """Publish complete immutable bytes without replacing an existing entry."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != value:
            raise ValueError("immutable installation entry already binds different bytes")
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".entry-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != value:
                raise ValueError("immutable installation entry already binds different bytes")
    finally:
        Path(temporary).unlink()


def publish_entry(path, adapter_file):
    """Write once: the native version in the Hook binds this exact runtime."""
    adapter = _read(adapter_file)
    diagnostics = dict(adapter.get("diagnostics_context", {}))
    diagnostics.setdefault("MINDIE_DIAGNOSTICS_CONFIG",
                           str(Path(adapter["base_config"]).parent / "diagnostics.json"))
    value = (
        "import os, sys\n"
        f"os.environ['MINDIE_CC_CONFIG'] = {str(adapter_file)!r}\n"
        f"os.environ.update({diagnostics!r})\n"
        "os.environ.pop('PYTHONPATH', None)\n"
        f"sys.path.insert(0, {str(HERE)!r})\n"
        "from bounded import exec_or_wait\n"
        f"exec_or_wait([{sys.executable!r}, '-I', "
        f"{str(HERE / 'mindie_launch.py')!r}, *sys.argv[1:]], dict(os.environ))\n"
    )
    _publish_bytes(Path(path), value.encode("utf-8"))


_REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]{1,128}\Z")
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}\Z")


def resolve_github_identity(repository, *, account=None, fork=None):
    """Best-effort GitHub login and fork resolution via `gh` when not explicitly passed."""
    if fork or not shutil.which("gh"):
        return account, fork
    import bounded

    repo_name = repository.split("/", 1)[1]
    try:
        if not account:
            login = bounded.run(
                ["gh", "api", "--hostname", "github.com", "user", "--jq", ".login"],
                timeout=5.0,
            ).strip()
            if login and "/" not in login and _NAME_RE.fullmatch(login):
                account = login
        if account and not fork:
            try:
                found = bounded.run(
                    [
                        "gh",
                        "api",
                        "--hostname",
                        "github.com",
                        f"repos/{account}/{repo_name}",
                        "--jq",
                        ".full_name",
                    ],
                    timeout=5.0,
                ).strip()
                if _REPOSITORY_RE.fullmatch(found):
                    fork = found
            except Exception:
                bounded.run(
                    ["gh", "repo", "fork", repository, "--clone=false"],
                    timeout=10.0,
                )
                fork = f"{account}/{repo_name}"
    except Exception:
        pass
    return account, fork


def _read_consent_json(path):
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return dict(state="missing", choice=None, choice_revision=None)
    except TimeoutError:
        raise
    except OSError:
        return dict(state="unreadable", choice=None, choice_revision=None)
    try:
        data = json.loads(raw)
    except ValueError:
        return dict(state="corrupt", choice=None, choice_revision=None)
    if not isinstance(data, dict) or data.get("schema") != "mindie-consent/1":
        return dict(state="corrupt", choice=None, choice_revision=None)
    choice = data.get("choice")
    if choice is not None and choice not in {"contribute", "read-only", "later", "disabled"}:
        return dict(state="corrupt", choice=None, choice_revision=None)
    rev = data.get("choice_revision")
    if rev is not None and (
        not isinstance(rev, str)
        or len(rev) != 32
        or any(c not in "0123456789abcdef" for c in rev)
    ):
        return dict(state="corrupt", choice=None, choice_revision=None)
    return dict(state="ok", choice=choice, choice_revision=rev)


def sharing_status(config=None):
    """Read-only fast path for `setup.py sharing --status`."""
    config = Path(config or default_config_path()).expanduser().resolve()
    try:
        import consent

        saved = consent.load(config)
    except ModuleNotFoundError:
        saved = _read_consent_json(config.parent / "mindie-consent.json")
    consent_state = saved.get("state") or "missing"
    consent_choice = saved.get("choice") if consent_state == "ok" else None
    choice_revision = saved.get("choice_revision") if consent_state == "ok" else None
    comm_path = config.with_name("mindie-community.json")
    if config.is_file():
        try:
            bound = _read(config)
            if isinstance(bound.get("community_config"), str):
                comm_path = Path(bound["community_config"])
        except Exception:
            pass
    comm_state = "missing"
    comm = {}
    try:
        raw = comm_path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or parsed.get("schema") != "mindie-community-config/1":
            raise ValueError("community settings schema mismatch")
        if not isinstance(parsed.get("enabled"), bool):
            raise ValueError("community enabled flag must be boolean")
        comm = parsed
        comm_state = "ok"
    except FileNotFoundError:
        comm_state = "missing"
    except TimeoutError:
        raise
    except ValueError:
        comm_state = "corrupt"
    except OSError:
        comm_state = "unreadable"
    try:
        from diagnostic_support import reporting_config_path

        diag_path = reporting_config_path(config)
    except Exception:
        diag_path = config.parent / "diagnostics.json"
    diag_enabled = False
    try:
        diag_raw = json.loads(diag_path.read_text(encoding="utf-8"))
        if isinstance(diag_raw, dict):
            diag_enabled = bool(
                diag_raw.get("decision") == "enabled" or diag_raw.get("enabled") is True
            )
    except TimeoutError:
        raise
    except (OSError, ValueError):
        diag_enabled = False
    repair_required = (
        consent_state in {"corrupt", "unreadable"}
        or comm_state in {"corrupt", "unreadable"}
    )
    sharing_enabled = bool(
        comm_state == "ok"
        and comm.get("enabled") is True
        and consent_state == "ok"
        and consent_choice == "contribute"
    )
    reporting_enabled = bool(
        diag_enabled
        and consent_state == "ok"
        and consent_choice == "contribute"
        and bool(choice_revision)
    )
    if repair_required:
        needs_onboarding_prompt = False
    else:
        needs_onboarding_prompt = bool(
            consent_state == "missing"
            or (
                consent_state == "ok"
                and (
                    consent_choice not in {"contribute", "disabled"}
                    or (consent_choice == "contribute" and not choice_revision)
                )
            )
        )
    return dict(
        schema="mindie-sharing-status/1",
        harness="claude-code",
        consent_state=consent_state,
        consent_choice=consent_choice,
        choice_revision=choice_revision,
        sharing_enabled=sharing_enabled,
        community_enabled=sharing_enabled,
        reporting_enabled=reporting_enabled,
        repository=comm.get("repository") or "mindie-agent/knowledge-vllm-ascend",
        fork=comm.get("fork"),
        account=comm.get("account"),
        project_roots=list(comm.get("project_roots") or []),
        needs_onboarding_prompt=needs_onboarding_prompt,
        repair_required=repair_required,
    )


def sharing_main(argv):
    parser = argparse.ArgumentParser(prog="setup.py sharing", description="Manage community sharing and fault reporting.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--enable", action="store_true")
    mode.add_argument("--disable", action="store_true")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--project-root", action="append", dest="project_root")
    parser.add_argument("--community-project-root", action="append", dest="community_project_root")
    parser.add_argument("--fork", dest="fork")
    parser.add_argument("--community-fork", dest="community_fork")
    parser.add_argument("--account", dest="account")
    parser.add_argument("--community-account", dest="community_account")
    parser.add_argument("--repository", dest="repository")
    parser.add_argument("--community-repository", dest="community_repository")
    parser.add_argument("--branch", dest="branch")
    parser.add_argument("--community-branch", dest="community_branch")
    parser.add_argument("--visibility", dest="visibility")
    parser.add_argument("--community-visibility", dest="community_visibility")
    args = parser.parse_args(argv)
    config = Path(args.config).expanduser().resolve()
    if args.status:
        print(json.dumps(sharing_status(config), indent=2))
        return
    import consent
    from community_config import configure as configure_community
    from diagnostic_support import activate_reporting

    saved = consent.load(config)
    if saved["state"] in {"corrupt", "unreadable"}:
        parser.error("saved setup state is damaged; configuration was not changed")
    selected = read_binding(config) if config.exists() else None
    python = (selected.get("python") if selected else None) or sys.executable
    comm_path = Path(selected["community_config"]) if selected and selected.get("community_config") else config.with_name("mindie-community.json")
    old = {}
    if comm_path.exists():
        try:
            old = json.loads(comm_path.read_text(encoding="utf-8"))
        except TimeoutError:
            raise
        except (OSError, ValueError):
            parser.error("community settings file is damaged or unreadable; bytes preserved")
        if not isinstance(old, dict) or old.get("schema") != "mindie-community-config/1":
            parser.error("community settings file is not a valid mindie-community-config/1 document")
    consent_file = str(consent.consent_path(config).resolve())
    if args.disable:
        consent.record_choice("disabled", config)
        disabled_req = dict(
            schema="mindie-community-config/1",
            enabled=False,
            repository=old.get("repository"),
            branch=old.get("branch") or "main",
            project_roots=list(old.get("project_roots") or []),
            idle_seconds=old.get("idle_seconds", 300),
            consent_config=consent_file,
        )
        comm_path.parent.mkdir(parents=True, exist_ok=True)
        merged = configure_community(comm_path, disabled_req, python=python)
        reporting = activate_reporting(config)
        print(
            json.dumps(
                dict(
                    status="configured" if reporting["status"] in {"running", "disabled"} else "degraded",
                    configuration_status="configured",
                    reporting=reporting,
                    community_config=str(comm_path),
                    sharing="disabled",
                    sharing_choice="disabled",
                    repository=merged.get("repository"),
                    project_roots=merged.get("project_roots", []),
                ),
                indent=2,
            )
        )
        if reporting["status"] not in {"running", "disabled"}:
            raise SystemExit(1)
        return
    repository = (
        args.repository
        or args.community_repository
        or old.get("repository")
        or "mindie-agent/knowledge-vllm-ascend"
    )
    if not isinstance(repository, str) or not _REPOSITORY_RE.fullmatch(repository):
        parser.error("community repository must be owner/repo")
    visibility = (
        args.visibility
        if args.visibility is not None
        else (args.community_visibility if args.community_visibility is not None else "public")
    )
    if visibility != "public":
        parser.error("community sharing requires --visibility public")
    branch = args.branch or args.community_branch or old.get("branch") or "main"
    if not isinstance(branch, str) or not _NAME_RE.fullmatch(branch) or ".." in branch:
        parser.error("community branch is invalid")
    raw_roots = (args.project_root or []) + (args.community_project_root or [])
    if not raw_roots:
        raw_roots = list(old.get("project_roots") or [str(Path.cwd().resolve())])
    roots = []
    for root in raw_roots:
        canonical = str(Path(root).expanduser().resolve())
        if not Path(canonical).is_dir():
            parser.error("community project root does not exist: " + canonical)
        if canonical not in roots:
            roots.append(canonical)
    if not roots:
        parser.error("community sharing requires at least one --project-root")
    account = args.account or args.community_account or old.get("account")
    fork = args.fork or args.community_fork or old.get("fork")
    if account is not None and (
        not isinstance(account, str) or "/" in account or not _NAME_RE.fullmatch(account)
    ):
        parser.error("community account is invalid")
    if fork is not None and (
        not isinstance(fork, str) or not _REPOSITORY_RE.fullmatch(fork)
    ):
        parser.error("community fork must be owner/repo")
    if not fork:
        account, fork = resolve_github_identity(repository, account=account, fork=fork)
    enabled_req = dict(
        schema="mindie-community-config/1",
        enabled=True,
        generation=secrets.token_hex(16),
        enabled_at=time.time(),
        repository=repository,
        branch=branch,
        project_roots=roots,
        idle_seconds=old.get("idle_seconds", 300),
        visibility="public",
        consent_config=consent_file,
    )
    if account:
        enabled_req["account"] = account
    if fork:
        enabled_req["fork"] = fork
    comm_path.parent.mkdir(parents=True, exist_ok=True)
    merged = configure_community(comm_path, enabled_req, python=python)
    consent.record_choice("contribute", config)
    reporting = activate_reporting(config)
    print(
        json.dumps(
            dict(
                status="configured" if reporting["status"] == "running" else "degraded",
                configuration_status="configured",
                reporting=reporting,
                community_config=str(comm_path),
                sharing="enabled",
                sharing_choice="contribute",
                repository=merged["repository"],
                fork=merged.get("fork"),
                account=merged.get("account"),
                project_roots=merged["project_roots"],
                enabled_at=merged["enabled_at"],
            ),
            indent=2,
        )
    )
    if reporting["status"] != "running":
        raise SystemExit(1)


def main(argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv[:1] == ["sharing"]:
        sharing_main(raw_argv[1:])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--activate", type=Path)
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--root", type=Path)
    parser.add_argument("--domain", default="vllm-ascend")
    parser.add_argument("--no-public-feed", action="store_true")
    parser.add_argument("--claude-config-dir", type=Path)
    parser.add_argument("--community-repository")
    parser.add_argument("--community-project-root", action="append")
    parser.add_argument("--community-branch")
    parser.add_argument("--community-account")
    parser.add_argument("--community-fork")
    parser.add_argument("--community-visibility", choices=["public"])
    args = parser.parse_args(argv)
    if args.activate:
        if args.source:
            parser.error("--activate does not accept --source")
        print(json.dumps(activate_binding(args.activate)))
        return
    if not args.source:
        parser.error("--source is required for preparation")
    result = prepare_binding(
        args.source, args.config, root=args.root, domain=args.domain,
        no_public_feed=args.no_public_feed, claude_config_dir=args.claude_config_dir,
        community=community_settings(args, parser),
    )
    print(json.dumps(result, default=str))
    if result["status"] == "degraded":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
