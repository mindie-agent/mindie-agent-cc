"""Stdlib first-use and slash prompt_id consumption. No knowledge import."""

from __future__ import annotations

import json
import os
from pathlib import Path

from paths import first_use_path, state_dir

CHOICES = ("contribute", "read-only", "later")


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def first_use():
    data = _load(first_use_path())
    choice = data.get("choice")
    return choice if choice in CHOICES else None


def set_first_use(choice: str) -> str:
    if choice not in CHOICES:
        raise ValueError("choice must be contribute, read-only, or later")
    data = _load(first_use_path())
    data["choice"] = choice
    _store(first_use_path(), data)
    return choice


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


def three_choices() -> dict:
    return dict(
        configured=False,
        sharing=dict(configured=False, enabled=False),
        first_use=first_use(),
        choices=[
            dict(
                id="contribute",
                recommended=True,
                summary="Contribute public experience for the current project",
                next=(
                    "/mindie-agent:sharing-enable --repository owner/repo "
                    "--account USER --project-root /absolute/path --visibility public"
                ),
            ),
            dict(
                id="read-only",
                summary="Read-only knowledge; no contribution",
                next="Run /mindie-agent:init read-only",
            ),
            dict(
                id="later",
                summary="Configure later (sharing stays off)",
                next="Run /mindie-agent:init later",
            ),
        ],
        setup="python3 scripts/setup.py --config ~/.config/mindie-agent/cc.json",
        note=(
            "Enabling contribution requires explicit public repository, account, "
            "project root, and visibility. There is no automatic yes."
        ),
    )
