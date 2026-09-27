"""Community sharing via the shared core validator. Default off.

The settings file is the declared ``community_config`` authority, resolved
read-only; profile convergence and the ``consent_config`` extension stamp
happen at explicit boundaries (``consent.migrate_community``), never inside
a status read.
"""

from __future__ import annotations

import json
from pathlib import Path

from consent import community_write_lock, consent_path, resolve_community_path


def _settings_mod():
    from mindie_knowledge.loop import settings as settings_mod

    return settings_mod


def load(config=None):
    path = resolve_community_path() if config is None else config
    return _settings_mod().load(path)


def public_status(config=None):
    return load(config).public_status()


def capture_allowed(lease, cwd, config=None) -> bool:
    settings = load(config)
    if not settings.allows_capture():
        return False
    if not lease or not isinstance(lease.get("project_root"), str):
        return False
    if not settings.in_scope(lease["project_root"]):
        return False
    if cwd and not settings.in_scope(cwd):
        return False
    return True


def write_enabled(
    *,
    repository,
    project_roots,
    branch="main",
    visibility="public",
    account=None,
    fork=None,
    config=None,
):
    if visibility != "public":
        raise ValueError("community sharing requires public visibility")
    path = resolve_community_path() if config is None else config
    with community_write_lock(path):
        settings = _settings_mod().write(
            path,
            enabled=True,
            repository=repository,
            project_roots=project_roots,
            branch=branch,
            visibility="public",
            account=account,
            fork=fork,
            consent_config=str(consent_path()),
        )
    return settings.public_status()


def write_disabled(config=None):
    path = resolve_community_path() if config is None else config
    previous = {}
    try:
        previous = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        previous = dict(schema="mindie-community-config/1", repository="local/unconfigured")
    repository = previous.get("repository") or "local/unconfigured"
    roots = previous.get("project_roots") or []
    with community_write_lock(path):
        settings = _settings_mod().write(
            path,
            enabled=False,
            repository=repository,
            project_roots=roots,
            branch=previous.get("branch", "main"),
            previous=previous,
            consent_config=str(consent_path()),
        )
    return settings.public_status()
