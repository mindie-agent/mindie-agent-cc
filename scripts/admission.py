"""Thin wrapper: shared Admission for default-on knowledge and opt-in capture.

[Role]
Delegates task lease activation, on-demand default provisioning, deactivation,
and read-only inspection to `mindie_knowledge.loop.activation.Admission`.

[Invariants]
- Read-only checks (`is_deactivated`, `knowledge_lease`) never create
  `admission.sqlite3` when absent on disk.
- `ensure_default_lease` provisions an active session lease on demand unless
  the session was explicitly deactivated (`status == "inactive"`), raising an
  earlier `0.0` placeholder boundary when a verified native creation timestamp
  (`not_before > 0`) becomes known.

[Timeouts & Locks]
- Uses shared core SQLite WAL + `busy_timeout=5000` inside `Admission`.

[Side-Effect Policy]
- Mutates `admission_path(engine)` only in `activate`, `ensure_default_lease`,
  and `deactivate`.
"""

from __future__ import annotations

import os
from pathlib import Path

from paths import admission_path


def gate(engine=None):
    from mindie_knowledge.loop.activation import Admission

    return Admission(admission_path(engine))


def is_deactivated(session, *, engine=None) -> bool:
    """Return True when an existing admission store marks `session` inactive."""
    if not isinstance(session, str) or not session.strip():
        return False
    path = admission_path(engine)
    if not path.is_file():
        return False
    state = gate(engine).inspect(session)
    if state.get("status") == "unavailable":
        from mindie_knowledge.loop.activation import AdmissionUnavailable

        raise AdmissionUnavailable(state.get("error_class") or "admission is unavailable")
    return state.get("status") == "inactive"


def knowledge_lease(session, *, engine=None) -> dict | None:
    """Read-only lookup of an existing active lease without creating SQLite state."""
    if not isinstance(session, str) or not session.strip():
        return None
    path = admission_path(engine)
    if not path.is_file():
        return None
    return gate(engine).knowledge_lease(session)


def activate(session, *, project_root, root_session=None, not_before=None, engine=None):
    adm = gate(engine)
    adm.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return adm.activate(
        session,
        project_root=str(Path(project_root).expanduser().resolve()),
        root_session=root_session,
        not_before=not_before,
    )


def ensure_default_lease(
    session,
    *,
    project_root=None,
    not_before: float | None = 0.0,
    engine=None,
) -> dict:
    """Return an active session lease, provisioning or refreshing scope on demand."""
    if is_deactivated(session, engine=engine):
        raise ValueError("knowledge session lease is inactive")
    existing = knowledge_lease(session, engine=engine)
    raw_root = (
        project_root
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or os.environ.get("MINDIE_HOST_CWD")
        or os.getcwd()
    )
    resolved_root = str(Path(raw_root).expanduser().resolve())
    if existing is not None:
        existing_boundary = existing.get("activated_at")
        same_root = (
            project_root is None
            or str(Path(existing.get("project_root") or "").expanduser().resolve()) == resolved_root
        )
        needs_boundary_raise = (
            existing_boundary == 0.0
            and isinstance(not_before, (int, float))
            and float(not_before) > 0.0
        )
        if existing_boundary != 0.0:
            return existing
        if same_root and not needs_boundary_raise:
            return existing
        if same_root and needs_boundary_raise:
            adm = gate(engine)
            adm._write(
                lambda db: db.execute(
                    "UPDATE leases SET activated_at=?, project_root=? WHERE session=? AND enabled=1",
                    (float(not_before), resolved_root, session.strip()),
                )
            )
            refreshed = knowledge_lease(session, engine=engine)
            if refreshed is not None:
                return refreshed
    return activate(
        session,
        project_root=resolved_root,
        root_session=session,
        not_before=not_before,
        engine=engine,
    )


def deactivate(session, engine=None):
    return gate(engine).deactivate(session)
