"""Release jump-script refresh and superseded release retirement.

[Role]
Rebinds `$CLAUDE_PLUGIN_DATA/releases/<active>/entry.py` to the interpreter
selected by a component update or activation switch, and retires older release
roots while retaining the current version plus one rollback generation.

[Invariants]
- Only the active version's entry is rewritten; a retained rollback version
  keeps its own adapter bytes and interpreter.
- Byte-identical entries are never rewritten, so repeated maintenance is a
  no-op. Installations without an absolute `CLAUDE_PLUGIN_DATA` are untouched.
- A release directory referenced by a live process is never retired; deletion
  uses `retire_directory` so Windows never holds an entry handle across rename.

[Timeouts & Locks]
- One absolute monotonic deadline covers enumeration and retirement. Entry
  replacement is a same-directory temp file plus `os.replace`; no lock is held
  inside a directory being renamed.

[Side-Effect Policy]
- Writes only the active release entry and retires disposable release roots.
  Never writes `~/.claude`, consent, community or knowledge content.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

from storage_common import (
    MAX_WARNINGS,
    RELEASE_BUDGET_SECONDS,
    SCHEMA,
    _VERSION,
    _binding,
    _bounded_deadline,
    _expired,
    _read_json,
    _remaining,
)

# --------------------------------------------------------------------------- #
# Release entry refresh and superseded release retirement
# --------------------------------------------------------------------------- #

def render_entry(adapter_file, python, scripts, diagnostics=None):
    """The exact release jump script published by `setup.publish_entry`."""
    diagnostics = dict(diagnostics or {})
    return (
        "import os, sys\n"
        f"os.environ['MINDIE_CC_CONFIG'] = {str(adapter_file)!r}\n"
        f"os.environ.update({diagnostics!r})\n"
        "os.environ.pop('PYTHONPATH', None)\n"
        f"sys.path.insert(0, {str(scripts)!r})\n"
        "from bounded import exec_or_wait\n"
        f"exec_or_wait([{str(python)!r}, '-I', "
        f"{str(Path(scripts) / 'mindie_launch.py')!r}, *sys.argv[1:]], dict(os.environ))\n"
    )


def _plugin_releases(plugin_data=None):
    raw = plugin_data if plugin_data is not None else os.environ.get("CLAUDE_PLUGIN_DATA")
    if not isinstance(raw, str) or not os.path.isabs(raw):
        return None
    releases = Path(raw) / "releases"
    return releases if releases.is_dir() else None


def active_version(binding):
    validation = binding.get("product_validation")
    value = validation.get("native_version") if isinstance(validation, dict) else None
    if isinstance(value, str) and _VERSION.match(value):
        return value
    source = binding.get("component_source")
    if isinstance(source, str) and os.path.isabs(source):
        plugin = _read_json(Path(source) / ".claude-plugin" / "plugin.json")
        value = plugin.get("version") if plugin else None
        if isinstance(value, str) and _VERSION.match(value):
            return value
    try:
        from product_contract import installed_version

        value = installed_version()
    except TimeoutError:
        raise
    except Exception:  # noqa: BLE001 - identity probe only; callers keep old behavior
        return None
    return value if isinstance(value, str) and _VERSION.match(value) else None


def _replace_entry(path, text, deadline=None):
    path = Path(path)
    data = text.encode("utf-8")
    try:
        if path.read_bytes() == data:
            return "unchanged"
    except TimeoutError:
        raise
    except OSError:
        pass
    if _expired(deadline):
        raise TimeoutError("release entry refresh budget exhausted")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".entry-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass
    return "refreshed"


def refresh_release_entry(binding, *, previous_python=None, version=None,
                          plugin_data=None, deadline=None):
    """Rebind the active release entry to the newly selected interpreter.

    Only the active version's jump script is touched; a retained rollback
    version keeps its own adapter bytes and interpreter. Byte-identical files
    are not rewritten, so repeated maintenance is a no-op.
    """
    binding = _binding(binding)
    releases = _plugin_releases(plugin_data)
    if releases is None:
        return {"status": "absent"}
    version = version or active_version(binding)
    if version is None:
        return {"status": "unknown_version"}
    entry = releases / version / "entry.py"
    if not entry.is_file():
        return {"status": "absent", "version": version}
    adapter_file = binding.get("adapter_config") or binding.get("config")
    python = binding.get("python")
    scripts = binding.get("runtime_scripts")
    if not all(isinstance(item, str) and os.path.isabs(item)
               for item in (adapter_file, python, scripts)):
        return {"status": "invalid_binding", "version": version}
    diagnostics = binding.get("diagnostics_context")
    text = render_entry(adapter_file, python, scripts,
                        diagnostics if isinstance(diagnostics, dict) else {})
    try:
        outcome = _replace_entry(entry, text, deadline)
    except TimeoutError:
        return {"status": "deferred", "version": version,
                "deferred_in_use": 1, "warnings": ["budget exhausted"]}
    except (OSError, ValueError) as exc:
        return {"status": "deferred", "version": version, "deferred_in_use": 1,
                "warnings": [type(exc).__name__]}
    return {"status": outcome, "version": version,
            "previous_python": previous_python}


def _version_key(name):
    match = _VERSION.match(name)
    return tuple(int(part) for part in match.groups()) if match else None


def prune_release_entries(binding=None, *, plugin_data=None, active=None,
                          keep_versions=2, budget_seconds=RELEASE_BUDGET_SECONDS,
                          deadline=None):
    """Retire superseded `releases/<version>` roots, keeping rollback safety."""
    binding = _binding(binding)
    releases = _plugin_releases(plugin_data)
    account = {"schema": SCHEMA, "status": "absent", "partial": False,
               "warnings": [], "reclaimed_dirs": 0, "deferred_in_use": 0,
               "kept": []}
    if releases is None:
        return account
    limit = _bounded_deadline(budget_seconds, deadline)
    versions = []
    for entry in releases.iterdir():
        key = _version_key(entry.name)
        if key is not None and entry.is_dir() and not entry.is_symlink():
            versions.append((key, entry))
    versions.sort()
    names = {entry.name for _key, entry in versions}
    active = active or active_version(binding)
    if active not in names:
        active = versions[-1][1].name if versions else None
    if active is None:
        account["status"] = "empty"
        return account
    keep = {active}
    keep_count = max(0, int(keep_versions))
    for name in sorted(names):
        if _version_key(name) is not None and _version_key(name) > _version_key(active):
            keep.add(name)  # never retire an installed newer generation
    older = [name for name in sorted(names) if _version_key(name) < _version_key(active)]
    # Newer installed versions and the active one never consume the rollback
    # slot: always retain the immediately previous generation when available.
    if keep_count > 1:
        for name in older[-(keep_count - 1):]:
            keep.add(name)
    try:
        from mindie_knowledge.storage import (
            is_path_used_by_live_process, live_process_prefixes, retire_directory,
        )
    except ImportError as exc:
        account.update(status="unavailable", reason=type(exc).__name__)
        return account
    try:
        live_paths, complete = live_process_prefixes(
            timeout=min(1.0, max(0.1, _remaining(limit) or 1.0)))
    except TimeoutError:
        account.update(status="partial", partial=True)
        account["warnings"].append("budget exhausted")
        return account
    except (OSError, RuntimeError):
        live_paths, complete = None, False
    account["status"] = "completed"
    account["kept"] = sorted(keep)
    for key, entry in versions:
        if entry.name in keep:
            continue
        if _expired(limit):
            account["partial"] = True
            account["warnings"].append("budget exhausted")
            break
        if not complete and live_paths is None:
            account["deferred_in_use"] += 1
            continue
        try:
            if is_path_used_by_live_process(entry, live_paths):
                continue
            outcome = retire_directory(entry, deadline=limit)
        except TimeoutError:
            account["partial"] = True
            account["warnings"].append("budget exhausted")
            break
        except PermissionError:
            account["deferred_in_use"] += 1
            continue
        except (OSError, RuntimeError) as exc:
            account["deferred_in_use"] += 1
            if len(account["warnings"]) < MAX_WARNINGS:
                account["warnings"].append(type(exc).__name__)
            continue
        if outcome.get("status") in {"retired", "trashed_partial"}:
            account["reclaimed_dirs"] += 1
        elif outcome.get("status") == "deferred_in_use":
            account["deferred_in_use"] += 1
        if outcome.get("status") == "absent":
            continue
        if outcome.get("partial"):
            account["partial"] = True
    if account["partial"]:
        account["status"] = "partial"
    return account

