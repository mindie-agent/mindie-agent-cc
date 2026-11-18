#!/usr/bin/env python3
"""Thin Claude Code setup CLI delegating shared behavior to `mindie_harness`.

[Role] Initializes `cc.json`/`cc.engine.json`, handles legacy `--source`/`--activate` upgrade handoffs from pre-0.1.30 updaters, configures sharing, runs doctor and runtime update, and emits validated `mindie-plugin-release/1` metadata.
[Invariants] `release-metadata` validates compatibility, AST `code_paths`, `test_ids`, and local evidence; `--activate` enforces `<state_dir>/update/operation.lock` plus `component_source_commands` CAS before publishing `cc.json`.
[Timeouts & Locks] Legacy `--activate` polls `operation.lock` up to `15s` and `.update.lock` up to `5s`; doctor/update delegates own their bounded deadlines.
[Side-Effect Policy] Profile and staged candidate writes stay inside MindIE-owned documents.
"""
import argparse
from contextlib import contextmanager
import ast
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tomllib

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
PLUGIN_ROOT = HERE.parent
_NO_WIN = 0x08000000 if os.name == "nt" else 0
REPOSITORY, HARNESS, DECLARATION, DEFAULT_DOMAIN = "mindie-agent/mindie-agent-cc", "claude-code", "harness-compatibility.json", "vllm-ascend"
SUBCOMMANDS = frozenset({"install", "init", "sharing", "doctor", "update", "release-metadata"})

def _read_json(path: Path) -> dict | None:
    try:
        val = json.loads(Path(path).read_bytes().decode("utf-8-sig")) if Path(path).is_file() else None
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return None
    return val if isinstance(val, dict) else None

def _write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

def _profile(config: str | Path | None = None):
    from mindie_harness.paths import ProfileRef

    return ProfileRef.resolve("cc", plugin_root=PLUGIN_ROOT, config_path=str(config) if config else os.environ.get("MINDIE_CC_CONFIG"))

def _emit(document: dict) -> None:
    sys.stdout.write(json.dumps(document, sort_keys=True) + "\n")
    sys.stdout.flush()

@contextmanager
def _legacy_operation_lock(state_dir: str, timeout: float = 15.0):
    lock_file = Path(state_dir) / "update" / "operation.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout
    locked = False
    try:
        while not locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except TimeoutError:
                raise
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        yield locked
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except TimeoutError:
                raise
            except OSError:
                pass
        os.close(fd)

def initialize_profile(root: str | None = None, domain: str = DEFAULT_DOMAIN) -> dict:
    """Create the `cc` profile documents and return the resolved profile facts."""
    from mindie_harness.locks import FileLock

    profile = _profile()
    lock = FileLock(profile.config_path.with_name(f".{profile.config_path.name}.update.lock"), exclusive=True, timeout=5.0)
    lock.acquire()
    try:
        engine_path = profile.config_path.with_name("cc.engine.json")
        community = str(profile.config_path.with_name("mindie-community.json"))
        prev_engine = _read_json(engine_path) or {}
        knowledge_root = str(Path(root).expanduser().resolve()) if root else str(prev_engine.get("root") or (profile.data_dir / "knowledge").resolve())
        _write_json(engine_path, {**prev_engine, "root": knowledge_root, "domain": domain,
                                  "admission_path": str(profile.admission_db), "transcript_adapter": str(HERE / "transcript.py"),
                                  "capture_mode": "public-transcript", "community_config": community})
        cfg = _read_json(profile.config_path) or {}
        python = sys.executable if importlib.util.find_spec("mindie_harness") is not None else (cfg.get("python") if isinstance(cfg.get("python"), str) and Path(cfg["python"]).is_file() else sys.executable)
        cfg.update(schema="mindie-cc-installation/1", facade_protocol=1, host="cc", python=python,
                   runtime_scripts=str(HERE), base_config=str(profile.config_path),
                   adapter_config=str(profile.config_path), engine_config=str(engine_path),
                   state_dir=str(profile.state_dir), community_config=community, source_root=str(PLUGIN_ROOT))
        _write_json(profile.config_path, cfg)
    finally:
        lock.release()
    return dict(status="configured", config_path=str(profile.config_path), engine_config=str(engine_path),
                root=knowledge_root, domain=domain, admission_path=str(profile.admission_db), reportable=False)

def _legacy_prepare(args) -> int:
    profile = _profile(args.config)
    config = profile.config_path
    os.environ["MINDIE_CC_CONFIG"] = str(config)
    selected = _read_json(config)
    state_dir = selected["state_dir"] if isinstance(selected, dict) and isinstance(selected.get("state_dir"), str) and os.path.isabs(selected["state_dir"]) else str(profile.state_dir)
    community = selected["community_config"] if isinstance(selected, dict) and isinstance(selected.get("community_config"), str) and os.path.isabs(selected["community_config"]) else str(config.with_name("mindie-community.json"))
    raw_ep = (selected or {}).get("engine_config")
    prev_engine = (_read_json(Path(raw_ep)) if isinstance(raw_ep, str) and os.path.isabs(raw_ep) else None) or _read_json(config.with_name("cc.engine.json")) or {}
    knowledge_root = str(Path(args.root).expanduser().resolve()) if args.root else str(prev_engine.get("root") or (profile.data_dir / "knowledge").resolve())
    commands = dict((selected or {}).get("component_commands") or {})
    rebound = commands
    if commands and isinstance(selected, dict):
        from mindie_knowledge.component_bindings import managed_commands, rebind_commands, validate_command_floor
        versions = {name: {"version": importlib.metadata.version(name)} for name, _py, _mod in managed_commands(selected).values()}
        validate_command_floor(selected, versions)
        rebound = rebind_commands(selected, sys.executable, versions)
    key = hashlib.sha256(f"{sys.executable}:{HERE}:{json.dumps(commands, sort_keys=True)}:{json.dumps((selected or {}).get('domain_configs') or {}, sort_keys=True)}".encode()).hexdigest()[:16]
    binding = Path(state_dir) / "installations" / key
    adapter_file, engine_file = binding / "cc.adapter.json", binding / "cc.engine.json"
    engine = {**prev_engine, "root": knowledge_root, "domain": prev_engine.get("domain") or args.domain or DEFAULT_DOMAIN,
              "admission_path": str(prev_engine.get("admission_path") or profile.admission_db),
              "transcript_adapter": str(HERE / "transcript.py"), "capture_mode": "public-transcript", "community_config": community}
    candidate = {**(selected or {}), "schema": "mindie-cc-installation/1", "facade_protocol": 1, "host": "cc",
                 "python": sys.executable, "runtime_scripts": str(HERE), "base_config": str(config),
                 "adapter_config": str(adapter_file), "engine_config": str(engine_file), "state_dir": state_dir,
                 "community_config": community, "source_root": str(Path(args.source).resolve()),
                 "component_source_commands": commands, "domain_source_configs": dict((selected or {}).get("domain_configs") or {})}
    if rebound:
        candidate["component_commands"] = rebound
    if args.claude_config_dir:
        candidate["claude_config_dir"] = str(Path(args.claude_config_dir).expanduser().resolve())
    _write_json(engine_file, engine)
    _write_json(adapter_file, candidate)
    if selected is None:
        _write_json(config.with_name("cc.engine.json"), engine)
        _write_json(config, candidate)
        status = "ready"
    else:
        status = "ready" if selected == candidate else "prepared"
    _emit({"base_config": str(config), "config": str(adapter_file), "status": status})
    return 0

def _legacy_activate(candidate_path: Path) -> int:
    from mindie_harness.locks import FileLock, LockTimeoutError

    started = time.monotonic()
    candidate = _read_json(candidate_path.expanduser().resolve())
    req = ("base_config", "state_dir", "community_config", "python", "runtime_scripts", "engine_config", "adapter_config")
    if not candidate or candidate.get("schema") != "mindie-cc-installation/1" or candidate.get("facade_protocol") != 1 or any(not isinstance(candidate.get(k), str) or not os.path.isabs(candidate[k]) for k in req):
        raise ValueError("invalid candidate installation binding")
    config = Path(candidate["base_config"])
    with _legacy_operation_lock(candidate["state_dir"], timeout=15.0) as op_locked:
        if not op_locked:
            _emit({"status": "deferred", "stage": "active_operation", "switched": False})
            return 0
        lock = FileLock(config.with_name(f".{config.name}.update.lock"), exclusive=True, timeout=5.0)
        try:
            lock.acquire()
        except LockTimeoutError:
            _emit({"status": "deferred", "stage": "active_operation", "switched": False})
            return 0
        try:
            previous = _read_json(config) or {}
            if previous == candidate:
                _emit({"status": "current", "switched": False})
                return 0
            if "domain_source_configs" in candidate and (previous.get("domain_configs") or {}) != candidate["domain_source_configs"]:
                _emit({"status": "deferred", "stage": "domain_routes_changed", "switched": False})
                return 0
            if "component_source_commands" in candidate and (previous.get("component_commands") or {}) != candidate["component_source_commands"]:
                _emit({"status": "deferred", "stage": "component_commands_changed", "switched": False})
                return 0
            for key in ("base_config", "state_dir", "community_config"):
                if key in previous and previous[key] != candidate[key]:
                    raise ValueError("candidate changes installation data identity")
            old_ep = previous.get("engine_config")
            if isinstance(old_ep, str) and Path(old_ep).is_file():
                old_eng = _read_json(Path(old_ep)) or {}
                old_root, old_dom = old_eng.get("root"), old_eng.get("domain")
                if isinstance(old_root, str) and old_root and isinstance(old_dom, str) and old_dom and (Path(old_root) / old_dom).is_dir():
                    from mindie_knowledge.loop.lifecycle import retire_service
                    try:
                        retirement = retire_service(Path(old_ep), control_timeout=15.0)
                    except TimeoutError:
                        raise
                    except (OSError, ValueError):
                        _emit({"status": "incomplete", "stage": "old_retirement", "switched": False})
                        return 0
                    if retirement.get("idle") is not True:
                        _emit({"status": "deferred", "stage": "old_owner", "switched": False})
                        return 0
                    if retirement.get("cleanup_failed"):
                        _emit({"status": "incomplete", "stage": "old_retirement", "switched": False})
                        return 0
            if eng := _read_json(Path(candidate["engine_config"])):
                _write_json(config.with_name("cc.engine.json"), eng)
            _write_json(config, candidate)
            _emit({"status": "activated", "switched": True, "seconds": round(time.monotonic() - started, 4)})
            return 0
        finally:
            lock.release()

def sharing(args) -> int:
    from mindie_harness.sharing import configure_sharing, sharing_status

    if args.status or not (args.enable or args.local_only or args.disable):
        _emit(sharing_status(_profile(), cwd=Path.cwd()))
    else:
        choice = "contribute" if args.enable else ("local-only" if args.local_only else "disabled")
        _emit(configure_sharing(_profile(), choice=choice, project_roots=args.project_root,
                                repository=args.repository, fork=args.fork, account=args.account))
    return 0

def doctor(args) -> int:
    from mindie_harness.api import run_host

    argv = ["--host", "cc", "--config", str(_profile().config_path), "doctor"]
    argv += ["--probe-remote"] if args.probe_remote else []
    argv += ["--cwd", args.cwd] if args.cwd else []
    argv += ["--timeout", str(args.timeout)] if args.timeout is not None else []
    return run_host(argv)

def update(args) -> int:
    from mindie_harness.runtime.wheelhouse import update_runtime

    _emit(update_runtime(_profile(), force=args.force, offline=args.offline,
                         channel_base_url=args.channel_base_url, background=args.background))
    return 0

def _has_symbol(path: Path, symbol: str) -> bool:
    nodes = ast.parse(path.read_text(encoding="utf-8")).body
    for segment in symbol.replace("::", ".").split("."):
        selected = next((node for node in nodes if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == segment), None)
        if selected is None:
            return False
        nodes = selected.body
    return True

def check_registry(source: Path, tests: Path, compatibility: dict) -> None:
    """Check every registered production symbol, test, and local evidence file."""
    for interface in compatibility["used_interfaces"]:
        row = compatibility["interface_registry"][interface]
        for reference in row["code_paths"]:
            filename, symbol = reference.split(":", 1)
            path = (source / filename).resolve()
            if not path.is_relative_to(source) or not _has_symbol(path, symbol):
                raise ValueError("registered Harness code boundary is missing: " + interface)
        for reference in row["test_ids"]:
            filename, symbol = reference.split("::", 1)
            path = (tests / Path(filename).relative_to("tests")).resolve()
            if not path.is_relative_to(tests) or not _has_symbol(path, symbol):
                raise ValueError("registered Harness contract test is missing: " + interface)
        for reference in row["evidence_refs"]:
            if not reference.startswith(("https://", "http://")):
                path = (source / reference.split("#", 1)[0]).resolve()
                if not path.is_relative_to(source) or not path.is_file():
                    raise ValueError("registered Harness evidence is missing: " + interface)

def release_metadata(source: Path, revision: str, tests: Path | None = None) -> dict:
    from mindie_knowledge.releases import validate_compatibility, validate_release_metadata

    source = Path(source).resolve()
    compatibility = validate_compatibility(
        json.loads((source / DECLARATION).read_text(encoding="utf-8")), harness_name=HARNESS)
    if tests is not None:
        check_registry(source, Path(tests).resolve(), compatibility)
    version = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    value = dict(schema="mindie-plugin-release/1", version=version,
                 source=dict(repository=REPOSITORY, sha=revision), compatibility=compatibility)
    return validate_release_metadata(value, repository=REPOSITORY, harness_name=HARNESS)

def _install(args) -> int:
    result = initialize_profile(root=getattr(args, "root", None), domain=getattr(args, "domain", DEFAULT_DOMAIN))
    choice = ("contribute" if args.community_enable else "local-only" if (args.community_local_only or args.local_only)
              else "disabled" if args.community_disable else None)
    if choice is not None:
        from mindie_harness.sharing import configure_sharing
        result["sharing"] = configure_sharing(_profile(), choice=choice, project_roots=args.community_project_root)
    _emit(result)
    return 0

def _release(args) -> int:
    _emit(release_metadata(args.source, args.revision, args.tests))
    return 0

def _add_flags(parser, spec: list[tuple[str, dict]]) -> None:
    for flag, options in spec:
        parser.add_argument(flag, **options)

def _run_legacy(raw_args: list[str]) -> int:
    lp = argparse.ArgumentParser(prog="setup.py")
    _add_flags(lp, [("--source", {"type": Path}), ("--activate", {"type": Path}), ("--config", {"type": Path}),
                    ("--root", {"type": Path}), ("--domain", {"default": DEFAULT_DOMAIN}),
                    ("--no-public-feed", {"action": "store_true"}), ("--claude-config-dir", {"type": Path}),
                    ("--community-repository", {}), ("--community-project-root", {"action": "append"}),
                    ("--community-branch", {}), ("--community-account", {}), ("--community-fork", {}),
                    ("--community-visibility", {"choices": ["public"]})])
    args = lp.parse_args(raw_args)
    if args.activate:
        if args.source:
            lp.error("--activate does not accept --source")
        return _legacy_activate(args.activate)
    if not args.source:
        lp.error("--source is required for preparation")
    return _legacy_prepare(args)

def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if importlib.util.find_spec("mindie_harness") is None:
        import mindie_launch
        py = mindie_launch.active_python(mindie_launch.config_path())
        if py is not None and py.resolve() != Path(sys.executable).resolve():
            return subprocess.run([str(py), "-I", "-X", "utf8", str(Path(__file__).resolve()), *raw_args], creationflags=_NO_WIN).returncode
    if raw_args and raw_args[0] not in SUBCOMMANDS and raw_args[0] not in {"-h", "--help"}:
        return _run_legacy(raw_args)
    parser = argparse.ArgumentParser(prog="setup.py")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "init"):
        p = sub.add_parser(name)
        _add_flags(p, [("--root", {}), ("--domain", {"default": DEFAULT_DOMAIN}),
                       ("--community-project-root", {"action": "append", "default": []})])
        g = p.add_mutually_exclusive_group()
        _add_flags(g, [("--local-only", {"action": "store_true"}), ("--community-enable", {"action": "store_true"}),
                       ("--community-local-only", {"action": "store_true"}), ("--community-disable", {"action": "store_true"})])
        p.set_defaults(handler=_install)
    p = sub.add_parser("sharing")
    g = p.add_mutually_exclusive_group()
    _add_flags(g, [("--status", {"action": "store_true"}), ("--enable", {"action": "store_true"}),
                   ("--local-only", {"action": "store_true"}), ("--disable", {"action": "store_true"})])
    _add_flags(p, [("--project-root", {"action": "append", "default": []}), ("--repository", {}),
                   ("--fork", {}), ("--account", {})])
    p.set_defaults(handler=sharing)
    p = sub.add_parser("doctor")
    _add_flags(p, [("--probe-remote", {"action": "store_true"}), ("--cwd", {}), ("--timeout", {"type": float})])
    p.set_defaults(handler=doctor)
    p = sub.add_parser("update")
    _add_flags(p, [("--force", {"action": "store_true"}), ("--offline", {"action": "store_true"}),
                   ("--channel-base-url", {}), ("--background", {"action": "store_true"})])
    p.set_defaults(handler=update)
    p = sub.add_parser("release-metadata")
    _add_flags(p, [("--source", {"type": Path, "required": True}), ("--tests", {"type": Path}),
                   ("--revision", {"required": True})])
    p.set_defaults(handler=_release)
    args = parser.parse_args(raw_args)
    return int(args.handler(args))

if __name__ == "__main__":
    raise SystemExit(main())
