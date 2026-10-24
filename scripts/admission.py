"""Thin wrapper: shared Admission only. No local lease or attempt SQL.

[Role]
Delegates task lease activation, deactivation, and inspection to the shared
`mindie_knowledge.loop.activation.Admission` store.

[Invariants]
- Lease is an internal per-task identity binding established by explicit entry;
  contribution consent never activates knowledge, and the lease is never
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


def deactivate(session, engine=None):
    return gate(engine).deactivate(session)
