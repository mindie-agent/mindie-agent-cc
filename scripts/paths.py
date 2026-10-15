"""MindIE-owned paths for the Claude Code adapter. Never writes ~/.claude."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

PLUGIN_ID = "mindie-agent"
MARKETPLACE = "mindie-agent-cc"
PLUGIN_QUALIFIED = f"{PLUGIN_ID}@{MARKETPLACE}"
CONFIG_ENV = "MINDIE_CC_CONFIG"
NATIVE_PROVIDER_ENV = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "CLAUDE_CODE_EFFORT_LEVEL", "MAX_THINKING_TOKENS",
)
NATIVE_RUNTIME_ENV = ("CLAUDE_CODE_GIT_BASH_PATH",)
TRANSPORT_ENV = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
)


def service_launch_context(adapter_file: Path, adapter: dict, previous=None) -> dict:
    """Declare service bindings without reading credentials or requiring Claude."""
    from mindie_knowledge.loop.service_context import validate
    from diagnostic_fallback import default_root
    from diagnostic_support import reporting_config_path

    if previous is not None:
        # A scheduler's ambient environment is not a new installation choice.
        selected_paths = validate({"service_launch_context": previous})["paths"]
        selected_paths[CONFIG_ENV] = str(adapter_file.expanduser().absolute())
        return validate({"service_launch_context": {
            "paths": selected_paths,
            "environment": [*NATIVE_PROVIDER_ENV, *NATIVE_RUNTIME_ENV, *TRANSPORT_ENV],
        }})
    selected = adapter.get("claude_config_dir")
    profile = Path(selected or os.environ.get("CLAUDE_CONFIG_DIR")
                   or Path.home() / ".claude")
    paths = {
        CONFIG_ENV: str(adapter_file.expanduser().absolute()),
        "CLAUDE_CONFIG_DIR": str(profile.expanduser().absolute()),
        "MINDIE_DIAGNOSTICS_CONFIG": str(reporting_config_path()),
        "MINDIE_DIAGNOSTICS_ROOT": str(default_root().expanduser().absolute()),
    }
    explicit = os.environ.get("MINDIE_CC_BIN")
    native = shutil.which(explicit or "claude")
    if explicit and native is None:
        requested = Path(explicit).expanduser()
        if not requested.is_absolute():
            raise ValueError("explicit native executable is unavailable")
        native = str(requested)
    if native:
        paths["MINDIE_CC_BIN"] = str(Path(native).expanduser().absolute())
    return validate({"service_launch_context": {
        "paths": paths,
        "environment": [*NATIVE_PROVIDER_ENV, *NATIVE_RUNTIME_ENV, *TRANSPORT_ENV],
    }})


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
            "Start a new Claude session with the native MindIE plugin enabled."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("adapter configuration must be one JSON object")
    return data


def base_config_path() -> Path:
    """The stable base adapter configuration.

    Hook/MCP children use a fixed runtime-specific binding. Its ``base_config``
    names the shared business profile, which is not a release selector.
    Consent and community choices always remain beside this shared profile.
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
