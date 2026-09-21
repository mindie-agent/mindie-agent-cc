#!/usr/bin/env python3
"""Configure MindIE-owned files for the Claude Code adapter. Default sharing OFF.

Does not write ~/.claude except through `claude plugin marketplace add/install`
when native install is requested. Sharing can be configured after install.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from bounded import run
from paths import PLUGIN_ROOT, config_path as default_config_path

PROBE_MODULES = (
    "mindie_knowledge.loop.cli",
    "mindie_knowledge.loop.activation",
    "mindie_knowledge.loop.engine",
    "remote_dev.mcp.tools",
)
_PROBE_TEMPLATE = """
import importlib
missing = []
for name in {modules!r}:
    try:
        importlib.import_module(name)
    except Exception as exc:
        missing.append(f"{{name}} ({{type(exc).__name__}}: {{exc}})")
if not missing:
    from mindie_knowledge.loop.engine import Engine
    from mindie_knowledge.loop import cli
    if not callable(getattr(Engine, "stop_if_idle", None)):
        missing.append("core engine lacks the stop_if_idle API")
    if not callable(getattr(cli, "load_transcript_adapter", None)):
        missing.append("core cli lacks load_transcript_adapter")
if not missing:
    try:
        module = cli.load_transcript_adapter({{"transcript_adapter": {parser!r}}})
        if module is None:
            missing.append("transcript adapter did not load")
    except Exception as exc:
        missing.append(f"transcript adapter load failed ({{type(exc).__name__}}: {{exc}})")
print("MISSING: " + "; ".join(missing) if missing else "OK")
"""


def probe_script(parser: str) -> str:
    return _PROBE_TEMPLATE.format(modules=list(PROBE_MODULES), parser=parser)


PROBE_SCRIPT = probe_script(
    str((PLUGIN_ROOT / "scripts" / "transcript.py").resolve()))

OFFICIAL_REPOSITORIES = {
    "mindie-knowledge": "https://github.com/mindie-agent/knowledge",
    "remote-dev": "https://github.com/mindie-agent/remote-dev",
}


def runtime_pins(requirements=None):
    """One version source, restricted to the two reviewed official Git repos."""
    path = Path(requirements) if requirements is not None else PLUGIN_ROOT / "runtime-requirements.txt"
    pins = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([a-z-]+)\s+@\s+git\+(https://github\.com/[^\s@]+)@([0-9a-f]{40})", line)
        if match is None:
            raise ValueError("runtime-requirements.txt requires official Git URLs and full commit SHAs")
        name, url, commit = match.groups()
        url = url.removesuffix(".git")
        if name in pins or OFFICIAL_REPOSITORIES.get(name) != url:
            raise ValueError("runtime-requirements.txt has a duplicate or non-official dependency")
        pins[name] = dict(url=url, commit=commit)
    if pins.keys() != OFFICIAL_REPOSITORIES.keys():
        raise ValueError("runtime-requirements.txt must contain both reviewed dependencies")
    return pins


_PIN_TEMPLATE = """
import json
from importlib.metadata import distribution
want = {pins!r}
missing = []
for name, pin in want.items():
    try:
        dist = distribution(name)
        direct = json.loads(dist.read_text("direct_url.json") or "{{}}")
        vcs = direct.get("vcs_info") or {{}}
        if (direct.get("url") not in (pin["url"], pin["url"] + ".git")
                or vcs.get("vcs") != "git" or vcs.get("commit_id") != pin["commit"]):
            missing.append(f"{{name}} does not match the required official Git commit")
    except Exception as exc:
        missing.append(f"{{name}} ({{type(exc).__name__}})")
print("MISSING: " + "; ".join(missing) if missing else "OK")
"""


def probe_runtime(python, pins=None):
    pins = runtime_pins() if pins is None else pins
    pin_script = _PIN_TEMPLATE.format(pins=pins)
    try:
        pin_result = run([python, "-c", pin_script], "", timeout=15)
    except Exception as exc:
        raise SystemExit(
            f"dependency pin probe failed in {python}: {type(exc).__name__}: {str(exc)[:200]}"
        )
    if not pin_result.strip().endswith("OK"):
        raise SystemExit(
            f"{python} does not have the exact required commits: {pin_result.strip()}"
        )
    try:
        output = run([python, "-c", PROBE_SCRIPT], "", timeout=15)
    except Exception as exc:
        raise SystemExit(
            f"knowledge runtime probe failed in {python}: {type(exc).__name__}: {str(exc)[:200]}"
        )
    if not output.strip().endswith("OK"):
        raise SystemExit(
            f"{python} is missing pinned dependencies: {output.strip()}. "
            "Install runtime-requirements.txt first."
        )


def write_private(path, value, *, replace=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if replace else os.O_EXCL)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def community_settings(args, parser):
    selected = any(
        getattr(args, key)
        for key in (
            "community_repository",
            "community_project_root",
            "community_account",
            "community_fork",
            "community_visibility",
        )
    )
    if not selected:
        if args.community_branch is not None:
            parser.error("--community-branch requires --community-repository")
        return None
    if not args.community_repository:
        parser.error("community sharing requires --community-repository owner/repo")
    if not args.community_project_root:
        parser.error("community sharing requires at least one --community-project-root")
    if args.community_visibility != "public":
        parser.error("community sharing requires --community-visibility public")
    roots = []
    for root in args.community_project_root:
        canonical = str(Path(root).expanduser().resolve())
        if not Path(canonical).is_dir():
            parser.error("community project root does not exist: " + canonical)
        if canonical not in roots:
            roots.append(canonical)
    data = dict(
        schema="mindie-community-config/1",
        enabled=True,
        generation=secrets.token_hex(16),
        enabled_at=time.time(),
        repository=args.community_repository,
        branch=args.community_branch or "main",
        project_roots=roots,
        idle_seconds=300,
        visibility="public",
    )
    if args.community_account:
        data["account"] = args.community_account
    if args.community_fork:
        data["fork"] = args.community_fork
    return data


def write_community(path, community):
    if community is None:
        write_private(
            path,
            dict(
                schema="mindie-community-config/1",
                enabled=False,
                generation=secrets.token_hex(16),
                enabled_at=None,
                repository=None,
                branch="main",
                project_roots=[],
                idle_seconds=300,
            ),
        )
        return "off"
    write_private(path, community, replace=path.exists())
    return "enabled"


def build_bootstrap_runtime(domain_root: Path) -> str:
    venv = domain_root / "bootstrap-venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if python.exists():
        return str(python)
    env = dict(
        os.environ,
        PIP_RETRIES="0",
        PIP_NO_INPUT="1",
        PIP_DISABLE_PIP_VERSION_CHECK="1",
        GIT_TERMINAL_PROMPT="0",
    )
    run([sys.executable, "-m", "venv", str(venv)], "", timeout=180)
    run(
        [str(python), "-m", "pip", "install", "-r",
         str(PLUGIN_ROOT / "runtime-requirements.txt")],
        "",
        timeout=600,
        max_output=512 * 1024,
        env=env,
    )
    return str(python)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-python", default=None,
                        help="operator override; default builds a pinned venv")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
        / "mindie-agent",
    )
    parser.add_argument("--domain", default="vllm-ascend")
    parser.add_argument("--no-public-feed", action="store_true")
    parser.add_argument("--community-repository", metavar="OWNER/REPO")
    parser.add_argument("--community-project-root", action="append")
    parser.add_argument("--community-branch")
    parser.add_argument("--community-account")
    parser.add_argument("--community-fork")
    parser.add_argument("--community-visibility", choices=["public"])
    parser.add_argument(
        "--claude-config-dir",
        type=Path,
        help="isolated CLAUDE_CONFIG_DIR for native plugin install (acceptance)",
    )
    parser.add_argument("--update-remote", default=None,
                        help="git remote tracked for automatic updates")
    parser.add_argument("--no-schedule", action="store_true",
                        help="do not register the automatic update check")
    parser.add_argument("--no-native-install", action="store_true",
                        help="configure MindIE files only; skip claude plugin install")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Python 3.11+ is required")
    try:
        pins = runtime_pins()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    community = community_settings(args, parser)
    config = args.config.expanduser().absolute()
    domain_root = args.root.expanduser().absolute() / "cc"
    if args.knowledge_python:
        python = str(Path(args.knowledge_python).expanduser())
        if not os.path.isabs(python):
            python = str(Path(python).absolute())
        probe_runtime(python, pins)
    else:
        python = build_bootstrap_runtime(domain_root)
        probe_runtime(python, pins)
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", args.domain):
        parser.error("invalid domain name")
    engine_config = config.with_name(config.stem + ".engine.json")
    community_config = config.with_name(config.stem + ".community.json")
    if config.exists() or engine_config.exists():
        if community is None:
            parser.error(
                "configuration already exists; pass --community-* to configure sharing"
            )
        sharing = write_community(community_config, community)
        print(json.dumps(dict(config=str(config), sharing=sharing, updated="community"), indent=2))
        return
    admission = domain_root / "admission.sqlite3"
    transcript = (PLUGIN_ROOT / "scripts" / "transcript.py").resolve()
    organizer = (PLUGIN_ROOT / "scripts" / "organizer.py").resolve()
    value = dict(
        root=str(domain_root),
        domain=args.domain,
        admission_path=str(admission),
        transcript_adapter=str(transcript),
        agent_command=[python, str(organizer)],
        community_config=str(community_config),
    )
    if args.domain == "vllm-ascend" and not args.no_public_feed:
        value["feeds"] = [
            dict(
                repository="mindie-agent/knowledge-vllm-ascend",
                ref="main",
                domain="vllm-ascend",
                interval_seconds=300,
            )
        ]
    write_private(engine_config, value)
    adapter_value = dict(
        python=python,
        engine_config=str(engine_config),
        community_config=str(community_config),
        state_dir=str(domain_root / "adapter-state"),
        base_config=str(config),
    )
    if args.claude_config_dir:
        adapter_value["claude_config_dir"] = str(args.claude_config_dir.expanduser().absolute())
    if args.update_remote:
        adapter_value["update_remote"] = args.update_remote
    write_private(config, adapter_value)
    sharing = write_community(community_config, community)
    import genstate
    import updater

    os.environ["MINDIE_CC_CONFIG"] = str(config)
    identity = updater.source_identity(PLUGIN_ROOT)
    generation = genstate.generations_dir(adapter_value) / identity
    if not (generation / updater.COMPLETE).is_file():
        updater.snapshot_source(PLUGIN_ROOT, generation)
        gen_adapter = updater.write_generation_configs(
            generation, Path(python), adapter_value
        )
        host_package = updater.build_host_package(
            generation, dict(adapter_value, python=python), identity,
            package_dir=generation / "host-package",
        )
        (generation / updater.COMPLETE).write_text(identity + "\n")
    else:
        gen_adapter = generation / "config" / "cc.adapter.json"
        host_package = generation / "host-package"
    genstate.write_current(
        {
            "generation": str(generation),
            "python": python,
            "adapter_config": str(gen_adapter),
            "sha": identity,
        },
        adapter_value,
    )
    native = "skipped"
    native_error = None
    if not args.no_native_install:
        try:
            import native_claude

            version = json.loads(
                (host_package / ".claude-plugin" / "plugin.json").read_text()
            )["version"]
            native = native_claude.install_and_verify(
                host_package,
                version,
                adapter_value,
                launcher=str(genstate.launch_dir(adapter_value) / identity / "mindie_launch.py"),
                config_file=str(config),
            )
        except Exception as exc:
            native_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            native = f"failed ({native_error})"
    feed = dict(result="skipped")
    if not args.no_public_feed:
        try:
            output = run(
                [python, "-m", "mindie_knowledge.loop.cli", "sync", "--config", str(engine_config)],
                "",
                timeout=60,
            )
            feed = dict(result="ok", detail=output.strip()[:400])
        except Exception as exc:
            feed = dict(result="stale", error=f"{type(exc).__name__}: {str(exc)[:200]}")
    scheduled = "skipped"
    if not args.no_schedule:
        try:
            import contextlib
            import io

            with contextlib.redirect_stdout(io.StringIO()):
                code = updater.install_schedule()
            scheduled = "registered" if code == 0 else "manual"
        except Exception as exc:
            scheduled = f"manual ({type(exc).__name__}: {str(exc)[:120]})"
    report = dict(
        config=str(config),
        engine_config=str(engine_config),
        community_config=str(community_config),
        admission_path=str(admission),
        generation=str(generation),
        sha=identity,
        native_package=str(host_package),
        domain=args.domain,
        sharing=sharing,
        feed_sync=feed,
        update_schedule=scheduled,
        native_install=native,
        plugin_install=(
            "Native install uses the stable marketplace path and "
            "claude plugin install/update mindie-agent@mindie-agent-cc --json -y."
        ),
    )
    print(json.dumps(report, indent=2, default=str))
    if native_error:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
