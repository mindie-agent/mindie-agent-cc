import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class ManifestTests(unittest.TestCase):
    def test_plugin_and_marketplace(self):
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
        self.assertEqual(plugin["name"], "mindie-agent")
        self.assertEqual(market["name"], "mindie-agent-cc")
        self.assertEqual(market["plugins"][0]["source"], "./")
        self.assertEqual(market["plugins"][0]["name"], "mindie-agent")
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        expansion = hooks["hooks"]["UserPromptExpansion"]
        matchers = {item["matcher"] for item in expansion}
        self.assertIn("mindie-agent:init", matchers)
        self.assertIn("mindie-agent:sharing-enable", matchers)
        self.assertTrue(hooks["hooks"]["Stop"])
        matcher = hooks["hooks"]["PreToolUse"][0]["matcher"]
        self.assertTrue(matcher.startswith("^mcp__plugin_mindie-agent_"))
        import mindie_launch

        self.assertEqual(mindie_launch.HOOK_TOTAL, 1.5)
        skill = (ROOT / "skills" / "init" / "SKILL.md").read_text()
        self.assertIn("disable-model-invocation: true", skill)
        mcp = json.loads((ROOT / ".mcp.json").read_text())
        self.assertIn("knowledge", mcp["mcpServers"])
        self.assertIn("remote", mcp["mcpServers"])
        self.assertNotIn("hooks", plugin)
        self.assertNotIn("mcpServers", plugin)

    def test_runtime_pins(self):
        text = (ROOT / "runtime-requirements.txt").read_text()
        self.assertIn("40063ebfc94a4b0fcf508a2a653af8b6403a1a0b", text)
        self.assertIn("13301ef7f52b53ffca0a6702a8a3c18f2edfcd52", text)
        self.assertNotIn("@main", text)


if __name__ == "__main__":
    unittest.main()
