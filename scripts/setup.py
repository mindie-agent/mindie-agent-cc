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
    """Initialize an empty profile, or verify this exact installed binding.

    Unpublished development profiles and different bindings are never migrated,
    retired, or overwritten. Their data and uncertain operations stay intact.
    """
    from bootstrap import runtime_key
    from capture_config import prepare, prepare_store
    from install_state import InstallationUnavailable, read_binding
    from state_compatibility import check

    source = Path(source).resolve()
    config = Path(config or default_config_path()).expanduser().resolve()
    os.environ["MINDIE_CC_CONFIG"] = str(config)
    key = runtime_key(source)
    domain_root = Path(root or Path(os.environ.get("XDG_DATA_HOME")
                                  or Path.home() / ".local/share") / "mindie-agent/cc")
    domain_root = domain_root.expanduser().resolve()
    initial = dict(state_dir=str(domain_root / "adapter-state"),
                   community_config=str(config.with_name("mindie-community.json")),
                   base_config=str(config))

    def existing_binding():
        value = read_binding(config)
        binding_dir = Path(value["state_dir"]) / "installations" / key
        adapter_file = binding_dir / "cc.adapter.json"
        engine_file = binding_dir / "cc.engine.json"
        if (value["base_config"] != str(config)
                or value["engine_config"] != str(engine_file)
                or value["python"] != sys.executable
                or not adapter_file.is_file() or _read(adapter_file) != value):
            raise InstallationUnavailable(
                "native_reload_required: different or unsupported development binding; "
                "replacement is unsupported before 0.0.1; existing choices and data are preserved"
            )
        engine = _read(engine_file)
        if "client_contract" in engine:
            raise InstallationUnavailable("unsupported development profile; preserved unchanged")
        return value, adapter_file, engine

    # Refuse unsupported profiles before creating even the installation lock.
    adapter = existing_binding()[0] if config.exists() else initial
    with OperationLock(adapter).exclusive(timeout=30):
        if config.exists():
            value, adapter_file, engine = existing_binding()
            from candidate_validate import validate
            validate(source, identity(source), value["product_validation"])
            check(engine, str(Path(value["state_dir"]) / "remote"))
            if community is not None:
                write_community(Path(value["community_config"]), community, python=sys.executable)
        else:
            binding = Path(adapter["state_dir"]) / "installations" / key
            adapter_file, engine_file = binding / "cc.adapter.json", binding / "cc.engine.json"
            validation = validate_candidate(source)
            engine = dict(root=str(domain_root), domain=domain,
                          admission_path=str(domain_root / "admission.sqlite3"))
            check(engine, str(Path(adapter["state_dir"]) / "remote"))
            engine.update(transcript_adapter=str(HERE / "transcript.py"),
                          **prepare(sys.executable, HERE),
                          community_config=adapter["community_config"],
                          product_validation=validation)
            if not no_public_feed and domain == validation["publication"]["domain"]:
                from product_contract import publication_feed
                engine["feeds"] = [publication_feed(validation)]
            value = dict(adapter, python=sys.executable, engine_config=str(engine_file),
                         product_validation=validation)
            if claude_config_dir:
                value["claude_config_dir"] = str(Path(claude_config_dir).expanduser().resolve())
            engine["service_launch_context"] = service_launch_context(adapter_file, value)
            prepare_store(sys.executable, HERE, engine)
            _publish_bytes(engine_file, (json.dumps(engine, indent=2) + "\n").encode())
            _publish_bytes(adapter_file, (json.dumps(value, indent=2) + "\n").encode())
            write_community(Path(value["community_config"]), community, python=sys.executable)
            atomic_write(config, value)
        os.environ["MINDIE_CC_CONFIG"] = str(adapter_file)
        return dict(config=str(adapter_file), base_config=str(config), status="ready")


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
