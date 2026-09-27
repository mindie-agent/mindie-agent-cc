"""Install-level one-time choices: the single persistent consent authority.

One small JSON document (``mindie-consent/1``) per installation profile,
stored beside the base adapter configuration so every adapter sharing this
profile reads the same saved choice. An independently isolated profile has
its own file and never inherits another profile's choice. Hook/MCP children
run against a per-generation config copy, so anchoring always resolves the
base config directory (``paths.base_config_path``): an upgrade generation
must not strand the saved choice.

The adapter first-use marker only deduplicates native slash attempts; the
user's choice lives here and is imported from legacy records exactly once.
A missing, unreadable, corrupt and explicitly disabled state stay strictly
apart: damaged saved state is a fault (read-only help keeps working, writes
stop), never a fresh install and never a guessed opt-in or opt-out.

Community settings reads use the declared ``community_config`` path as-is.
Migrating a legacy per-adapter file into the profile-shared location, and
pointing every config copy (base and current generation, adapter and
engine) at the one authority, happens only at explicit install/upgrade/
entry boundaries via ``migrate_community`` — never inside a status/load
read, and never by falling back to another file when the declared
authority is unreadable.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from paths import (
    base_config_path,
    community_config_path,
    config_path,
    first_use_path,
)

SCHEMA = "mindie-consent/1"
CHOICES = ("contribute", "read-only", "later", "disabled")
REPORTING = ("enabled", "disabled", "later")
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
    newest explicit record, imported once into the profile location.
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


def _import_legacy_once(path: Path) -> None:
    record = (
        _legacy_generation_record()
        or _legacy_marker_choice()
        or _legacy_community_choice()
    )
    if record is None:
        return
    data = {
        "schema": SCHEMA,
        "choice": record["choice"],
        "choice_at": time.time(),
        "migrated_from": "legacy",
    }
    if record.get("reporting") is not None:
        data["reporting"] = record["reporting"]
        data["reporting_at"] = time.time()
    _store(path, data)


def load() -> dict:
    """Read the persistent choice, importing legacy state exactly once.

    Returns ``state`` of ``ok``/``missing``/``unreadable``/``corrupt`` plus
    the saved ``choice`` and ``reporting`` values when valid. An invalid
    saved choice value is corrupt, not absent.
    """
    path = consent_path()
    if not path.exists():
        try:
            _import_legacy_once(path)
        except OSError:
            pass  # an unwritable profile stays truthful below
    state, data = _read_json(path)
    result = dict(state=state, choice=None, reporting=None, path=str(path),
                  error=None)
    if state != "ok":
        if state == "corrupt":
            result["error"] = "consent file is damaged"
        elif state == "unreadable":
            result["error"] = "consent file is unreadable"
        return result
    if data.get("schema") != SCHEMA:
        result.update(state="corrupt", error="unsupported consent schema")
        return result
    choice = data.get("choice")
    if choice is not None and choice not in CHOICES:
        result.update(state="corrupt", error="unknown consent choice")
        return result
    reporting = data.get("reporting")
    if reporting is not None and reporting not in REPORTING:
        result.update(state="corrupt", error="unknown reporting choice")
        return result
    result.update(choice=choice, reporting=reporting)
    return result


def record_choice(choice: str) -> str:
    if choice not in CHOICES:
        raise ValueError("choice must be contribute, read-only, later or disabled")
    current = load()
    data = {}
    if current["state"] == "ok":
        data = dict(_read_json(consent_path())[1])
    data.update(schema=SCHEMA, choice=choice, choice_at=time.time())
    data.pop("migrated_from", None)
    _store(consent_path(), data)
    return choice


def record_reporting(value: str) -> str:
    if value not in REPORTING:
        raise ValueError("reporting must be enabled, disabled or later")
    current = load()
    data = {}
    if current["state"] == "ok":
        data = dict(_read_json(consent_path())[1])
    data.update(schema=SCHEMA, reporting=value, reporting_at=time.time())
    _store(consent_path(), data)
    return value


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


def _ensure_consent_extension(effective: Path, result: dict) -> None:
    """Stamp the ``consent_config`` extension (absolute path of the profile
    consent authority) onto a valid settings document, preserving every
    other field including the generation. A damaged or missing document is
    left alone: missing is an honest state and corrupt is a fault, neither
    is repaired here. The field grants no extra permission; an explicit
    enabled=false always wins at the core gate."""
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
    settings-change operations — never from a status/load read. Idempotent:

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
