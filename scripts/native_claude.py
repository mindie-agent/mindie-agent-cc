"""Native Claude plugin marketplace add/install/list. Structured JSON only.

Does not edit private registries. Isolated acceptance uses CLAUDE_CONFIG_DIR.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from bounded import run
from paths import CONFIG_ENV, MARKETPLACE, PLUGIN_ID, PLUGIN_QUALIFIED, claude_config_dir

COMMANDS = (
    "init",
    "status",
    "deactivate",
    "sharing-enable",
    "sharing-disable",
    "recover",
    "reporting-status",
    "reporting-enable",
    "reporting-disable",
)


def claude_bin():
    explicit = os.environ.get("MINDIE_CC_BIN")
    if explicit:
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    raise RuntimeError("claude binary is not on PATH")


def native_env(config=None):
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    directory = None
    if isinstance(config, dict):
        value = config.get("claude_config_dir")
        if isinstance(value, str) and os.path.isabs(value):
            directory = value
    else:
        directory = claude_config_dir(config)
    if directory:
        env["CLAUDE_CONFIG_DIR"] = directory
    return env


def _parse_json(text, what):
    text = (text or "").strip()
    if not text:
        raise RuntimeError(f"native {what} returned no JSON")
    try:
        return json.loads(text)
    except ValueError:
        start = text.find("[")
        start_obj = text.find("{")
        if start < 0 or (0 <= start_obj < start):
            start = start_obj
        end_list = text.rfind("]")
        end_obj = text.rfind("}")
        end = max(end_list, end_obj)
        if start < 0 or end <= start:
            raise RuntimeError(f"native {what} was not JSON")
        return json.loads(text[start : end + 1])


def plugin_list(config=None, *, timeout=30):
    output = run(
        [claude_bin(), "plugin", "list", "--json"],
        "",
        timeout=timeout,
        env=native_env(config),
    )
    data = _parse_json(output, "plugin list")
    if not isinstance(data, list):
        raise RuntimeError("native plugin list is not a JSON array")
    return data


def mcp_list(config=None, *, timeout=20):
    return run(
        [claude_bin(), "mcp", "list"],
        "",
        timeout=timeout,
        env=native_env(config),
        check=False,
    )


def marketplace_list(config=None, *, timeout=30):
    output = run(
        [claude_bin(), "plugin", "marketplace", "list", "--json"],
        "",
        timeout=timeout,
        env=native_env(config),
    )
    data = _parse_json(output, "marketplace list")
    if not isinstance(data, list):
        raise RuntimeError("native marketplace list is not a JSON array")
    return data


def marketplace_add(package: Path, config=None, *, timeout=60):
    package = Path(package).resolve()
    run(
        [claude_bin(), "plugin", "marketplace", "add", str(package)],
        "",
        timeout=timeout,
        env=native_env(config),
    )


def plugin_install(config=None, *, timeout=90):
    output = run(
        [
            claude_bin(),
            "plugin",
            "install",
            PLUGIN_QUALIFIED,
            "--json",
            "-y",
            "--scope",
            "user",
        ],
        "",
        timeout=timeout,
        env=native_env(config),
    )
    try:
        return _parse_json(output, "plugin install")
    except RuntimeError:
        return dict(raw=output[:500], outcome="unknown")


def plugin_update(config=None, *, timeout=90):
    output = run(
        [
            claude_bin(),
            "plugin",
            "update",
            PLUGIN_QUALIFIED,
            "--json",
            "-y",
            "--scope",
            "user",
        ],
        "",
        timeout=timeout,
        env=native_env(config),
    )
    try:
        return _parse_json(output, "plugin update")
    except RuntimeError:
        return dict(raw=output[:500], outcome="unknown")


def installed_record(listing, *, plugin_id=PLUGIN_QUALIFIED):
    if not isinstance(listing, list):
        raise RuntimeError("native plugin list is not a JSON array")
    matches = [
        row for row in listing
        if isinstance(row, dict) and row.get("id") == plugin_id
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"native plugin list holds {len(matches)} records for {plugin_id}"
        )
    return matches[0]


def marketplace_record(listing, *, name=MARKETPLACE):
    if not isinstance(listing, list):
        raise RuntimeError("native marketplace list is not a JSON array")
    matches = [
        row for row in listing
        if isinstance(row, dict) and row.get("name") == name
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"native marketplace list holds {len(matches)} records for {name}"
        )
    return matches[0]


def verify_readback(config, package: Path, version: str, *, listing=None, markets=None,
                    launcher=None, config_file=None, timeout=30):
    package = Path(package).resolve()
    listing = plugin_list(config, timeout=timeout) if listing is None else listing
    record = installed_record(listing)
    problems = []
    if record.get("version") != version:
        problems.append(f"version={record.get('version')!r}")
    if record.get("enabled") is not True:
        problems.append(f"enabled={record.get('enabled')!r}")
    errors = record.get("errors")
    if errors:
        problems.append(f"errors={errors!r}")
    install_path = record.get("installPath")
    if not isinstance(install_path, str) or not Path(install_path).is_dir():
        problems.append(f"installPath={install_path!r}")
    else:
        manifest = Path(install_path) / ".claude-plugin" / "plugin.json"
        if not manifest.is_file():
            problems.append("installed plugin.json missing")
        else:
            installed = json.loads(manifest.read_text())
            if installed.get("version") != version or installed.get("name") != PLUGIN_ID:
                problems.append(
                    f"installed manifest name={installed.get('name')!r} "
                    f"version={installed.get('version')!r}"
                )
        if launcher:
            hooks = Path(install_path) / "hooks" / "hooks.json"
            mcp = Path(install_path) / ".mcp.json"
            try:
                stop = json.loads(hooks.read_text())["hooks"]["Stop"][0]["hooks"][0]["command"]
                servers = json.loads(mcp.read_text())["mcpServers"]
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                problems.append("installed hooks or .mcp.json missing")
            else:
                if str(launcher) not in stop:
                    problems.append(f"installed Stop hook launcher mismatch: {stop!r}")
                if config_file and config_file not in stop:
                    problems.append("installed Stop hook lacks adapter --config")
                knowledge = servers.get("knowledge") or {}
                args = knowledge.get("args") or []
                if str(launcher) not in args:
                    problems.append(f"installed knowledge MCP launcher mismatch: {args!r}")
                if config_file and config_file not in args and config_file not in (
                    (knowledge.get("env") or {}).values()
                ):
                    problems.append("installed knowledge MCP lacks adapter config")
                if "remote" not in servers:
                    problems.append("installed plugin lacks remote MCP surface")
    markets = marketplace_list(config, timeout=timeout) if markets is None else markets
    market = marketplace_record(markets)
    source_path = market.get("path") or market.get("installLocation")
    if market.get("source") != "directory":
        problems.append(f"marketplace source={market.get('source')!r}")
    if not isinstance(source_path, str) or Path(source_path).resolve() != package:
        problems.append(f"marketplace path={source_path!r} package={str(package)!r}")
    try:
        mcp_text = mcp_list(config, timeout=timeout)
        surfaces = set(re.findall(r"plugin:mindie-agent:([A-Za-z0-9_-]+)", mcp_text))
        if "knowledge" not in surfaces or "remote" not in surfaces:
            problems.append(f"mcp list surfaces={sorted(surfaces)!r} text={mcp_text[:300]!r}")
    except Exception as exc:
        problems.append(f"mcp list failed: {type(exc).__name__}: {str(exc)[:200]}")
    if problems:
        raise RuntimeError("native readback mismatch: " + ", ".join(problems))
    return dict(plugin=record, marketplace=market)


def hook_command(python: str, launcher: str, op: str, config_file: str) -> str:
    if os.name == "nt":
        return f'"{python}" "{launcher}" --config "{config_file}" hook {op}'
    return f"'{python}' '{launcher}' --config '{config_file}' hook {op}"


def mcp_args(python: str, launcher: str, surface: str, config_file: str) -> dict:
    return dict(
        command=python,
        args=[launcher, "--config", config_file, "mcp", surface],
        env={CONFIG_ENV: config_file},
    )


def render_hooks(python: str, launcher: str, config_file: str) -> dict:
    expansion = hook_command(python, launcher, "expansion", config_file)
    pretool = hook_command(python, launcher, "pretool", config_file)
    stop = hook_command(python, launcher, "stop", config_file)
    expansion_hooks = []
    for name in COMMANDS:
        expansion_hooks.append(
            {
                "matcher": f"mindie-agent:{name}",
                "hooks": [{"type": "command", "command": expansion, "timeout": 2}],
            }
        )
    return {
        "description": "MindIE Agent Claude Code hooks.",
        "hooks": {
            "UserPromptExpansion": expansion_hooks,
            "PreToolUse": [
                {
                    "matcher": "^mcp__plugin_mindie-agent_(knowledge|remote)__[A-Za-z0-9_.]+$",
                    "hooks": [{"type": "command", "command": pretool, "timeout": 2}],
                }
            ],
            "Stop": [
                {
                    "hooks": [{"type": "command", "command": stop, "timeout": 2}],
                }
            ],
        },
    }


def render_mcp(python: str, launcher: str, config_file: str) -> dict:
    return {
        "mcpServers": {
            "knowledge": mcp_args(python, launcher, "knowledge", config_file),
            "remote": mcp_args(python, launcher, "remote", config_file),
        }
    }


def write_host_package(source: Path, package: Path, *, python: str, launcher: Path, version: str, config_file: str) -> Path:
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)
    manifest = json.loads((source / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((source / ".claude-plugin" / "marketplace.json").read_text())
    manifest["version"] = version
    manifest.pop("hooks", None)
    manifest.pop("mcpServers", None)
    market["plugins"][0]["version"] = version
    (package / ".claude-plugin").mkdir()
    (package / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    (package / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(market, indent=2) + "\n"
    )
    hooks_dir = package / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        json.dumps(render_hooks(python, str(launcher), config_file), indent=2) + "\n"
    )
    (package / ".mcp.json").write_text(
        json.dumps(render_mcp(python, str(launcher), config_file), indent=2) + "\n"
    )
    skills = source / "skills"
    if skills.is_dir():
        shutil.copytree(skills, package / "skills")
    return package


def sync_stable_package(package: Path, stable: Path) -> Path:
    package = Path(package).resolve()
    stable = Path(stable).resolve()
    if package != stable:
        if stable.exists():
            shutil.rmtree(stable)
        shutil.copytree(package, stable)
    return stable


def install_and_verify(package: Path, version: str, config=None, *, timeout=120,
                       deadline=None, stable=None, launcher=None, config_file=None):
    from genstate import native_package_path

    if deadline is None:
        deadline = time.monotonic() + timeout

    def remaining(cap):
        left = deadline - time.monotonic()
        if left <= 0.5:
            raise RuntimeError("native installer deadline exhausted")
        return min(cap, left)

    package = Path(package).resolve()
    if stable is None:
        stable = native_package_path(config if isinstance(config, dict) else None)
    stable = sync_stable_package(package, Path(stable))
    markets = marketplace_list(config, timeout=remaining(20))
    present = any(isinstance(row, dict) and row.get("name") == MARKETPLACE for row in markets)
    if present:
        market = marketplace_record(markets)
        source = Path(market.get("path") or market.get("installLocation") or "").resolve()
        if source != stable:
            raise RuntimeError(
                f"marketplace {MARKETPLACE} is registered at {source}, not {stable}"
            )
    else:
        marketplace_add(stable, config, timeout=remaining(30))
    listing = plugin_list(config, timeout=remaining(20))
    installed = any(
        isinstance(row, dict) and row.get("id") == PLUGIN_QUALIFIED for row in listing
    )
    if installed:
        report = plugin_update(config, timeout=remaining(40))
    else:
        report = plugin_install(config, timeout=remaining(40))
    listing = plugin_list(config, timeout=remaining(20))
    markets = marketplace_list(config, timeout=remaining(20))
    verified = verify_readback(
        config,
        stable,
        version,
        listing=listing,
        markets=markets,
        launcher=launcher,
        config_file=config_file,
        timeout=remaining(20),
    )
    return dict(
        install=report,
        plugin=verified["plugin"],
        marketplace=verified["marketplace"],
        stable_package=str(stable),
    )
