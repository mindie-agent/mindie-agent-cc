"""Isolated environment and pre-0.1.5 substrate test shim for thin-plugin tests.

[Role]
Autouse fixture redirects every host/profile/temp environment variable into a
per-test temporary directory so no test touches the developer's real
`~/.config`, `~/.claude`, plugin data, or remote auth state, and provides a
faithful `mindie_harness.runtime.knowledge_binding` test shim strictly when
running against `mindie-harness < 0.1.5`.
[Invariants]
- Every patched variable is absolute and lives under the test `tmp_path`.
- When `mindie_harness.runtime.knowledge_binding` is present (`>= 0.1.5`), zero
  shims are installed and production code runs against the real package.
[Timeouts & Locks]
- No network access; local SQLite/file locks stay inside `tmp_path`.
[Side-Effect Policy]
- Writes only inside `tmp_path`; restores the environment after each test.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import types
from typing import Any

import pytest

_DIRECTORY_VARIABLES = (
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "MINDIE_CONFIG_HOME",
    "MINDIE_DATA_HOME",
    "MINDIE_DIAGNOSTICS_ROOT",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_PLUGIN_DATA",
    "REMOTE_DEV_AUTH_DIR",
    "MINDIE_CHANNEL_BASE_URL",
    "UV_CACHE_DIR",
    "TMPDIR",
)
_FILE_VARIABLES = (
    "MINDIE_CC_CONFIG",
    "MINDIE_AGENT_CONFIG",
    "MINDIE_DIAGNOSTICS_CONFIG",
)

_NEEDS_PRE_015_SHIM = importlib.util.find_spec("mindie_harness.runtime.knowledge_binding") is None

_HOST_TRANSCRIPT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "codex": ("plugins/mindie-agent/scripts/codex_transcript.py", "scripts/codex_transcript.py"),
    "cc": ("scripts/transcript.py",),
    "kimi": ("scripts/kimi_transcript.py", "scripts/transcript.py"),
    "dsh": ("scripts/dsh_transcript.py",),
    "agy": ("plugins/mindie-agent/scripts/agy_transcript.py", "scripts/agy_transcript.py"),
    "wb": ("plugins/mindie-agent/scripts/wb_transcript.py", "scripts/wb_transcript.py"),
    "oc": ("plugins/mindie-agent/scripts/opencode_transcript.py", "scripts/opencode_transcript.py"),
}

_NOOP_ADAPTER_SRC = (
    "from dataclasses import dataclass\n"
    "@dataclass(frozen=True)\n"
    "class FileIdentity:\n"
    "    path: str\n"
    "    dev: int = 0\n"
    "    ino: int = 0\n"
    "    size: int = 0\n"
    "    mtime_ns: int = 0\n"
    "def identify(path): return None\n"
    "def read_material(path, start, **kw): return {'status': 'unchanged', 'start': start, 'end': start, 'digest': '', 'text': '', 'records': 0}\n"
)


def install_pre_015_knowledge_binding_shim() -> None:
    """Install a faithful `interface_contract.md` shim when `mindie-harness < 0.1.5`."""
    if "mindie_harness.runtime.knowledge_binding" in sys.modules:
        return

    def _resolve_adapter(host: str, plugin_root: Path, state_dir: Path, prior_engine: Mapping[str, Any]) -> tuple[Path, bool]:
        for rel in _HOST_TRANSCRIPT_CANDIDATES.get(host, ()):
            cand = (plugin_root / rel).resolve()
            if cand.is_file():
                return cand, True
        prev = prior_engine.get("transcript_adapter")
        if isinstance(prev, str) and Path(prev).is_absolute():
            prev_path = Path(prev)
            if prev_path.is_file() and prev_path.name != "noop_transcript_adapter.py":
                return prev_path.resolve(), True
        noop = (state_dir / "noop_transcript_adapter.py").resolve()
        if not noop.is_file():
            noop.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            noop.write_text(_NOOP_ADAPTER_SRC, encoding="utf-8")
        return noop, False

    def _resolve_redactor(data_dir: Path) -> tuple[Path, bool]:
        exe_name = "gitleaks.exe" if os.name == "nt" else "gitleaks"
        cached = (data_dir / "cache" / "gitleaks" / "8.30.1" / exe_name).resolve()
        if cached.is_file():
            return cached, True
        found = shutil.which("gitleaks")
        if found and Path(found).is_file():
            return Path(found).resolve(), True
        return cached, False

    def ensure_knowledge_binding(
        profile,
        *,
        plugin_root: Path | None = None,
        python_executable: str | Path | None = None,
        install_redactor: bool = False,
        retire_previous: bool = False,
        include_default_feed: bool = False,
        already_locked: bool = False,
    ) -> dict[str, Any]:
        from mindie_harness.atomic import atomic_write_json, atomic_write_text, read_json_safe
        from mindie_harness.locks import FileLock, LockTimeoutError
        from mindie_knowledge.loop import domain_catalog
        from mindie_knowledge.loop.cli import validate_config
        from mindie_knowledge.loop.store import Store

        lock = None
        if not already_locked:
            lock = FileLock(profile.config_path.with_name(f".{profile.config_path.name}.update.lock"), exclusive=True, timeout=5.0)
            try:
                lock.acquire()
            except LockTimeoutError:
                return {"status": "deferred", "reason": "lock_timeout", "reportable": False}

        try:
            existing_raw = read_json_safe(profile.config_path)
            existing = dict(existing_raw) if isinstance(existing_raw, Mapping) else {}
            eff_root = Path(plugin_root).resolve() if plugin_root is not None else Path(profile.plugin_root).resolve()
            prior_eng_raw = read_json_safe(existing["engine_config"]) if isinstance(existing.get("engine_config"), str) else None
            if not isinstance(prior_eng_raw, Mapping):
                prior_eng_raw = read_json_safe(profile.config_path.with_name(f"{profile.config_path.stem}.engine.json"))
            prior_eng = dict(prior_eng_raw) if isinstance(prior_eng_raw, Mapping) else {}

            adapter_path, has_adapter = _resolve_adapter(profile.host, eff_root, profile.state_dir, prior_eng)
            redactor_path, has_redactor = _resolve_redactor(profile.data_dir)
            logical_capture_mode = "public-transcript" if (has_adapter and (has_redactor or install_redactor)) else "disabled"

            env_kroot = os.environ.get("MINDIE_KNOWLEDGE_ROOT")
            raw_kroot = (env_kroot.strip() if isinstance(env_kroot, str) and env_kroot.strip() else None) or prior_eng.get("root")
            knowledge_root = str(Path(raw_kroot).expanduser().resolve()) if isinstance(raw_kroot, str) and raw_kroot else str((profile.data_dir / "knowledge").resolve())
            default_domain = "vllm-ascend"
            community_cfg = str((profile.config_path.parent / "mindie-community.json").resolve())
            consent_cfg = str(profile.consent_path.resolve())
            admission_cfg = str(profile.admission_db.resolve())

            base_engine: dict[str, Any] = {
                "root": knowledge_root,
                "domain": default_domain,
                "admission_path": admission_cfg,
                "transcript_adapter": str(adapter_path),
                "redactor_executable": str(redactor_path),
                "capture_mode": logical_capture_mode,
                "community_config": community_cfg,
            }
            if isinstance(prior_eng.get("feeds"), list) and prior_eng["feeds"]:
                base_engine["feeds"] = list(prior_eng["feeds"])
            elif include_default_feed:
                base_engine["feeds"] = [domain_catalog.get_domain_config(default_domain).to_feed()]
            try:
                validate_config(dict(base_engine))
            except ValueError:
                base_engine["capture_mode"] = "public-transcript"
                validate_config(dict(base_engine))

            store = Store(knowledge_root, default_domain)
            store.close()
            engines_dir = (profile.config_path.parent / "engines" / profile.host).resolve()
            default_text = json.dumps(base_engine, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            default_digest = hashlib.sha256(default_text.encode("utf-8")).hexdigest()[:20]
            default_engine_path = engines_dir / default_digest / f"{default_domain}.engine.json"
            if not default_engine_path.is_file() or default_engine_path.read_text(encoding="utf-8") != default_text:
                atomic_write_text(default_engine_path, default_text)

            prepared = domain_catalog.prepare(
                {"engine_config": str(default_engine_path), "domain_configs": {}},
                base_engine,
                config_directory=str(engines_dir),
                authority_directory=str((profile.config_path.parent / "authorities" / profile.host).resolve()),
                consent_config=consent_cfg,
                public_feeds=True,
            )
            domain_configs = dict(prepared["domain_configs"])
            domain_configs[default_domain] = str(default_engine_path)

            retirement_report: dict[str, Any] | None = None
            if retire_previous and (existing.get("engine_config") or existing.get("domain_configs")):
                try:
                    retirement_report = domain_catalog.retire_changed(
                        existing,
                        {"engine_config": str(default_engine_path), "domain_configs": domain_configs},
                        include_default=True,
                    )
                    if isinstance(retirement_report, Mapping) and retirement_report.get("status") == "deferred":
                        return {
                            "status": "deferred",
                            "reason": "knowledge_service_busy",
                            "reportable": False,
                            "retirement": dict(retirement_report),
                        }
                except TimeoutError:
                    raise
                except Exception:
                    pass

            binding_fields = {
                "host": profile.host,
                "engine_config": str(default_engine_path),
                "domain_configs": domain_configs,
                "community_config": community_cfg,
                "consent_config": consent_cfg,
                "admission_path": admission_cfg,
            }
            if not already_locked:
                updated = dict(existing)
                if python_executable is not None:
                    updated.setdefault("python", str(python_executable))
                updated.setdefault("source_root", str(eff_root))
                updated.update(binding_fields)
                atomic_write_json(profile.config_path, updated)

            return {
                "status": "configured",
                "engine_config": str(default_engine_path),
                "domain_configs": domain_configs,
                "domains": sorted(domain_configs),
                "capture_mode": logical_capture_mode,
                "transcript_adapter": str(adapter_path),
                "standalone_adapter": not has_adapter,
                "redactor_executable": str(redactor_path),
                "binding_fields": binding_fields,
                "retirement": retirement_report,
                "reportable": False,
            }
        finally:
            if lock is not None:
                lock.release()

    def run_stop_capture(
        profile,
        event: Mapping[str, Any],
        *,
        plugin_root: Path | None = None,
        timeout_sec: float = 6.0,
    ) -> dict[str, Any]:
        from mindie_harness.atomic import read_json_safe
        from mindie_harness.process.supervisor import Deadline
        from mindie_harness.sharing import sharing_status
        from mindie_knowledge.loop.activation import Admission
        from mindie_knowledge.loop.cli import config_at
        from mindie_knowledge.loop.handoff import _store_path, accept_stop, prepare_schema

        deadline = Deadline.from_timeout(timeout_sec)
        cwd = event.get("cwd") if isinstance(event, Mapping) else None
        status = sharing_status(profile, cwd=cwd if isinstance(cwd, str) else None)
        if status.get("tier") not in {"contribute", "local-only"} or not status.get("cwd_in_scope"):
            return {"stage": "inert", "reason": "sharing-disabled"}
        session_id = event.get("session_id") if isinstance(event, Mapping) else None
        if not isinstance(session_id, str) or not session_id.strip():
            return {"stage": "rejected", "reason": "missing-identity"}

        admission = Admission(profile.admission_db)
        inspected = admission.inspect(session_id)
        if inspected.get("status") == "inactive":
            return {"stage": "inert", "reason": "not-activated"}
        if inspected.get("status") == "unavailable":
            return {"stage": "unavailable", "reason": "admission-unreadable"}

        existing = admission.knowledge_lease(session_id) if profile.admission_db.is_file() else None
        if existing is None:
            profile.admission_db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            lease = admission.activate(session_id, project_root=str(cwd), root_session=session_id, not_before=0.0)
        else:
            lease = existing

        cfg = read_json_safe(profile.config_path)
        engine_cfg = cfg.get("engine_config") if isinstance(cfg, Mapping) else None
        if not isinstance(engine_cfg, str) or not Path(engine_cfg).is_file():
            engine_cfg = ensure_knowledge_binding(profile, plugin_root=plugin_root)["engine_config"]
        if not _store_path(config_at(engine_cfg)).is_file():
            prepare_schema(engine_cfg)

        payload = dict(event)
        payload.setdefault("hook_event_name", "Stop")
        payload["mindie_activation"] = lease["token"]
        return accept_stop(engine_cfg, payload, timeout=max(0.1, deadline.remaining()))

    mod = types.ModuleType("mindie_harness.runtime.knowledge_binding")
    mod.__spec__ = importlib.machinery.ModuleSpec("mindie_harness.runtime.knowledge_binding", loader=None)
    mod.ensure_knowledge_binding = ensure_knowledge_binding  # type: ignore[attr-defined]
    mod.run_stop_capture = run_stop_capture  # type: ignore[attr-defined]
    import mindie_harness.runtime as runtime_pkg

    setattr(runtime_pkg, "knowledge_binding", mod)
    sys.modules["mindie_harness.runtime.knowledge_binding"] = mod

    import mindie_knowledge

    if not callable(getattr(mindie_knowledge, "rpc", None)):
        def _shim_select_engine_config(cfg_or_profile=None):
            from mindie_harness.atomic import read_json_safe
            from mindie_harness.paths import ProfileRef

            prof = cfg_or_profile if isinstance(cfg_or_profile, ProfileRef) else ProfileRef.resolve("cc", config_path=cfg_or_profile)
            doc = read_json_safe(prof.config_path)
            if not isinstance(doc, Mapping) or not isinstance(doc.get("engine_config"), str) or not Path(doc["engine_config"]).is_file():
                return ensure_knowledge_binding(prof)
            return dict(doc)

        def _shim_ensure_service(config=None, **_kw):
            return config or {}

        def _shim_rpc(service, operation: str | None = None, arguments: Mapping[str, Any] | None = None, **_kw):
            from mindie_harness.atomic import read_json_safe
            from mindie_knowledge.loop.store import Store

            if isinstance(service, str) and isinstance(operation, Mapping):
                op_name, args = service, dict(operation)
                svc: Mapping[str, Any] = {}
            else:
                op_name = str(operation or "")
                args = dict(arguments or {})
                svc = service if isinstance(service, Mapping) else {}
            domain = str(args.get("domain") or "vllm-ascend")
            domain_cfgs = svc.get("domain_configs") if isinstance(svc.get("domain_configs"), Mapping) else {}
            eng_path = domain_cfgs.get(domain) or svc.get("engine_config")
            eng = read_json_safe(eng_path) if isinstance(eng_path, str) else None
            root = str((eng or {}).get("root") or svc.get("root") or (Path(os.environ["MINDIE_DATA_HOME"]) / "knowledge"))
            store = Store(root, domain)
            try:
                if op_name == "knowledge_query":
                    return store.query(
                        args.get("query", ""),
                        limit=int(args.get("limit", 5)),
                        conditions=args.get("conditions"),
                        token_budget=int(args.get("token_budget", 8000)),
                    )
                if op_name == "knowledge_explain":
                    return store.explain(
                        ref=args.get("ref"),
                        refs=args.get("refs"),
                        conditions=args.get("conditions"),
                        known_refs=args.get("known_refs"),
                        token_budget=int(args.get("token_budget", 16000)),
                    )
                return {"status": "ok", "operation": op_name}
            finally:
                store.close()

        setattr(mindie_knowledge, "select_engine_config", _shim_select_engine_config)
        setattr(mindie_knowledge, "ensure_service", _shim_ensure_service)
        setattr(mindie_knowledge, "rpc", _shim_rpc)


if _NEEDS_PRE_015_SHIM:
    install_pre_015_knowledge_binding_shim()


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point every host/profile/temp variable at one fresh temporary tree."""
    for name in _DIRECTORY_VARIABLES:
        target = tmp_path / name.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(target))
    for name in _FILE_VARIABLES:
        target = tmp_path / "config" / f"{name.lower()}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(target))
    monkeypatch.delenv("_MINDIE_UV_FLAGS", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("MINDIE_KNOWLEDGE_ROOT", raising=False)
    if _NEEDS_PRE_015_SHIM:
        shim_dir = tmp_path / "_pre015_shim"
        shim_dir.mkdir(parents=True, exist_ok=True)
        tests_dir = str(Path(__file__).resolve().parent)
        (shim_dir / "sitecustomize.py").write_text(
            f"import sys\nif {tests_dir!r} not in sys.path:\n    sys.path.insert(0, {tests_dir!r})\n"
            "import conftest\nconftest.install_pre_015_knowledge_binding_shim()\n",
            encoding="utf-8",
        )
        existing_py_path = os.environ.get("PYTHONPATH", "")
        monkeypatch.setenv("PYTHONPATH", f"{shim_dir}{os.pathsep}{existing_py_path}" if existing_py_path else str(shim_dir))
    return tmp_path
