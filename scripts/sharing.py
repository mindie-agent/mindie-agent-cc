"""Community sharing via the shared core validator and write boundary.

The settings file is the declared ``community_config`` authority, resolved
read-only; profile convergence and the ``consent_config`` stamp happen at
explicit boundaries (``consent.migrate_community``), never inside a status
read. Managed mutations go through core's locked ``settings.write`` — one
lock protocol, no adapter fork. Because a boundary migration can repoint
the authority while a write is in flight, each mutation confirms the
current pointer after its first locked write and re-applies exactly once
on the converged file when it moved — the user's intent is never left on a
retired legacy file.
"""

from __future__ import annotations

import json
from pathlib import Path

from consent import consent_path, resolve_community_path


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


def _write_mutation(config, mutate):
    """One locked settings mutation on the current authority.

    ``mutate(path)`` performs the core ``settings.write`` (which holds the
    file's cross-process lock for the whole read-merge-write). If a
    concurrent boundary migration repointed the authority during the first
    write, the mutation is applied once more on the converged path; the
    stale-file write is inert because every consumer follows the pointer.
    """
    path = resolve_community_path() if config is None else config
    settings = mutate(path)
    current = resolve_community_path() if config is None else config
    if current != path:
        settings = mutate(current)
    return settings


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

    def enable(path):
        return _settings_mod().write(
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

    return _write_mutation(config, enable).public_status()


def write_disabled(config=None):
    def disable(path):
        previous = {}
        try:
            previous = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            previous = dict(schema="mindie-community-config/1", repository="local/unconfigured")
        repository = previous.get("repository") or "local/unconfigured"
        roots = previous.get("project_roots") or []
        return _settings_mod().write(
            path,
            enabled=False,
            repository=repository,
            project_roots=roots,
            branch=previous.get("branch", "main"),
            previous=previous,
            consent_config=str(consent_path()),
        )

    return _write_mutation(config, disable).public_status()
