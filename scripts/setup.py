#!/usr/bin/env python3
"""Prepare this native release's fixed runtime binding and existing shared state.

Plugin installation, update and removal belong to Claude's Git marketplace.
No code-update scheduler or mutable code-selection registry lives here.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
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
from product_contract import identity

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


def validate_candidate(source):
    from candidate_validate import validate
    return validate(source, identity(source))


def prepare_binding(source, config=None, *, root=None, domain="vllm-ascend",
                    no_public_feed=False, claude_config_dir=None, community=None):
    """Prepare immutable candidate bytes; only an empty profile is activated here."""
    from bootstrap import runtime_key
    from capture_config import prepare, prepare_store
    from harness import require_engine_support
    from state_compatibility import check

    source = Path(source).resolve()
    config = Path(config or default_config_path()).expanduser().resolve()
    os.environ["MINDIE_CC_CONFIG"] = str(config)
    key = runtime_key(source)
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
    binding = Path(adapter["state_dir"]) / "installations" / key
    adapter_file, engine_file = binding / "cc.adapter.json", binding / "cc.engine.json"
    if adapter_file.exists():
        value = read_binding(adapter_file)
        from candidate_validate import validate
        validate(source, identity(source), value["product_validation"])
        if (value["python"] != sys.executable or value["runtime_scripts"] != str(HERE)
                or value["base_config"] != str(config)):
            raise InstallationUnavailable("prepared candidate identity differs")
        engine = _read(value["engine_config"])
        require_engine_support(engine, source=source)
        check(engine, str(Path(value["state_dir"]) / "remote"))
    else:
        validation = validate_candidate(source)
        if selected is None:
            engine = dict(root=str(domain_root), domain=domain,
                          admission_path=str(domain_root / "admission.sqlite3"))
        else:
            engine = _read(selected["engine_config"])
        check(engine, str(Path(adapter["state_dir"]) / "remote"))
        engine.update(transcript_adapter=str(HERE / "transcript.py"),
                      **prepare(sys.executable, HERE),
                      community_config=adapter["community_config"],
                      product_validation=validation)
        if selected is None and not no_public_feed and domain == validation["publication"]["domain"]:
            from product_contract import publication_feed
            engine["feeds"] = [publication_feed(validation)]
        value = dict(adapter, python=sys.executable, runtime_scripts=str(HERE),
                     adapter_config=str(adapter_file), engine_config=str(engine_file),
                     product_validation=validation)
        if claude_config_dir:
            value["claude_config_dir"] = str(Path(claude_config_dir).expanduser().resolve())
        engine["service_launch_context"] = service_launch_context(adapter_file, value)
        value["claude_config_dir"] = engine["service_launch_context"]["paths"]["CLAUDE_CONFIG_DIR"]
        require_engine_support(engine, source=source)
        if selected is None:
            prepare_store(sys.executable, HERE, engine)
        _publish_bytes(engine_file, (json.dumps(engine, indent=2) + "\n").encode())
        _publish_bytes(adapter_file, (json.dumps(value, indent=2) + "\n").encode())

    # Validation, dependencies and storage preparation never hold admission.
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
    return dict(config=str(adapter_file), base_config=str(config), status=status)


def activate_binding(candidate_file, *, observation_seconds=30.0):
    """Switch a native-installed candidate only after idle retirement and readiness.

    The caller verifies native installation facts. This operation never downloads,
    retries a native mutation, cancels business work, or rewrites saved choices.
    """
    from mindie_knowledge.loop.cli import config_at, connect, prepare_service
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
            if state == "absent":
                prepare_service(candidate["engine_config"], probe_timeout=remaining())
            while True:
                if remaining() <= 0:
                    return incomplete("new_readiness", service=state, retirement=retirement)
                state = _probe(engine, remaining(), config_path=candidate["engine_config"])
                if remaining() <= 0:
                    return incomplete("new_readiness", service=state, retirement=retirement)
                if state == "ready":
                    connect(engine, config_path=candidate["engine_config"])
                    try:
                        atomic_write(config, candidate)
                    except OSError as exc:
                        return dict(status="incomplete", stage="canonical_publish",
                                    service="ready", retirement=retirement,
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
    value = (
        "import os, sys\n"
        f"os.environ['MINDIE_CC_CONFIG'] = {str(adapter_file)!r}\n"
        "os.environ.pop('PYTHONPATH', None)\n"
        f"sys.path.insert(0, {str(HERE)!r})\n"
        "from bounded import exec_or_wait\n"
        f"exec_or_wait([{sys.executable!r}, '-I', "
        f"{str(HERE / 'mindie_launch.py')!r}, *sys.argv[1:]], dict(os.environ))\n"
    )
    _publish_bytes(Path(path), value.encode("utf-8"))


def main(argv=None):
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


if __name__ == "__main__":
    main()
