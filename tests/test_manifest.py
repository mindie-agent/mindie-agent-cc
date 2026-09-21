import json
import sys
import tempfile
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
        from setup import runtime_pins

        pins = runtime_pins()
        self.assertEqual(set(pins), {"mindie-knowledge", "remote-dev"})
        self.assertEqual(pins["mindie-knowledge"]["url"], "https://github.com/mindie-agent/knowledge")
        self.assertEqual(pins["remote-dev"]["url"], "https://github.com/mindie-agent/remote-dev")
        for pin in pins.values():
            self.assertRegex(pin["commit"], r"^[0-9a-f]{40}$")

    def test_runtime_manifest_rejects_floating_foreign_duplicate_or_missing_pins(self):
        from setup import runtime_pins

        pins = runtime_pins()
        rows = [f"{name} @ git+{pin['url']}@{pin['commit']}" for name, pin in pins.items()]
        invalid = [
            "\n".join(rows).replace(pins["mindie-knowledge"]["commit"], "main"),
            "\n".join(rows).replace("github.com/mindie-agent/knowledge", "github.com/other/knowledge"),
            "\n".join(rows + [rows[0]]),
            rows[0],
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime-requirements.txt"
            for text in invalid:
                with self.subTest(text=text):
                    path.write_text(text)
                    with self.assertRaises(ValueError):
                        runtime_pins(path)


if __name__ == "__main__":
    unittest.main()
