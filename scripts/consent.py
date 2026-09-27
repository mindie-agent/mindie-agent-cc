"""Thin adapter wiring over the shared consent store.

The persistent choice document (``mindie-consent/1``) is implemented ONCE by
the knowledge core (``mindie_knowledge/consent_store.py``); this adapter
carries a byte-identical bootstrap copy (``scripts/consent_store.py``) because
the entry hook runs before the selected runtime can be loaded. Only path
anchoring and legacy-candidate collection live here — no third hand-written
policy.

One document per installation profile, stored beside the base adapter
configuration so every adapter sharing this profile reads the same saved
choice. An independently isolated profile has its own file and never inherits
another profile's choice. Hook/MCP children run against a per-generation
config copy, so anchoring always resolves the base config directory
(``paths.base_config_path``): an upgrade generation must not strand the saved
choice.

Reads are pure: a status/load never migrates, repairs or probes another
authority. Legacy import (``migrate_consent``) and community convergence
(``migrate_community``) run only at explicit install/upgrade/entry
boundaries. The adapter first-use marker only deduplicates native slash
attempts; it is a one-time migration candidate, never a consent source.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import consent_store
from paths import (
    base_config_path,
    community_config_path,
    config_path,
    first_use_path,
)

ConsentError = consent_store.ConsentError
SCHEMA = consent_store.SCHEMA
CHOICES = consent_store.CHOICES
REPORTING = consent_store.REPORTING
COMMUNITY_SCHEMA = "mindie-community-config/1"


def consent_path() -> Path:
    """The profile-shared consent document (sibling of the base config)."""
    return base_config_path().parent / "mindie-consent.json"


def shared_community_path() -> Path:
    """The profile-shared community settings path."""
    return base_config_path().parent / "mindie-community.json"


def _store(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _read_json(path: Path):
    """(state, data): ok / missing / unreadable / corrupt. Never raises."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unreadable", None
    if len(raw) > 64 * 1024:
        return "corrupt", None
    try:
        data = json.loads(raw)
    except ValueError:
        return "corrupt", None
    if not isinstance(data, dict):
        return "corrupt", None
    return "ok", data


def load() -> dict:
    """Pure read of the persistent choice in the adapter's legacy shape.

    ``state`` is ``ok``/``missing``/``unreadable``/``corrupt``; an invalid
    saved choice value is corrupt, not absent. Zero side effects: no import,
    no repair, no migration (that is ``migrate_consent`` at an explicit
    boundary only).
    """
    raw = consent_store.read(consent_path())
    return dict(
        state=raw["state"],
        choice=raw["choice"],
        reporting=raw["reporting"],
        path=raw["path"],
        error=raw["error"],
    )


def record_choice(choice: str) -> str:
    """Explicit user choice; preserves reporting and untouched metadata.

    Raises ``ConsentError`` when the saved authority is corrupt or
    unreadable — a damaged file is never silently cleared by an update.
    """
    consent_store.record_choice(consent_path(), choice)
    return choice


def record_reporting(value: str) -> str:
    """Explicit reporting choice; preserves the contribution choice."""
    consent_store.record_reporting(consent_path(), value)
    return value


def _marker_path():
    try:
        return first_use_path()
    except (OSError, ValueError, FileNotFoundError):
        return None


def _legacy_marker_choice():
    path = _marker_path()
    if path is None:
        return None
    state, data = _read_json(path)
    if state == "ok":
        choice = data.get("choice")
        if choice in CHOICES:
            return dict(choice=choice)
    return None


def _legacy_generation_record():
    """A consent document stranded at a pre-fix per-generation location.

    Real installs handed hook children a generation config copy, so older
    revisions wrote the authority beside it. It is the same schema and the
    newest explicit record, a one-time migration candidate.
    """
    try:
        generation = config_path().parent / "mindie-consent.json"
    except OSError:
        return None
    if generation == consent_path():
        return None
    state, data = _read_json(generation)
    if state != "ok" or data.get("schema") != SCHEMA:
        return None
    record = {}
    choice = data.get("choice")
    if choice in CHOICES:
        record["choice"] = choice
    reporting = data.get("reporting")
    if reporting in REPORTING:
        record["reporting"] = reporting
    return record or None


def _legacy_community_choice():
    """A legacy settings file with enabled=true was an explicit public
    opt-in; anything else proves no choice (never guessed)."""
    for candidate in (shared_community_path(), _legacy_community_path()):
        if candidate is None:
            continue
        state, data = _read_json(candidate)
        if state == "ok" and data.get("enabled") is True:
            return dict(choice="contribute")
    return None


def _legacy_community_path():
    try:
        return community_config_path()
    except (OSError, ValueError, FileNotFoundError):
        return None


def _legacy_candidates():
    """Validated legacy choice records, newest-explicit first, each naming
    its source. Anything unreadable or malformed simply has no evidence."""
    candidates = []
    generation = _legacy_generation_record()
    if generation is not None:
        candidates.append(dict(
            choice=generation.get("choice"),
            reporting=generation.get("reporting"),
            source="generation-consent",
        ))
    marker = _legacy_marker_choice()
    if marker is not None:
        candidates.append(dict(
            choice=marker["choice"], reporting=None, source="first-use-marker",
        ))
    community = _legacy_community_choice()
    if community is not None:
        candidates.append(dict(
            choice=community["choice"], reporting=None, source="community-enabled",
        ))
    return candidates


def migrate_consent() -> dict:
    """One explicit-boundary import of legacy choices via the shared store.

    An existing valid authority always wins (``kept``); missing evidence is
    ``absent``; conflicting legacy sources are a diagnosable ``conflict``;
    a damaged authority is an ``error``. Nothing is guessed and the original
    data is preserved on every no-write outcome.
    """
    return consent_store.migrate(consent_path(), _legacy_candidates())


def marker_exists() -> bool:
    """A legacy first-use marker exists (any state): prior setup evidence."""
    marker = _marker_path()
    return bool(marker is not None and marker.exists())


def install_traces() -> bool:
    """Any evidence this installation was set up before — consent (any
    state), community settings (shared or legacy, any state) or a legacy
    marker. A cold install has none; everything else is an existing
    installation and never re-runs first-time onboarding."""
    marker = _marker_path()
    if consent_path().exists() or (marker is not None and marker.exists()):
        return True
    if shared_community_path().exists():
        return True
    legacy = _legacy_community_path()
    return bool(legacy is not None and legacy.exists())


def resolve_community_path() -> Path:
    """The declared community settings authority, resolved read-only.

    This never copies, rewrites or probes another file: the adapter
    configuration's ``community_config`` key is the one declared path, and a
    missing key or unreadable config surfaces as the honest exception. The
    profile convergence of legacy per-adapter files is ``migrate_community``
    at explicit boundaries only.
    """
    return community_config_path()


def _repoint_community_keys(effective: Path, result: dict) -> None:
    """Point every config copy's ``community_config`` key at the one
    authority: base adapter and engine configs plus the committed current
    generation's adapter and engine configs (the worker reads the
    generation engine copy). Data-only; each document independently
    atomic; failures are recorded, never hidden."""
    from paths import engine_config_path

    targets = []
    base = base_config_path()
    targets.append(base)
    try:
        base_adapter = json.loads(base.read_text())
        if isinstance(base_adapter, dict):
            targets.append(engine_config_path(base_adapter))
    except (OSError, ValueError) as exc:
        result["errors"].append(f"base config unreadable: {type(exc).__name__}")
    try:
        import genstate

        current = genstate.read_current()
        generation_adapter = Path(current["adapter_config"])
        if generation_adapter != base and generation_adapter.is_file():
            targets.append(generation_adapter)
            try:
                gen_adapter = json.loads(generation_adapter.read_text())
                if isinstance(gen_adapter, dict):
                    gen_engine = engine_config_path(gen_adapter)
                    if gen_engine.is_file():
                        targets.append(gen_engine)
            except (OSError, ValueError) as exc:
                result["errors"].append(
                    f"generation config unreadable: {type(exc).__name__}"
                )
    except Exception as exc:
        result["errors"].append(f"current generation unknown: {type(exc).__name__}")
    seen = set()
    for target in targets:
        target = Path(target)
        if target in seen:
            continue
        seen.add(target)
        try:
            data = json.loads(target.read_text())
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            if data.get("community_config") == str(effective):
                continue
            data["community_config"] = str(effective)
            _store(target, data)
            result["repointed"].append(str(target))
        except (OSError, ValueError) as exc:
            result["errors"].append(f"{target}: {type(exc).__name__}")


def community_write_lock(anchor=None):
    """Bounded cross-process lock serializing ALL community settings writes
    in this profile.

    The lock anchors at the profile-canonical path (never at the file being
    written): the declared authority can move during adoption, so a
    per-target lock would let a migration and a concurrent enable/disable
    serialize against two different files and lose each other. One stable
    anchor covers stamp, enable/disable and install writes alike. Reuses the
    shared store's lock discipline (sibling ``.lock`` file, flock/msvcrt,
    bounded wait, never unlinked). This is the adapter-side half of review
    item 6; the shared core write boundary proposed in DESIGN-ISSUES.md
    replaces this interim wiring when published.
    """
    base = Path(anchor) if anchor is not None else shared_community_path()
    return consent_store._UpdateLock(Path(str(base) + ".lock"))


def _ensure_consent_extension(effective: Path, result: dict) -> None:
    """Stamp the ``consent_config`` extension (absolute path of the profile
    consent authority) onto a valid settings document, preserving every
    other field including the generation. A damaged or missing document is
    left alone: missing is an honest state and corrupt is a fault, neither
    is repaired here. The field grants no extra permission; an explicit
    enabled=false always wins at the core gate. Caller holds the profile
    community write lock (``migrate_community``), so the re-read and write
    cannot interleave with an enable/disable."""
    state, data = _read_json(effective)
    if state != "ok" or data.get("schema") != COMMUNITY_SCHEMA:
        if state not in {"missing"}:
            result["consent_config"] = f"skipped ({state})"
        return
    authority = str(consent_path())
    if data.get("consent_config") == authority:
        return
    data["consent_config"] = authority
    try:
        _store(effective, data)
        result["consent_config"] = "stamped"
    except OSError as exc:
        result["errors"].append(
            f"consent_config stamp failed: {type(exc).__name__}"
        )


def migrate_community() -> dict:
    """One explicit-boundary migration of the community settings authority.

    Called only from install (setup.py), upgrade/entry (op_init) and
    settings-change operations — never from a status/load read. The whole
    migration holds the profile community write lock, so a concurrent
    enable/disable either lands first (and is adopted) or runs after (and
    writes the repointed authority): no ordering loses a field. Idempotent:

    - declared path missing from the adapter config: honest fault, no action
      and no search for another possibly-enabled file;
    - only the declared legacy file exists: adopt it into the profile-shared
      location by atomic copy (the legacy file is kept as evidence);
    - both exist with different bytes: a scope conflict — the declared
      authority is kept, nothing is merged or widened, and the conflict is
      reported for diagnosis;
    - afterwards every config copy (base and current generation, adapter
      and engine) points at the one effective authority, and a valid
      settings document carries the ``consent_config`` extension.
    """
    result = dict(migrated=False, conflict=False, repointed=[], errors=[])
    try:
        with community_write_lock():
            return _migrate_community_locked(result)
    except ConsentError as exc:
        result["errors"].append(f"community migration lock: {exc}")
        return result


def _migrate_community_locked(result: dict) -> dict:
    try:
        declared = community_config_path()
    except FileNotFoundError:
        result["errors"].append("adapter configuration is missing")
        return result
    except ValueError as exc:
        result["errors"].append(str(exc))
        return result
    canonical = shared_community_path()
    effective = declared
    if declared != canonical:
        declared_state, declared_data = _read_json(declared)
        canonical_exists = canonical.exists()
        if declared_state == "ok" and not canonical_exists:
            try:
                canonical.parent.mkdir(parents=True, exist_ok=True)
                tmp = canonical.with_suffix(canonical.suffix + ".tmp")
                tmp.write_bytes(declared.read_bytes())
                os.replace(tmp, canonical)
                try:
                    canonical.chmod(0o600)
                except OSError:
                    pass
                effective = canonical
                result["migrated"] = True
            except OSError as exc:
                result["errors"].append(
                    f"adoption copy failed: {type(exc).__name__}"
                )
                return result
        elif declared_state in {"missing", "unreadable"} and canonical_exists:
            effective = canonical
            result["migrated"] = True
        elif declared_state == "ok" and canonical_exists:
            if declared.read_bytes() == canonical.read_bytes():
                effective = canonical
                result["migrated"] = True
            else:
                # Two divergent saved scopes: keep the declared authority,
                # surface the conflict; never merge into a wider scope.
                result["conflict"] = True
                result["conflict_detail"] = (
                    f"declared {declared} and profile {canonical} differ; "
                    "the declared authority was kept"
                )
                return result
        elif declared_state == "corrupt":
            result["errors"].append(
                f"declared community settings are corrupt: {declared}"
            )
            return result
    _repoint_community_keys(effective, result)
    _ensure_consent_extension(effective, result)
    result["effective"] = str(effective)
    return result
