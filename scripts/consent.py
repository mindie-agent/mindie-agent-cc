"""Explicit profile choices backed by the shared Core consent authority.

[Role]
Reads and writes the profile-shared `mindie-consent.json` file via
`mindie_knowledge.consent_store`.

[Invariants]
- Reads explicit saved choices without implicit inference or silent repair.
- Resolves community configuration path alongside the base adapter config.

[Timeouts & Locks]
- Uses shared core `consent_store` atomic file operations.

[Side-Effect Policy]
- Mutates only `mindie-consent.json` when explicitly saving user consent choices.
"""

import json
from pathlib import Path

from paths import base_config_path, community_config_path

try:
    from mindie_knowledge import consent_store
except ModuleNotFoundError:
    try:
        _py = json.loads(base_config_path().read_text(encoding="utf-8")).get("python")
        if _py:
            _root = Path(_py).parent.parent
            for _sp in (_root / "Lib" / "site-packages", *_root.glob("lib/python*/site-packages")):
                if _sp.is_dir():
                    import sys as _sys

                    _sys.path.insert(0, str(_sp))
    except Exception:
        pass
    from mindie_knowledge import consent_store

ConsentError = consent_store.ConsentError
SCHEMA = consent_store.SCHEMA
CHOICES = consent_store.CHOICES


def consent_path(config: Path | str | None = None) -> Path:
    """The profile-shared consent document (sibling of the base config)."""
    base = Path(config).expanduser() if config is not None else base_config_path()
    return base.parent / "mindie-consent.json"


def load(config: Path | str | None = None) -> dict:
    """Read the explicit saved choice without import, inference or repair."""
    return consent_store.read(consent_path(config))


def record_choice(choice: str, config: Path | str | None = None) -> str:
    """Explicit user choice; preserves reporting and untouched metadata.

    Raises ``ConsentError`` when the saved authority is corrupt or
    unreadable — a damaged file is never silently cleared by an update.
    """
    consent_store.record_choice(consent_path(config), choice)
    return choice


def resolve_community_path() -> Path:
    """Read only the declared community settings authority."""
    return community_config_path()
