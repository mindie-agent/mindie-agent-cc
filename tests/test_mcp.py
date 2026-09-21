import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import env_for, make_config

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "mcp_server.py"
SESSION = "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"


class McpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.config = make_config(self.tmp)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _once(self, surface, message, env=None):
        proc = subprocess.run(
            [sys.executable, str(SERVER), surface, "--once"],
            input=(json.dumps(message) + "\n").encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env or env_for(self.config),
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        lines = [line for line in proc.stdout.decode().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, proc.stdout.decode())
        return json.loads(lines[0])

    def test_tools_list_knowledge_has_no_nonce(self):
        response = self._once(
            "knowledge",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        tools = response["result"]["tools"]
        names = {item["name"] for item in tools}
        self.assertEqual(
            names,
            {"mindie_status", "knowledge_query", "knowledge_explain", "knowledge_feedback"},
        )
        for item in tools:
            props = item["inputSchema"].get("properties") or {}
            self.assertNotIn("request_nonce", props)
            self.assertNotIn("session_id", props)

    def test_knowledge_call_without_bind_fails_closed(self):
        response = self._once(
            "knowledge",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "knowledge_query",
                    "arguments": {"query": "x"},
                    "_meta": {"claudecode/toolUseId": "call_00_missing"},
                },
            },
        )
        self.assertTrue(response["result"]["isError"])

    def test_status_after_bind(self):
        try:
            from mindie_knowledge.loop import settings as _settings  # noqa: F401
        except ImportError:
            self.skipTest("pinned mindie_knowledge.loop.settings is not available")
        env = env_for(self.config)
        sys.path.insert(0, str(ROOT / "scripts"))
        os.environ["MINDIE_CC_CONFIG"] = str(self.config)
        import identity

        identity.publish_bind(
            SESSION,
            "call_00_status",
            "mcp__plugin_mindie-agent_knowledge__mindie_status",
            {},
        )
        response = self._once(
            "knowledge",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "mindie_status",
                    "arguments": {},
                    "_meta": {"claudecode/toolUseId": "call_00_status"},
                },
            },
            env=env,
        )
        self.assertFalse(response["result"].get("isError"))
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertTrue(payload["configured"])
        replay = self._once(
            "knowledge",
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "mindie_status",
                    "arguments": {},
                    "_meta": {"claudecode/toolUseId": "call_00_status"},
                },
            },
            env=env,
        )
        self.assertTrue(replay["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
