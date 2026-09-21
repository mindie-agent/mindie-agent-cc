import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import env_for, make_config

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "scripts" / "bridge.py"
LAUNCH = ROOT / "scripts" / "mindie_launch.py"

SESSION = "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, script, op, payload, env):
        proc = subprocess.run(
            [sys.executable, str(script), *op],
            input=json.dumps(payload).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=5,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        return json.loads(proc.stdout.decode().splitlines()[0])

    def test_stop_default_off_no_state(self):
        config = make_config(self.tmp, sharing=False)
        env = env_for(config)
        result = self._run(
            BRIDGE,
            ["stop"],
            dict(
                hook_event_name="Stop",
                session_id=SESSION,
                prompt_id="p1",
                cwd=str(self.tmp),
                transcript_path=str(self.tmp / f"{SESSION}.jsonl"),
                stop_hook_active=False,
            ),
            env,
        )
        self.assertEqual(result, {})
        self.assertFalse((self.tmp / "domain" / "store-v3.sqlite3").exists())

    def test_launch_stop_skips_stdin_when_sharing_off(self):
        config = make_config(self.tmp, sharing=False)
        env = env_for(config)
        proc = subprocess.run(
            [sys.executable, str(LAUNCH), "hook", "stop"],
            input=b"",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=3,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout.decode()), {})

    def test_pretool_binds_plugin_tool(self):
        config = make_config(self.tmp)
        env = env_for(config)
        payload = dict(
            hook_event_name="PreToolUse",
            session_id=SESSION,
            prompt_id="p1",
            cwd=str(self.tmp),
            transcript_path=str(self.tmp / f"{SESSION}.jsonl"),
            tool_name="mcp__plugin_mindie-agent_knowledge__knowledge_query",
            tool_use_id="call_00_bindtest",
            tool_input={"query": "x"},
        )
        result = self._run(BRIDGE, ["pretool"], payload, env)
        self.assertEqual(result, {})
        bind = self.tmp / "state" / "mcp-binds.sqlite3"
        self.assertTrue(bind.is_file())

    def test_expansion_unconfigured(self):
        env = env_for(extra={"XDG_CONFIG_HOME": str(self.tmp / "cfg")})
        payload = dict(
            hook_event_name="UserPromptExpansion",
            expansion_type="slash_command",
            command_name="mindie-agent:init",
            command_source="plugin",
            command_args="",
            session_id=SESSION,
            prompt_id="143eccc4-86da-4095-bfa9-9b8f3416b31f",
            cwd=str(self.tmp),
            transcript_path=str(self.tmp / f"{SESSION}.jsonl"),
        )
        result = self._run(BRIDGE, ["expansion"], payload, env)
        self.assertIn("additionalContext", result)
        self.assertIn("contribute", result["additionalContext"])


if __name__ == "__main__":
    unittest.main()
