"""Stdlib first-use and slash prompt_id consumption. No knowledge import.

The persistent choice lives in the profile-shared consent document
(``consent``); the legacy marker below only deduplicates native slash
prompt ids and is a one-time migration source, never a consent source.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from paths import first_use_path, state_dir

import consent

CHOICES = ("contribute", "disabled")


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def first_use():
    saved = consent.load()
    if saved["state"] == "ok" and saved["choice"] in consent.CHOICES:
        return saved["choice"]
    return None


def set_first_use(choice: str) -> str:
    if choice not in CHOICES:
        raise ValueError("configure contribution or explicitly disable it; read-only/later are legacy data only")
    return consent.record_choice(choice)


def consume_prompt(prompt_id: str) -> bool:
    """True once per native prompt_id (unconfigured path)."""
    if not isinstance(prompt_id, str) or not prompt_id or len(prompt_id) > 256:
        raise ValueError("invalid prompt_id")
    path = state_dir() / "slash-prompts.json"
    data = _load(path)
    seen = data.get("consumed")
    if not isinstance(seen, list):
        seen = []
    if prompt_id in seen:
        return False
    seen.append(prompt_id)
    data["consumed"] = seen[-256:]
    _store(path, data)
    return True


def configuration_required() -> dict:
    """Incomplete configuration is a state, never a product tier."""
    return dict(
        configured=False,
        experience="needs-configuration",
        sharing=dict(configured=False, enabled=False),
        first_use=first_use(),
        choices=[],
        required=["runtime", "public_repository", "account", "project_scope"],
        setup="python3 scripts/setup.py --knowledge-python <venv-python>",
        note=("Supply only missing destination and scope information. Reuse "
              "previously approved values. Installation or task binding alone "
              "does not make the experience loop available. Explicitly disabled "
              "and legacy declined settings remain disabled until changed."),
    )
