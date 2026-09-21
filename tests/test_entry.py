import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from support import make_config

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


SESSION = "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"
PROMPT = "143eccc4-86da-4095-bfa9-9b8f3416b31f"


def expansion(command, args="", **extra):
    event = dict(
        hook_event_name="UserPromptExpansion",
        expansion_type="slash_command",
        command_name=command,
        command_source="plugin",
        command_args=args,
        session_id=SESSION,
        prompt_id=PROMPT,
        cwd="/tmp/project",
        transcript_path=f"/tmp/{SESSION}.jsonl",
    )
    event.update(extra)
    return event


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ.pop("MINDIE_CC_CONFIG", None)
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "cfg")
        import entry
        import entry_state
        import identity

        self.entry = entry
        self.entry_state = entry_state
        self.identity = identity

    def tearDown(self):
        os.environ.pop("MINDIE_CC_CONFIG", None)
        os.environ.pop("XDG_CONFIG_HOME", None)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unconfigured_init_three_choices(self):
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "cfg")
        payload = self.entry.dispatch_event(expansion("mindie-agent:init"))
        self.assertFalse(payload["configured"])
        self.assertEqual(len(payload["choices"]), 3)
        self.assertTrue(payload["choices"][0]["recommended"])

    def test_init_read_only_persists(self):
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "cfg")
        payload = self.entry.dispatch_event(
            expansion("mindie-agent:init", "read-only")
        )
        self.assertEqual(payload["first_use"], "read-only")
        self.assertEqual(payload.get("choices"), [])

    def test_rejects_non_plugin_source(self):
        with self.assertRaises(ValueError):
            self.entry.dispatch_event(expansion("mindie-agent:init", command_source="user"))

    def test_rejects_subagent(self):
        with self.assertRaises(ValueError):
            self.entry.dispatch_event(expansion("mindie-agent:init", agent_id="agent_1"))

    def test_recover_returns_cli_without_running(self):
        try:
            from mindie_knowledge.loop import settings as _settings  # noqa: F401
        except ImportError:
            self.skipTest("pinned mindie_knowledge is not available")
        config = make_config(self.tmp)
        os.environ["MINDIE_CC_CONFIG"] = str(config)
        payload = self.entry.op_recover(
            SESSION,
            expansion("mindie-agent:recover", "--batch B1 retry"),
        )
        self.assertTrue(payload["run_outside_hook"])
        self.assertEqual(payload["operation"], "contribution-retry")
        self.assertIn("contribution-retry", payload["command_line"])
        self.assertIn("--batch", payload["command_line"])
        self.assertNotIn("actions", payload)

    def test_sharing_enable_requires_native_args(self):
        with self.assertRaises(ValueError):
            self.entry._parse_sharing("")
        with self.assertRaises(ValueError):
            self.entry._parse_sharing("--repository owner/repo")


if __name__ == "__main__":
    unittest.main()
