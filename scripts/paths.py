"""MindIE-owned paths for the Claude Code adapter. Never writes ~/.claude.

[Role]
Resolves MindIE-owned configuration, state, admission, and community file paths
and extracts read-only provider/transport environment variables from Claude
settings.

[Invariants]
- Never writes to `~/.claude` or mutates host-owned Claude Code settings.
- `MINDIE_CC_CONFIG` overrides the default adapter config path when set.

[Timeouts & Locks]
- Pure path resolution and bounded local JSON reads.

[Side-Effect Policy]
- Strictly read-only path and environment resolution.
"""

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


def service_launch_context(adapter_file: Path, adapter: dict) -> dict:
    """Declare service bindings without reading credentials or requiring Claude."""
    from mindie_knowledge.loop.service_context import validate
    from mindie_diagnostics.logging import default_root
    from diagnostic_support import reporting_config_path

    selected = adapter.get("claude_config_dir")
    profile = Path(selected or os.environ.get("CLAUDE_CONFIG_DIR")
                   or Path.home() / ".claude")
    paths = {
        CONFIG_ENV: str(adapter_file.expanduser().absolute()),
        "CLAUDE_CONFIG_DIR": str(profile.expanduser().absolute()),
        "MINDIE_DIAGNOSTICS_CONFIG": str(
            adapter.get("diagnostics_context", {}).get("MINDIE_DIAGNOSTICS_CONFIG")
            or reporting_config_path(adapter["base_config"])),
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
    names the installation profile.
    Consent and community choices always remain beside this shared profile.
    """
    from entry import _read_regular_json

    path = config_path()
    state, data = _read_regular_json(path)
    if state == "missing":
        return path
    if state is not None:
        raise ValueError("installation configuration is unreadable or corrupt; consent path unchanged")
    base = data.get("base_config")
    if not isinstance(base, str) or not os.path.isabs(base):
        raise ValueError("installation configuration lacks an absolute base_config")
    base = Path(base)
    if base != path:
        state, selected = _read_regular_json(base)
        if state is not None or selected.get("base_config") != str(base):
            raise ValueError("base installation configuration is unavailable; consent path unchanged")
    return base


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
