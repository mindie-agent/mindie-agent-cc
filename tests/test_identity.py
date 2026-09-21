import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from support import env_for, make_config

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.config = make_config(self.tmp)
        os.environ["MINDIE_CC_CONFIG"] = str(self.config)
        import identity

        self.identity = identity

    def tearDown(self):
        os.environ.pop("MINDIE_CC_CONFIG", None)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bind_consume_once_and_reject_replay(self):
        session = "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"
        call = "call_00_jGaL2YjbjsulhtOkG9iq8376"
        tool = "mcp__plugin_mindie-agent_knowledge__knowledge_query"
        args = {"query": "rms"}
        status = self.identity.publish_bind(session, call, tool, args, cwd="/tmp")
        self.assertEqual(status, "bound")
        bound = self.identity.claim_bind(
            call, "mcp__plugin_mindie-agent_knowledge__knowledge_query", args
        )
        self.assertEqual(bound["session"], session)
        with self.assertRaises(ValueError):
            self.identity.claim_bind(
                call, "mcp__plugin_mindie-agent_knowledge__knowledge_query", args
            )

    def test_contradictory_bind_is_ambiguous(self):
        session = "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"
        call = "call_00_same"
        tool = "mcp__plugin_mindie-agent_remote__remote.bash"
        self.identity.publish_bind(session, call, tool, {"command": "true"})
        status = self.identity.publish_bind(session, call, tool, {"command": "false"})
        self.assertEqual(status, "ambiguous")
        with self.assertRaises(ValueError):
            self.identity.claim_bind(
                call, "mcp__plugin_mindie-agent_remote__remote.bash", {"command": "true"}
            )

    def test_meta_tool_use_id(self):
        ident = self.identity.meta_tool_use_id(
            {"_meta": {"claudecode/toolUseId": "call_00_abc"}}
        )
        self.assertEqual(ident, "call_00_abc")
        with self.assertRaises(ValueError):
            self.identity.meta_tool_use_id({})

    def test_mcp_name_prefixes(self):
        self.assertTrue(
            self.identity.is_mindie_mcp_tool(
                "mcp__plugin_mindie-agent_knowledge__knowledge_query"
            )
        )
        self.assertTrue(
            self.identity.is_knowledge_tool(
                "mcp__plugin_mindie-agent_knowledge__knowledge_query"
            )
        )
        self.assertTrue(
            self.identity.is_mindie_mcp_tool(
                "mcp__plugin_mindie-agent_remote__remote.bash"
            )
        )
        self.assertFalse(self.identity.is_mindie_mcp_tool("remote.bash"))
        self.assertFalse(self.identity.is_mindie_mcp_tool("Bash"))
        self.assertFalse(
            self.identity.is_mindie_mcp_tool("mcp__plugin_other_knowledge__knowledge_query")
        )
        self.assertEqual(
            self.identity.canonical_native_name("knowledge", "knowledge_query"),
            "mcp__plugin_mindie-agent_knowledge__knowledge_query",
        )


if __name__ == "__main__":
    unittest.main()
