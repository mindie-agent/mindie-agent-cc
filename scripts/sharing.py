"""Community sharing via the shared core validator and write boundary.

The settings file is the declared ``community_config`` authority, resolved
read-only; profile convergence and the ``consent_config`` stamp happen at
explicit boundaries (``consent.migrate_community``), never inside a status
read. Managed mutations hold the shared ``CommunityWriteContext`` anchored
at the profile's canonical community path — the one lock key every writer
of this profile uses — and resolve/re-read the current authority inside it
before mutating. No adapter lock fork, no write-then-check replay, no
caller-supplied stale merge base.
"""

from __future__ import annotations

from pathlib import Path

from consent import consent_path, resolve_community_path, shared_community_path


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


def _write_context(config):
    """The profile's one write boundary, anchored at the canonical key even
    while the declared authority is still a legacy file (an explicit test
    path anchors at itself)."""
    settings_mod = _settings_mod()
    key = shared_community_path() if config is None else Path(config)
    return settings_mod, settings_mod.CommunityWriteContext(key)


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
    settings_mod, context = _write_context(config)
    with context as ctx:
        path = resolve_community_path() if config is None else config
        current = ctx.read(path)
        if account is None:
            account = current.raw.get('account')
        if (fork is None and current.repository == repository
                and current.branch == branch and current.raw.get('account') == account):
            fork = current.raw.get('fork')
        settings = ctx.write(
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
    settings_mod, context = _write_context(config)
    with context as ctx:
        path = resolve_community_path() if config is None else config
        current = ctx.read(path)
        # Scope values reload inside the boundary — never a stale pre-lock
        # snapshot; an explicit disable keeps the saved scope verbatim. A
        # corrupt authority fails in ctx.write below with bytes preserved.
        settings = ctx.write(
            path,
            enabled=False,
            repository=current.repository or "local/unconfigured",
            project_roots=[str(root) for root in current.project_roots],
            branch=current.branch,
            consent_config=str(consent_path()),
        )
    return settings.public_status()
