"""Explicit profile choices backed by the shared Core consent authority."""

from pathlib import Path

from mindie_knowledge import consent_store
from paths import base_config_path, community_config_path

ConsentError = consent_store.ConsentError
SCHEMA = consent_store.SCHEMA
CHOICES = consent_store.CHOICES
REPORTING = consent_store.REPORTING


def consent_path(config: Path | str | None = None) -> Path:
    """The profile-shared consent document (sibling of the base config)."""
    base = Path(config).expanduser() if config is not None else base_config_path()
    return base.parent / "mindie-consent.json"


def load(config: Path | str | None = None) -> dict:
    """Read the explicit saved choice without import, inference or repair."""
    raw = consent_store.read(consent_path(config))
    return dict(
        state=raw["state"],
        choice=raw["choice"],
        reporting=raw["reporting"],
        path=raw["path"],
        error=raw["error"],
    )


def record_choice(choice: str, config: Path | str | None = None) -> str:
    """Explicit user choice; preserves reporting and untouched metadata.

    Raises ``ConsentError`` when the saved authority is corrupt or
    unreadable — a damaged file is never silently cleared by an update.
    """
    consent_store.record_choice(consent_path(config), choice)
    return choice


def record_reporting(value: str, config: Path | str | None = None) -> str:
    """Explicit reporting choice; preserves the contribution choice."""
    consent_store.record_reporting(consent_path(config), value)
    return value


def resolve_community_path() -> Path:
    """Read only the declared community settings authority."""
    return community_config_path()
