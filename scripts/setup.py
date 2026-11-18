#!/usr/bin/env python3
"""Thin Claude Code setup CLI delegating shared behavior to `mindie_harness`.

[Role] Initializes `cc.json`/`cc.engine.json`, configures sharing, runs doctor and runtime update, and emits validated `mindie-plugin-release/1` metadata.
[Invariants] `release-metadata` validates compatibility, AST `code_paths`, `test_ids`, and local evidence; version selection reads `pyproject.toml`; sharing delegates verbatim.
[Timeouts & Locks] Doctor/update delegates own their bounded deadlines and locks.
[Side-Effect Policy] Profile writes stay inside MindIE-owned documents.
"""
import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
PLUGIN_ROOT = HERE.parent
_NO_WIN = 0x08000000 if os.name == "nt" else 0
REPOSITORY = "mindie-agent/mindie-agent-cc"
HARNESS = "claude-code"
DECLARATION = "harness-compatibility.json"
DEFAULT_DOMAIN = "vllm-ascend"

def _write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

def _profile():
    from mindie_harness.paths import ProfileRef

    return ProfileRef.resolve("cc", plugin_root=PLUGIN_ROOT, config_path=os.environ.get("MINDIE_CC_CONFIG"))

def _emit(document: dict) -> None:
    sys.stdout.write(json.dumps(document, sort_keys=True) + "\n")
    sys.stdout.flush()

def initialize_profile(root: str | None = None, domain: str = DEFAULT_DOMAIN) -> dict:
    """Create the `cc` profile documents and return the resolved profile facts."""
    from mindie_harness.locks import FileLock

    profile = _profile()
    lock = FileLock(profile.config_path.with_name(f".{profile.config_path.name}.update.lock"), exclusive=True, timeout=5.0)
    lock.acquire()
    try:
        engine_path = profile.config_path.with_name("cc.engine.json")
        community = str(profile.config_path.with_name("mindie-community.json"))
        try:
            prev_engine = json.loads(engine_path.read_bytes().decode("utf-8-sig")) if engine_path.is_file() else {}
        except TimeoutError:
            raise
        except (OSError, ValueError):
            prev_engine = {}
        knowledge_root = str(Path(root).expanduser().resolve()) if root else str(prev_engine.get("root") or (profile.data_dir / "knowledge").resolve())
        _write_json(engine_path, {**prev_engine, "root": knowledge_root, "domain": domain,
                                  "admission_path": str(profile.admission_db), "transcript_adapter": str(HERE / "transcript.py"),
                                  "capture_mode": "public-transcript", "community_config": community})
        try:
            cfg = json.loads(profile.config_path.read_bytes().decode("utf-8-sig")) if profile.config_path.is_file() else {}
        except TimeoutError:
            raise
        except (OSError, ValueError):
            cfg = {}
        python = sys.executable if importlib.util.find_spec("mindie_harness") is not None else (cfg.get("python") if isinstance(cfg.get("python"), str) and Path(cfg["python"]).is_file() else sys.executable)
        cfg.update(dict(schema="mindie-cc-installation/1", facade_protocol=1, host="cc", python=python,
                        runtime_scripts=str(HERE), base_config=str(profile.config_path),
                        adapter_config=str(profile.config_path), engine_config=str(engine_path),
                        state_dir=str(profile.state_dir), community_config=community, source_root=str(PLUGIN_ROOT)))
        _write_json(profile.config_path, cfg)
    finally:
        lock.release()
    return dict(status="configured", config_path=str(profile.config_path), engine_config=str(engine_path),
                root=knowledge_root, domain=domain, admission_path=str(profile.admission_db), reportable=False)

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
            if reference.startswith(("https://", "http://")):
                continue
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

def _add_flags(parser: argparse.ArgumentParser, spec: list[tuple[str, dict]]) -> None:
    for flag, options in spec:
        parser.add_argument(flag, **options)

def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
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
    if importlib.util.find_spec("mindie_harness") is None:
        import mindie_launch
        py = mindie_launch.active_python(mindie_launch.config_path())
        if py is not None and py.resolve() != Path(sys.executable).resolve():
            return subprocess.run([str(py), "-I", "-X", "utf8", str(Path(__file__).resolve()), *raw_args], creationflags=_NO_WIN).returncode
    return int(args.handler(args))

if __name__ == "__main__":
    raise SystemExit(main())
