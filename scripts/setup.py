#!/usr/bin/env python3
"""Thin Claude Code setup CLI delegating shared behavior to `mindie_harness`.

[Role] Provisions `cc.json` and 9-domain engines via `ensure_knowledge_binding`, handles legacy `--source`/`--activate` upgrade handoffs from pre-0.1.30 updaters, configures sharing, runs doctor and runtime update, and emits validated `mindie-plugin-release/1` metadata.
[Invariants] `release-metadata` validates compatibility, AST `code_paths`, `test_ids`, and local evidence; `--activate` enforces `<state_dir>/update/operation.lock` plus CAS without overwriting `.retired` canonical engine configs in place.
[Timeouts & Locks] Legacy `--activate` acquires `operation.lock` (`15s`) and `.update.lock` (`5s`); doctor/update delegates own bounded deadlines.
[Side-Effect Policy] Profile and staged candidate writes stay inside MindIE-owned documents.
"""
import argparse, ast, hashlib, importlib.metadata, importlib.util, json, os, subprocess, sys, time, tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from host_adapter import adapter_plugin_root, plugin_root_dir  # noqa: E402
PLUGIN_ROOT, _NO_WIN = plugin_root_dir(), (0x08000000 if os.name == "nt" else 0)
REPOSITORY, HARNESS, DECLARATION, DEFAULT_DOMAIN = "mindie-agent/mindie-agent-cc", "claude-code", "harness-compatibility.json", "vllm-ascend"
SUBCOMMANDS = frozenset({"install", "init", "sharing", "doctor", "update", "release-metadata"})

def _doc(path: Path) -> dict | None:
    from mindie_harness.atomic import read_json_safe
    return val if isinstance(val := read_json_safe(path), dict) else None

def _profile(config: str | Path | None = None):
    from mindie_harness.paths import ProfileRef
    return ProfileRef.resolve("cc", plugin_root=PLUGIN_ROOT, config_path=str(config) if config else os.environ.get("MINDIE_CC_CONFIG"))

def _emit(document: dict) -> None:
    sys.stdout.write(json.dumps(document, sort_keys=True) + "\n"); sys.stdout.flush()

def _call_binding(profile, *, plugin_root: Path, python: str, retire_previous: bool, root: str | None = None) -> dict:
    from mindie_harness.runtime.knowledge_binding import ensure_knowledge_binding
    prior = os.environ.get("MINDIE_KNOWLEDGE_ROOT")
    if root:
        os.environ["MINDIE_KNOWLEDGE_ROOT"] = str(Path(root).expanduser().resolve())
    try:
        return ensure_knowledge_binding(profile, plugin_root=plugin_root, python_executable=python,
                                        install_redactor=False, retire_previous=retire_previous, already_locked=True)
    finally:
        if root:
            os.environ.pop("MINDIE_KNOWLEDGE_ROOT", None) if prior is None else os.environ.__setitem__("MINDIE_KNOWLEDGE_ROOT", prior)

def initialize_profile(root: str | None = None, domain: str = DEFAULT_DOMAIN) -> dict:
    """Create the `cc` profile and 9-domain knowledge bindings under one `.update.lock`."""
    from mindie_harness.atomic import atomic_write_json
    from mindie_harness.locks import FileLock, LockTimeoutError

    profile = _profile()
    lock = FileLock(profile.config_path.with_name(f".{profile.config_path.name}.update.lock"), exclusive=True, timeout=5.0)
    try:
        lock.acquire()
    except LockTimeoutError:
        return {"status": "deferred", "reason": "lock_timeout", "config_path": str(profile.config_path), "reportable": False}
    try:
        cfg = _doc(profile.config_path) or {}
        python = sys.executable if importlib.util.find_spec("mindie_harness") is not None else (
            cfg.get("python") if isinstance(cfg.get("python"), str) and Path(cfg["python"]).is_file() else sys.executable)
        res = _call_binding(profile, plugin_root=adapter_plugin_root(profile.state_dir), python=python, retire_previous=True, root=root)
        if res.get("status") != "configured":
            return {**res, "config_path": str(profile.config_path), "reportable": False}
        fields = dict(res["binding_fields"])
        engine_config, domains = str(fields["engine_config"]), dict(fields["domain_configs"])
        comm = cfg["community_config"] if isinstance(cfg.get("community_config"), str) and os.path.isabs(cfg["community_config"]) else str(fields["community_config"])
        updated = {**cfg, **fields, "schema": "mindie-cc-installation/1", "facade_protocol": 1, "host": "cc", "python": python,
                   "runtime_scripts": str(HERE), "base_config": str(profile.config_path), "adapter_config": str(profile.config_path),
                   "engine_config": engine_config, "domain_configs": domains, "state_dir": str(profile.state_dir),
                   "community_config": comm, "source_root": str(PLUGIN_ROOT)}
        if root:
            updated["knowledge_root"] = str(Path(root).expanduser().resolve())
        atomic_write_json(profile.config_path, updated)
    finally:
        lock.release()
    resolved_root = str((_doc(Path(engine_config)) or {}).get("root") or (Path(root).expanduser().resolve() if root else (profile.data_dir / "knowledge").resolve()))
    return dict(status="configured", config_path=str(profile.config_path), engine_config=engine_config,
                domain_configs=domains, root=resolved_root, domain=domain, admission_path=str(fields["admission_path"]), reportable=False)

def _legacy_prepare(args) -> int:
    from mindie_harness.atomic import atomic_write_json
    from mindie_harness.locks import FileLock, LockTimeoutError

    profile = _profile(args.config)
    config, selected, source_root = profile.config_path, _doc(profile.config_path), Path(args.source).resolve()
    os.environ["MINDIE_CC_CONFIG"] = str(config)
    state_dir = selected["state_dir"] if isinstance(selected, dict) and isinstance(selected.get("state_dir"), str) and os.path.isabs(selected["state_dir"]) else str(profile.state_dir)
    community = selected["community_config"] if isinstance(selected, dict) and isinstance(selected.get("community_config"), str) and os.path.isabs(selected["community_config"]) else str(config.with_name("mindie-community.json"))
    raw_ep = (selected or {}).get("engine_config")
    prev_engine = (_doc(Path(raw_ep)) if isinstance(raw_ep, str) and os.path.isabs(raw_ep) else None) or _doc(config.with_name("cc.engine.json")) or {}
    knowledge_root = str(Path(args.root).expanduser().resolve()) if args.root else str(prev_engine.get("root") or (profile.data_dir / "knowledge").resolve())
    commands = dict((selected or {}).get("component_commands") or {})
    rebound = commands
    if commands and isinstance(selected, dict):
        from mindie_knowledge.component_bindings import managed_commands, rebind_commands, validate_command_floor
        versions = {name: {"version": importlib.metadata.version(name)} for name, _py, _mod in managed_commands(selected).values()}
        validate_command_floor(selected, versions)
        rebound = rebind_commands(selected, sys.executable, versions)
    key = hashlib.sha256(f"{sys.executable}:{HERE}:{json.dumps(commands, sort_keys=True)}:{json.dumps((selected or {}).get('domain_configs') or {}, sort_keys=True)}".encode()).hexdigest()[:16]
    binding_dir = Path(state_dir) / "installations" / key
    adapter_file = binding_dir / "cc.adapter.json"
    candidate = {**(selected or {}), "schema": "mindie-cc-installation/1", "facade_protocol": 1, "host": "cc", "python": sys.executable,
                 "runtime_scripts": str(HERE), "base_config": str(config), "adapter_config": str(adapter_file),
                 "engine_config": str(raw_ep if isinstance(raw_ep, str) and os.path.isabs(raw_ep) else binding_dir / f"{DEFAULT_DOMAIN}.engine.json"),
                 "state_dir": state_dir, "community_config": community, "source_root": str(source_root),
                 "component_source_commands": commands, "domain_source_configs": dict((selected or {}).get("domain_configs") or {})}
    if args.root:
        candidate["knowledge_root"] = knowledge_root
    if rebound:
        candidate["component_commands"] = rebound
    if args.claude_config_dir:
        candidate["claude_config_dir"] = str(Path(args.claude_config_dir).expanduser().resolve())
    if selected is not None:
        atomic_write_json(adapter_file, candidate)
        _emit({"base_config": str(config), "config": str(adapter_file), "status": "ready" if selected == candidate else "prepared"}); return 0
    lock = FileLock(config.with_name(f".{config.name}.update.lock"), exclusive=True, timeout=5.0)
    try:
        lock.acquire()
    except LockTimeoutError:
        _emit({"base_config": str(config), "config": str(adapter_file), "status": "deferred", "reason": "lock_timeout"}); return 0
    try:
        res = _call_binding(profile, plugin_root=adapter_plugin_root(Path(state_dir), source_root), python=sys.executable, retire_previous=True, root=knowledge_root if args.root else None)
        if res.get("status") != "configured":
            _emit({**res, "base_config": str(config), "config": str(adapter_file)}); return 0
        fields = dict(res["binding_fields"])
        candidate.update(**fields, engine_config=str(fields["engine_config"]), domain_configs=dict(fields["domain_configs"]), community_config=community)
        atomic_write_json(adapter_file, candidate); atomic_write_json(config, candidate)
    finally:
        lock.release()
    _emit({"base_config": str(config), "config": str(adapter_file), "status": "ready"}); return 0

def _legacy_activate(candidate_path: Path) -> int:
    from mindie_harness.atomic import atomic_write_json
    from mindie_harness.locks import FileLock, LockTimeoutError

    started, candidate = time.monotonic(), _doc(candidate_path.expanduser().resolve())
    req = ("base_config", "state_dir", "community_config", "python", "runtime_scripts", "engine_config", "adapter_config")
    if not candidate or candidate.get("schema") != "mindie-cc-installation/1" or candidate.get("facade_protocol") != 1 or any(not isinstance(candidate.get(k), str) or not os.path.isabs(candidate[k]) for k in req):
        raise ValueError("invalid candidate installation binding")
    config = Path(candidate["base_config"])
    op_lock = FileLock(Path(candidate["state_dir"]) / "update" / "operation.lock", exclusive=True, timeout=15.0)
    upd_lock = FileLock(config.with_name(f".{config.name}.update.lock"), exclusive=True, timeout=5.0)
    try:
        op_lock.acquire()
        try:
            upd_lock.acquire()
        except LockTimeoutError:
            _emit({"status": "deferred", "stage": "active_operation", "switched": False}); return 0
        try:
            previous = _doc(config) or {}
            if previous == candidate:
                _emit({"status": "current", "switched": False}); return 0
            if "domain_source_configs" in candidate and (previous.get("domain_configs") or {}) != candidate["domain_source_configs"]:
                _emit({"status": "deferred", "stage": "domain_routes_changed", "switched": False}); return 0
            if "component_source_commands" in candidate and (previous.get("component_commands") or {}) != candidate["component_source_commands"]:
                _emit({"status": "deferred", "stage": "component_commands_changed", "switched": False}); return 0
            if any(k in previous and previous[k] != candidate[k] for k in ("base_config", "state_dir", "community_config")):
                raise ValueError("candidate changes installation data identity")
            old_ep = previous.get("engine_config")
            old_eng = _doc(Path(old_ep)) if isinstance(old_ep, str) and Path(old_ep).is_file() else {}
            old_root, old_dom = (old_eng or {}).get("root"), (old_eng or {}).get("domain")
            had_legacy_dir = bool(isinstance(old_ep, str) and "redactor_executable" not in (old_eng or {}) and isinstance(old_root, str) and old_root and isinstance(old_dom, str) and old_dom and (Path(old_root) / old_dom).is_dir())
            profile = _profile(config)
            k_root = str(candidate.get("knowledge_root") or old_root or (profile.data_dir / "knowledge").resolve())
            src_root = Path(candidate.get("source_root") or PLUGIN_ROOT).resolve()
            res = _call_binding(profile, plugin_root=adapter_plugin_root(Path(candidate["state_dir"]), src_root), python=candidate["python"], retire_previous=True, root=k_root)
            if res.get("status") != "configured":
                _emit({"status": "deferred" if res.get("status") == "deferred" else "incomplete",
                       "stage": "old_owner" if res.get("reason") == "knowledge_service_busy" else str(res.get("reason") or "binding"), "switched": False}); return 0
            if had_legacy_dir:
                from mindie_knowledge.loop.lifecycle import retire_service
                try:
                    ret = retire_service(Path(old_ep), control_timeout=15.0)
                except TimeoutError:
                    raise
                except (OSError, ValueError):
                    _emit({"status": "incomplete", "stage": "old_retirement", "switched": False}); return 0
                if ret.get("idle") is not True or ret.get("cleanup_failed"):
                    _emit({"status": "deferred" if ret.get("idle") is False else "incomplete",
                           "stage": "old_owner" if ret.get("idle") is False else "old_retirement", "switched": False}); return 0
            fields = dict(res["binding_fields"])
            published = {**candidate, **fields, "engine_config": str(fields["engine_config"]), "domain_configs": dict(fields["domain_configs"]), "community_config": candidate["community_config"]}
            atomic_write_json(Path(published["adapter_config"]), published)
            atomic_write_json(config, published)
            _emit({"status": "activated", "switched": True, "seconds": round(time.monotonic() - started, 4)})
            return 0
        finally:
            upd_lock.release()
    except LockTimeoutError:
        _emit({"status": "deferred", "stage": "active_operation", "switched": False}); return 0
    finally:
        op_lock.release()

def sharing(args) -> int:
    from mindie_harness.sharing import configure_sharing, sharing_status
    if args.status or not (args.enable or args.local_only or args.disable):
        _emit(sharing_status(_profile(), cwd=Path.cwd()))
    else:
        choice = "contribute" if args.enable else ("local-only" if args.local_only else "disabled")
        _emit(configure_sharing(_profile(), choice=choice, project_roots=args.project_root, repository=args.repository, fork=args.fork, account=args.account))
    return 0

def doctor(args) -> int:
    from mindie_harness.api import run_host
    argv = ["--host", "cc", "--config", str(_profile().config_path), "doctor"]
    return run_host(argv + (["--probe-remote"] if args.probe_remote else []) + (["--cwd", args.cwd] if args.cwd else []) + (["--timeout", str(args.timeout)] if args.timeout is not None else []))

def update(args) -> int:
    from mindie_harness.runtime.wheelhouse import update_runtime
    _emit(update_runtime(_profile(), force=args.force, offline=args.offline, channel_base_url=args.channel_base_url, background=args.background)); return 0

def _has_symbol(path: Path, symbol: str) -> bool:
    nodes = ast.parse(path.read_text(encoding="utf-8")).body
    for segment in symbol.replace("::", ".").split("."):
        if (selected := next((n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == segment), None)) is None:
            return False
        nodes = selected.body
    return True

def check_registry(source: Path, tests: Path, compatibility: dict) -> None:
    """Check every registered production symbol, test, and local evidence file."""
    for interface in compatibility["used_interfaces"]:
        row = compatibility["interface_registry"][interface]
        for ref in row["code_paths"]:
            fn, sym = ref.split(":", 1)
            if not (path := (source / fn).resolve()).is_relative_to(source) or not _has_symbol(path, sym):
                raise ValueError("registered Harness code boundary is missing: " + interface)
        for ref in row["test_ids"]:
            fn, sym = ref.split("::", 1)
            if not (path := (tests / Path(fn).relative_to("tests")).resolve()).is_relative_to(tests) or not _has_symbol(path, sym):
                raise ValueError("registered Harness contract test is missing: " + interface)
        for ref in row["evidence_refs"]:
            if not ref.startswith(("https://", "http://")) and (not (path := (source / ref.split("#", 1)[0]).resolve()).is_relative_to(source) or not path.is_file()):
                raise ValueError("registered Harness evidence is missing: " + interface)

def release_metadata(source: Path, revision: str, tests: Path | None = None) -> dict:
    from mindie_knowledge.releases import validate_compatibility, validate_release_metadata
    source = Path(source).resolve()
    compatibility = validate_compatibility(json.loads((source / DECLARATION).read_text(encoding="utf-8")), harness_name=HARNESS)
    if tests is not None:
        check_registry(source, Path(tests).resolve(), compatibility)
    version = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    return validate_release_metadata(dict(schema="mindie-plugin-release/1", version=version, source=dict(repository=REPOSITORY, sha=revision), compatibility=compatibility), repository=REPOSITORY, harness_name=HARNESS)

def _install(args) -> int:
    result = initialize_profile(root=getattr(args, "root", None), domain=getattr(args, "domain", DEFAULT_DOMAIN))
    choice = "contribute" if args.community_enable else ("local-only" if (args.community_local_only or args.local_only) else ("disabled" if args.community_disable else None))
    if choice is not None:
        from mindie_harness.sharing import configure_sharing
        result["sharing"] = configure_sharing(_profile(), choice=choice, project_roots=args.community_project_root)
    _emit(result); return 0

def _release(args) -> int:
    _emit(release_metadata(args.source, args.revision, args.tests)); return 0

def _add_flags(parser, spec: list[tuple[str, dict]]):
    for flag, options in spec:
        parser.add_argument(flag, **options)
    return parser

def _run_legacy(raw_args: list[str]) -> int:
    lp = _add_flags(argparse.ArgumentParser(prog="setup.py"), [
        ("--source", {"type": Path}), ("--activate", {"type": Path}), ("--config", {"type": Path}), ("--root", {"type": Path}),
        ("--domain", {"default": DEFAULT_DOMAIN}), ("--no-public-feed", {"action": "store_true"}), ("--claude-config-dir", {"type": Path}),
        ("--community-repository", {}), ("--community-project-root", {"action": "append"}), ("--community-branch", {}),
        ("--community-account", {}), ("--community-fork", {}), ("--community-visibility", {"choices": ["public"]})])
    args = lp.parse_args(raw_args)
    if args.activate and args.source:
        lp.error("--activate does not accept --source")
    if not args.activate and not args.source:
        lp.error("--source is required for preparation")
    return _legacy_activate(args.activate) if args.activate else _legacy_prepare(args)

def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if importlib.util.find_spec("mindie_harness") is None:
        import mindie_launch
        local_py = PLUGIN_ROOT / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
        py = mindie_launch.active_python(mindie_launch.config_path()) or (local_py if local_py.is_file() else None)
        if py is not None and py.resolve() != Path(sys.executable).resolve():
            return subprocess.run([str(py), "-I", "-X", "utf8", str(Path(__file__).resolve()), *raw_args], creationflags=_NO_WIN).returncode
    if raw_args and raw_args[0] not in SUBCOMMANDS and raw_args[0] not in {"-h", "--help"}:
        return _run_legacy(raw_args)
    parser = argparse.ArgumentParser(prog="setup.py")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "init"):
        p = _add_flags(sub.add_parser(name), [("--root", {}), ("--domain", {"default": DEFAULT_DOMAIN}), ("--community-project-root", {"action": "append", "default": []})])
        _add_flags(p.add_mutually_exclusive_group(), [("--local-only", {"action": "store_true"}), ("--community-enable", {"action": "store_true"}),
                                                      ("--community-local-only", {"action": "store_true"}), ("--community-disable", {"action": "store_true"})])
        p.set_defaults(handler=_install)
    p = _add_flags(sub.add_parser("sharing"), [("--project-root", {"action": "append", "default": []}), ("--repository", {}), ("--fork", {}), ("--account", {})])
    _add_flags(p.add_mutually_exclusive_group(), [("--status", {"action": "store_true"}), ("--enable", {"action": "store_true"}), ("--local-only", {"action": "store_true"}), ("--disable", {"action": "store_true"})])
    p.set_defaults(handler=sharing)
    _add_flags(sub.add_parser("doctor"), [("--probe-remote", {"action": "store_true"}), ("--cwd", {}), ("--timeout", {"type": float})]).set_defaults(handler=doctor)
    _add_flags(sub.add_parser("update"), [("--force", {"action": "store_true"}), ("--offline", {"action": "store_true"}), ("--channel-base-url", {}), ("--background", {"action": "store_true"})]).set_defaults(handler=update)
    _add_flags(sub.add_parser("release-metadata"), [("--source", {"type": Path, "required": True}), ("--tests", {"type": Path}), ("--revision", {"required": True})]).set_defaults(handler=_release)
    return int((args := parser.parse_args(raw_args)).handler(args))

if __name__ == "__main__":
    raise SystemExit(main())
