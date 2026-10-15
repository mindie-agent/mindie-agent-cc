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

from install_state import OperationLock, atomic_write
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
    from pathlib import Path
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


def _handoff(adapter, engine_file, *, advance=True):
    from knowledge_service import client_engine_path, newer_binding
    from mindie_knowledge.loop.cli import config_at
    from mindie_knowledge.loop.client_contract import connect_ready, resolve_config
    from service_handoff import stop

    own = Path(engine_file).resolve()
    own_target, _ = resolve_config(own)
    target = client_engine_path(adapter)
    if own_target.resolve() != own or newer_binding(own, target) != own:
        return dict(status="superseded", config=str(target))
    connection = connect_ready(str(own), config_at(own))
    if connection:
        previous = Path(connection["config_path"])
    else:
        anchor = _read(adapter["base_config"])["engine_config"]
        previous, _ = resolve_config(anchor)
    if previous.resolve() == own:
        return dict(status="ready")
    if newer_binding(own, previous) != own:
        return dict(status="superseded", config=str(previous))
    if not advance:
        return dict(status="pending", config=str(previous))
    retirement = stop(str(previous), successor_config_path=str(own))
    if retirement.get("idle") is True and not retirement.get("cleanup_failed"):
        return dict(status="ready", retirement=retirement)
    if retirement.get("idle") is False and retirement.get("service") == "busy":
        return dict(status="pending", retirement=retirement)
    raise RuntimeError("previous service handoff is uncertain; original owner "
                       "and exact retirement receipt were retained")


def advance_handoff(adapter_file=None):
    """Session admission may finish a known busy handoff; hot Hooks never do."""
    adapter_file = Path(adapter_file or default_config_path()).resolve()
    adapter = _read(adapter_file)
    with OperationLock(adapter).exclusive(timeout=30):
        return _handoff(adapter, adapter["engine_config"])


def prepare_binding(source, config=None, *, root=None, domain="vllm-ascend",
                    no_public_feed=False, claude_config_dir=None, community=None,
                    advance=True):
    """Prepare the native-selected release without a mutable runtime selector."""
    from bootstrap import runtime_key
    from capture_config import prepare, prepare_store
    from state_compatibility import check
    from service_handoff import stop
    from legacy_update import migrate, status as legacy_status

    source = Path(source).resolve()
    config = Path(config or default_config_path()).expanduser().resolve()
    os.environ["MINDIE_CC_CONFIG"] = str(config)
    if config.is_file():
        adapter = _read(config)
    else:
        domain_root = Path(root or Path(os.environ.get("XDG_DATA_HOME")
                                      or Path.home() / ".local/share") / "mindie-agent/cc")
        domain_root = domain_root.expanduser().resolve()
        adapter = dict(state_dir=str(domain_root / "adapter-state"),
                       community_config=str(config.with_name("mindie-community.json")),
                       base_config=str(config))
    binding = Path(adapter["state_dir"]) / "installations" / runtime_key(source)
    adapter_file, engine_file = binding / "cc.adapter.json", binding / "cc.engine.json"
    with OperationLock(adapter).exclusive(timeout=30):
        existing = config.is_file()
        if existing:
            adapter = _read(config)
            anchor = Path(adapter["engine_config"])
            engine = _read(anchor)
        else:
            anchor = None
            engine = dict(root=str(domain_root), domain=domain,
                          admission_path=str(domain_root / "admission.sqlite3"))
        if claude_config_dir:
            adapter["claude_config_dir"] = str(Path(claude_config_dir).expanduser().resolve())
        legacy_profile = existing and not engine.get("client_contract")
        if legacy_profile and not advance:
            raise RuntimeError("legacy migration requires native SessionStart; "
                               "reload or open a Claude session before using this release")
        legacy = (migrate(adapter, config) if not existing or legacy_profile
                  else legacy_status(adapter))
        if adapter_file.is_file() and engine_file.is_file():
            value = _read(adapter_file)
            saved = _read(engine_file)
            if (value.get("python") != sys.executable
                    or value.get("base_config") != str(config)
                    or saved.get("client_contract", {}).get("adapter_profile") != str(config)):
                raise ValueError("prepared binding differs from this runtime/profile")
            from candidate_validate import validate
            validate(source, identity(source), value["product_validation"])
            engine = saved
        else:
            validation = validate_candidate(source)
            if anchor is not None:
                check(anchor, str(Path(adapter["state_dir"]) / "remote"))
            engine.update(
                transcript_adapter=str(HERE / "transcript.py"),
                **prepare(sys.executable, HERE),
                community_config=adapter["community_config"],
                product_validation=validation,
                client_contract=dict(
                    schema="mindie-service-client/1",
                    transcript_protocol="mindie-cc-transcript/1",
                    summary_protocol="mindie-cc-summary/1",
                    adapter_profile=str(config), adapter_config_env="MINDIE_CC_CONFIG",
                ),
            )
            if anchor is None and not no_public_feed:
                from product_contract import publication_feed
                if engine["domain"] == validation["publication"]["domain"]:
                    engine["feeds"] = [publication_feed(validation)]
            value = dict(adapter, python=sys.executable, engine_config=str(engine_file),
                         base_config=str(config), product_validation=validation)
            engine["service_launch_context"] = service_launch_context(
                adapter_file, value, previous=engine.get("service_launch_context"))
            prepare_store(sys.executable, HERE, engine)
            atomic_write(engine_file, engine)
            atomic_write(adapter_file, value)
        handoff = dict(status="ready")
        if legacy_profile:
            retirement = stop(str(anchor))
            if retirement.get("idle") is not True or retirement.get("cleanup_failed"):
                raise RuntimeError("legacy service handoff is pending; original "
                                   "configuration and receipts were retained")
            handoff["retirement"] = retirement
        elif existing:
            handoff = _handoff(value, engine_file, advance=advance)
        write_community(Path(value["community_config"]), community, python=sys.executable)
        if legacy_profile:
            import hashlib
            original = config.read_bytes()
            preserved = binding.parent / (
                "legacy-profile-" + hashlib.sha256(original).hexdigest() + ".json")
            _publish_bytes(preserved, original)
        if not existing or legacy_profile:
            atomic_write(config, value)
        os.environ["MINDIE_CC_CONFIG"] = str(adapter_file)
        import consent
        consent_result = consent.migrate_consent()
        return dict(config=str(adapter_file), base_config=str(config), status=handoff["status"],
                    legacy=legacy, handoff=handoff,
                    legacy_session_reload_required=legacy_profile,
                    consent_migration=consent_result)


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
        f"os.execv({sys.executable!r}, [{sys.executable!r}, '-I', "
        f"{str(HERE / 'mindie_launch.py')!r}, *sys.argv[1:]])\n"
    )
    _publish_bytes(Path(path), value.encode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
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
    result = prepare_binding(
        args.source, args.config, root=args.root, domain=args.domain,
        no_public_feed=args.no_public_feed, claude_config_dir=args.claude_config_dir,
        community=community_settings(args, parser),
    )
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
