"""Thin wrapper: shared Admission only. No local lease or attempt SQL.

[Role]
Delegates task lease activation, deactivation, and inspection to the shared
`mindie_knowledge.loop.activation.Admission` store.

[Invariants]
- Lease is an internal per-task identity binding established by explicit entry
  or authorized in-scope Stop association; never a consent prompt and never
  failure-paused.
- An explicit `deactivate` is a revocation that ordinary events never undo.

[Timeouts & Locks]
- Uses shared core SQLite WAL + busy_timeout inside `Admission`.

[Side-Effect Policy]
- Mutates only the neutral `admission_path(engine)` SQLite database.
"""

from __future__ import annotations

from pathlib import Path

from paths import admission_path


def gate(engine=None):
    from mindie_knowledge.loop.activation import Admission

    return Admission(admission_path(engine))


def activate(session, *, project_root, root_session=None, engine=None):
    return gate(engine).activate(
        session,
        project_root=str(Path(project_root).resolve()),
        root_session=root_session,
    )


def associate(session, *, project_root, not_before, engine=None):
    """Bind a verified host event to its existing authorization.

    The association boundary is the authorized task creation time, never the
    Stop or binding time; a revoked task is never reactivated here.
    """
    return gate(engine).associate(
        session,
        project_root=str(Path(project_root).resolve()),
        not_before=not_before,
    )


def deactivate(session, engine=None):
    return gate(engine).deactivate(session)
