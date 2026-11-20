"""Thin-plugin release, manifest, seed, and metadata contract tests.

[Role]
Verifies the `0.1.32` manifest set, the `<= 1500` script line ceiling, the
9 runtime component declarations (including `npu-top`), the bundled seed,
the `HostAdapter` protocol, and `setup.py release-metadata`.
[Invariants]
- Assertions read the real working tree; no test writes outside `tmp_path`.
[Timeouts & Locks]
- One bounded subprocess for `release-metadata` (`60s`).
[Side-Effect Policy]
- Read-only repository inspection plus the isolated test environment.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
VERSION = "0.1.32"
_NO_WIN = 0x08000000 if os.name == "nt" else 0
EXPECTED_COMPONENTS = {
    "mindie-harness",
    "mindie-knowledge",
    "remote-dev",
    "mindie-diagnostics",
    "mindie-debugger",
    "mindie-profiler",
    "mindie-sim",
    "npu-top",
    "mindie-coordinator",
}


def _json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_total_script_loc_ceiling() -> None:
    total = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in SCRIPTS.glob("*.py"))
    assert total <= 1350, f"scripts/ totals {total} lines; target ceiling is 1350 (hard ceiling 1500)"


def test_release_version_and_marketplace_manifests_are_current() -> None:
    import entry

    plugin = _json(".claude-plugin/plugin.json")
    marketplace = _json(".claude-plugin/marketplace.json")
    contract = _json("product-contract.json")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    hooks_text = (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
    hooks = json.loads(hooks_text)
    mcp = _json(".mcp.json")
    release_yml = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    checks_yml = (ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
    skill_md = (ROOT / "skills" / "mindie-agent" / "SKILL.md").read_text(encoding="utf-8")

    assert entry.NATIVE_VERSION == VERSION
    assert plugin["version"] == VERSION
    assert marketplace["plugins"][0]["version"] == VERSION
    assert project["project"]["version"] == VERSION
    assert contract["native_version"] == VERSION
    assert set(contract["components"]) == EXPECTED_COMPONENTS
    assert contract["components"]["npu-top"] == "0.3.5"

    declared_deps = project["project"]["dependencies"]
    for pkg in EXPECTED_COMPONENTS:
        assert any(dep.startswith(f"{pkg} @ git+https://github.com/mindie-agent/") for dep in declared_deps), pkg

    assert f"mindie-agent-cc-{VERSION}.md" in release_yml
    assert "mindie-agent/mindie-bench/.github/workflows/verify-host-plugin.yml@main" in checks_yml
    assert "host-acceptance" in checks_yml
    assert "`knowledge` (`knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`)" in skill_md
    for tool_name in ("`npu_top`", "`rsi_loop`", "`experiment_ledger`"):
        assert tool_name in skill_md

    assert VERSION in hooks_text
    assert "python -c" not in hooks_text
    assert "plugin_entry.py" in hooks_text
    previous = "0.1." + "31"
    assert previous not in hooks_text
    assert previous not in json.dumps(mcp)
    assert "python -c" not in json.dumps(mcp)
    assert mcp["mcpServers"]["remote"]["env"]["REMOTE_DEV_HARNESS_PROFILE"] == "claude"
    for name, server in mcp["mcpServers"].items():
        assert any("plugin_entry.py" in arg for arg in server["args"]), name
        assert VERSION in server["args"], name
        assert "-c" not in server["args"], name
    assert set(hooks["hooks"]) >= {"PreToolUse", "Stop", "SessionStart"}
    assert "--offline" not in hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "--offline" in hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "--offline" in hooks["hooks"]["Stop"][0]["hooks"][0]["command"]


def test_bundled_seed_and_host_adapter_contract() -> None:
    import host_adapter
    import mindie_harness.api as api

    info = host_adapter.verify_bundled_seed()
    assert info["schema"] == "mindie-harness-seed/1"
    assert info["size_bytes"] > 0
    assert isinstance(host_adapter.ClaudeHostAdapter(), api.HostAdapter)


def test_release_metadata_matches_source_tree() -> None:
    revision = "a" * 40
    command = [sys.executable, "-B", str(SCRIPTS / "setup.py"), "release-metadata",
               "--source", str(ROOT), "--tests", str(ROOT / "tests"), "--revision", revision]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=60, creationflags=_NO_WIN)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "mindie-plugin-release/1"
    assert payload["version"] == VERSION
    assert payload["source"] == {"repository": "mindie-agent/mindie-agent-cc", "sha": revision}
    assert payload["compatibility"]["harness"] == "claude-code"
    assert payload["compatibility"]["used_interfaces"]
