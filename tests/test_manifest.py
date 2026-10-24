import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class ManifestTests(unittest.TestCase):
    def test_plugin_and_marketplace(self):
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["name"], "mindie-agent")
        self.assertEqual(market["name"], "mindie-agent-cc")
        self.assertEqual(market["plugins"][0]["source"], "./")
        self.assertEqual(market["plugins"][0]["name"], "mindie-agent")
        self.assertEqual(plugin["version"], market["plugins"][0]["version"])
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        expansion = hooks["hooks"]["UserPromptExpansion"]
        matchers = {item["matcher"] for item in expansion}
        self.assertIn("mindie-agent:init", matchers)
        self.assertIn("mindie-agent:sharing-enable", matchers)
        self.assertTrue(hooks["hooks"]["Stop"])
        matcher = hooks["hooks"]["PreToolUse"][0]["matcher"]
        self.assertTrue(matcher.startswith("^mcp__plugin_mindie-agent_"))
        self.assertEqual(hooks["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"], 20)
        self.assertEqual(hooks["hooks"]["Stop"][0]["hooks"][0]["timeout"], 20)
        mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        self.assertIn("knowledge", mcp["mcpServers"])
        self.assertIn("remote", mcp["mcpServers"])
        self.assertEqual(
            mcp["mcpServers"]["remote"].get("env"),
            {"REMOTE_DEV_HARNESS_PROFILE": "claude"},
        )
        self.assertNotIn("env", mcp["mcpServers"]["knowledge"])
        self.assertNotIn("hooks", plugin)
        self.assertNotIn("mcpServers", plugin)

    def test_runtime_pins(self):
        from product_contract import requirements
        pins, digest = requirements(ROOT)
        self.assertEqual(set(pins), {"mindie-knowledge", "remote-dev", "mindie-diagnostics"})
        self.assertEqual(len(digest), 64)
        for pin in pins.values():
            self.assertRegex(pin, r"^[0-9a-f]{40}$")

    def test_hot_hooks_cannot_download_python_or_install_a_runtime(self):
        hooks = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
        version = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
        for name, rows in hooks.items():
            for row in rows:
                for hook in row["hooks"]:
                    self.assertIn("CLAUDE_PLUGIN_DATA", hook["command"])
                    self.assertIn(version, hook["command"])
                    if name != "SessionStart":
                        self.assertIn("--offline --no-python-downloads", hook["command"])

    def test_runtime_lock_rejects_foreign_or_floating_git_sources(self):
        from product_contract import requirements
        original = (ROOT / "uv.lock").read_text(encoding="utf-8")
        for old, new in (("github.com/mindie-agent/knowledge", "github.com/other/knowledge"),
                         ("?rev=", "?branch=")):
            with tempfile.TemporaryDirectory() as directory:
                Path(directory, "uv.lock").write_text(original.replace(old, new), encoding="utf-8")
                with self.assertRaises(ValueError):
                    requirements(Path(directory))
