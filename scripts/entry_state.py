"""Persistent explicit choice and native slash prompt_id deduplication.

[Role]
Deduplicates Claude Code `UserPromptExpansion` `prompt_id` deliveries and
persists explicit user contribution choices via `consent.py`.

[Invariants]
- Preserves unreadable or corrupt slash-prompt receipt files untouched and
  raises `ValueError` rather than silently overwriting them.

[Timeouts & Locks]
- Uses atomic temp-file replacement in `state_dir()`.

[Side-Effect Policy]
- Writes prompt deduplication receipts under `state_dir()` and updates
  `mindie-consent.json`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from paths import state_dir

import consent

CHOICES = ("contribute", "disabled")


def _load(path: Path) -> dict:
    from entry import _read_regular_json

    state, data = _read_regular_json(path)
    if state == "missing":
        return {}
    if state is not None:
        raise ValueError("slash prompt receipt is unreadable or corrupt; preserved unchanged")
    return data


def _store(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except TimeoutError:
        raise
    except OSError:
        pass


def saved_choice():
    saved = consent.load()
    if saved["state"] == "missing":
        return None
    if saved["state"] != "ok":
        raise consent.ConsentError("saved consent is unavailable; preserved unchanged",
                                   state=saved["state"])
    return saved["choice"]


def set_choice(choice: str) -> str:
    if choice not in CHOICES:
        raise ValueError("configure contribution or explicitly disable it")
    return consent.record_choice(choice)


def consume_prompt(prompt_id: str) -> bool:
    """True once per native prompt_id (unconfigured path)."""
    if not isinstance(prompt_id, str) or not prompt_id or len(prompt_id) > 256:
        raise ValueError("invalid prompt_id")
    path = state_dir() / "slash-prompts.json"
    data = _load(path)
    seen = data.get("consumed", [])
    if not isinstance(seen, list) or not all(isinstance(item, str) for item in seen):
        raise ValueError("slash prompt receipt has an invalid consumed list; preserved unchanged")
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
        first_use=saved_choice(),
        choices=[],
        required=["runtime", "public_repository", "account", "project_scope"],
        setup="Start a new Claude session with the MindIE native plugin enabled",
        note=("Supply only missing destination and scope information. Reuse "
              "previously approved values. Installation or task binding alone "
              "does not make the experience loop available. Explicitly disabled "
              "settings remain disabled until changed. Contribution authorizes "
              "scoped knowledge PRs and sanitized product fault Issues together."),
    )
