"""Claude Code thin host adapter backed by `mindie-harness` (`API_VERSION = 1`).

[Role] Verifies the bundled seed and exposes `ClaudeHostAdapter`, delegating profile, session, MCP schema, and dispatch behavior to `ManagedMCPAdapter("cc")`.
[Invariants] Top-level import is pure stdlib; `ClaudeHostAdapter` satisfies `mindie_harness.api.HostAdapter`; seed verification checks schema, size, and SHA-256 without execution.
[Timeouts & Locks] Pure path and byte-hash resolution; no locks.
[Side-Effect Policy] Import and verification are strictly read-only.
"""
from collections.abc import Mapping
import hashlib, importlib.util, json, os
from pathlib import Path
from typing import Any

SEED_SCHEMA = "mindie-harness-seed/1"

def plugin_root_dir(source: str | Path | None = None) -> Path:
    """Locate the plugin or installed package root containing `assets/`."""
    if source is not None:
        candidate = Path(source).expanduser().resolve()
        nested = candidate / "plugins" / "mindie-agent"
        return nested if nested.is_dir() else candidate
    here = Path(__file__).resolve().parent
    return here / "resources" if (here / "resources" / "assets" / "seed-lock.json").is_file() else here.parent

def adapter_plugin_root(state_dir: str | Path, source: str | Path | None = None) -> Path:
    """Resolve a `plugin_root` exposing `scripts/transcript.py` in source and wheel layouts."""
    root, here = plugin_root_dir(source), Path(__file__).resolve().parent
    if (root / "scripts" / "transcript.py").is_file():
        return root
    cands = ([Path(source).expanduser().resolve() / "transcript.py"] if source else []) + [here / "transcript.py"]
    cand = next((p.resolve() for p in cands if p.is_file()), None)
    if cand is None:
        return root
    shim = Path(state_dir).expanduser().resolve() / "adapter-root"
    target = shim / "scripts" / "transcript.py"
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.is_file() and ((target.is_symlink() and target.resolve() == cand) or (not target.is_symlink() and target.read_bytes() == cand.read_bytes())):
        return shim
    try:
        target.unlink(missing_ok=True); os.symlink(cand, target); return shim
    except TimeoutError:
        raise
    except OSError:
        tmp = target.with_name(f".transcript.{os.getpid()}.tmp")
        try:
            tmp.write_bytes(cand.read_bytes()); os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
    return shim

def verify_bundled_seed(source: str | Path | None = None) -> dict[str, Any]:
    """Verify `assets/mindie-harness-seed.pyz` against `assets/seed-lock.json`."""
    assets = plugin_root_dir(source) / "assets"
    lock_path = assets / "seed-lock.json"
    if not lock_path.is_file():
        raise ValueError(f"missing bundled harness seed lock: {lock_path}")
    raw = lock_path.read_bytes()
    lock = json.loads(raw.decode("utf-8")) if len(raw) <= 16384 else None
    if not isinstance(lock, dict) or lock.get("schema") != SEED_SCHEMA:
        raise ValueError("unsupported bundled harness seed schema")
    artifact, expected_sha, expected_size = lock.get("artifact"), lock.get("sha256"), lock.get("size_bytes")
    if (not isinstance(artifact, str) or "/" in artifact or "\\" in artifact
            or not isinstance(expected_sha, str) or len(expected_sha) != 64
            or type(expected_size) is not int or expected_size <= 0):
        raise ValueError("invalid seed-lock.json fields")
    seed_path = assets / artifact
    payload = seed_path.read_bytes() if seed_path.is_file() else b""
    if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_sha:
        raise ValueError("bundled harness seed integrity mismatch")
    return dict(lock, path=str(seed_path))

def harness_available() -> bool:
    """Return True when `mindie_harness` (`API_VERSION == 1`) is importable."""
    if importlib.util.find_spec("mindie_harness.api") is None:
        return False
    import mindie_harness.api as api
    return getattr(api, "API_VERSION", None) == 1

class ClaudeHostAdapter:
    """Thin Claude Code `HostAdapter` delegating to the shared MCP gateway."""
    host: str = "cc"

    def __init__(self, *, plugin_root: str | Path | None = None, config_path: str | Path | None = None,
                 data_dir: str | Path | None = None) -> None:
        self._plugin_root = plugin_root_dir(plugin_root)
        self._config_path = Path(config_path).expanduser().resolve() if config_path else None
        self._data_dir = Path(data_dir).expanduser().resolve() if data_dir else None

    def _delegate(self):
        from mindie_harness.mcp.gateway import ManagedMCPAdapter
        return ManagedMCPAdapter(self.host, plugin_root=self._plugin_root, config_path=self._config_path, data_dir=self._data_dir)

    def resolve_profile(self, config_path: str | Path | None = None):
        target = config_path or self._config_path or os.environ.get("MINDIE_CC_CONFIG") or os.environ.get("MINDIE_AGENT_CONFIG")
        return self._delegate().resolve_profile(target)

    def extract_session_identity(self, request: Mapping[str, Any], env: Mapping[str, str]) -> str | None:
        return self._delegate().extract_session_identity(request, env)

    def mcp_tools(self, surface: str, profile: Any = None) -> list[dict[str, Any]]:
        return self._delegate().mcp_tools(surface, profile)

    def dispatch_tool(self, ctx: Any, arguments: dict[str, Any], profile: Any = None) -> dict[str, Any]:
        return self._delegate().dispatch_tool(ctx, arguments, profile)

__all__ = ["ClaudeHostAdapter", "SEED_SCHEMA", "adapter_plugin_root", "harness_available", "plugin_root_dir", "verify_bundled_seed"]
