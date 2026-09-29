"""MindIE-owned paths for the Claude Code adapter. Never writes ~/.claude."""

from __future__ import annotations

import json
import os
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "mindie-agent"
MARKETPLACE = "mindie-agent-cc"
PLUGIN_QUALIFIED = f"{PLUGIN_ID}@{MARKETPLACE}"
CONFIG_ENV = "MINDIE_CC_CONFIG"


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser().absolute()
    return (
        Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        / "mindie-agent"
        / "cc.json"
    ).absolute()


def load_adapter_config() -> dict:
    path = config_path()
    if not path.is_file():
        raise FileNotFoundError(
            "MindIE Claude Code adapter configuration is missing. "
            "Run: python3 scripts/setup.py --config PATH"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("adapter configuration must be one JSON object")
    return data


def base_config_path() -> Path:
    """The stable base adapter configuration.

    Hook/MCP children of a real install run with ``MINDIE_CC_CONFIG`` pointing
    at a generation copy under ``state/update/generations/<sha>/config/``; the
    copy's ``base_config`` key names the stable profile file. Profile-shared
    authorities (consent, community settings) anchor at the base config's
    directory, never at a per-generation directory, so an upgrade generation
    cannot strand them. Falls back to the effective config when no absolute
    ``base_config`` is recorded (bootstrap or unconfigured layouts).
    """
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return path
    base = data.get("base_config") if isinstance(data, dict) else None
    if isinstance(base, str) and os.path.isabs(base):
        return Path(base)
    return path


def engine_config_path(config=None) -> Path:
    config = config if config is not None else load_adapter_config()
    value = config.get("engine_config")
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ValueError("adapter configuration lacks an absolute engine_config")
    return Path(value)


def load_engine_config(config=None) -> dict:
    path = engine_config_path(config)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not {"root", "domain"} <= set(data):
        raise ValueError("engine configuration requires root and domain")
    if "session_activation" in data:
        raise ValueError("session_activation was removed; use admission_path")
    return data


def admission_path(engine=None) -> Path:
    engine = engine if engine is not None else load_engine_config()
    value = engine.get("admission_path")
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ValueError("engine configuration lacks an absolute admission_path")
    path = Path(value)
    if path.suffix.lower() in {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}:
        raise ValueError("admission_path must be a SQLite file, not an adapter config")
    return path


def community_config_path(config=None) -> Path:
    config = config if config is not None else load_adapter_config()
    value = config.get("community_config")
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ValueError("adapter configuration lacks an absolute community_config")
    return Path(value)


def default_state_dir() -> Path:
    return (
        Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        / "mindie-agent"
        / "state-cc"
    )


def state_dir(config=None) -> Path:
    try:
        config = config if config is not None else load_adapter_config()
    except FileNotFoundError:
        return default_state_dir()
    value = config.get("state_dir")
    if isinstance(value, str) and os.path.isabs(value):
        return Path(value)
    engine = load_engine_config(config)
    return Path(engine["root"]) / "cc-adapter"


def first_use_path() -> Path:
    return state_dir() / "cc.first-use.json"


def plugin_root_from_env() -> Path:
    value = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if isinstance(value, str) and value.strip():
        return Path(value)
    return PLUGIN_ROOT


def configured_python(config=None) -> str:
    config = config if config is not None else load_adapter_config()
    python = config.get("python")
    if not isinstance(python, str) or not python:
        raise ValueError("adapter configuration has no runtime interpreter")
    return python


def claude_config_dir(config=None):
    try:
        config = config if config is not None else load_adapter_config()
    except FileNotFoundError:
        return None
    value = config.get("claude_config_dir")
    if isinstance(value, str) and os.path.isabs(value):
        return value
    return None
