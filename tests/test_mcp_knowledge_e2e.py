"""End-to-end MCP knowledge tool dispatch through real mcp_server.py and mindie_knowledge service."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from mindie_knowledge import consent_store
from mindie_knowledge.loop.lifecycle import retire_service
from mindie_knowledge.loop.store import Store

from support import SCRIPTS, make_config

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import admission  # noqa: E402
import identity  # noqa: E402
import mcp_server  # noqa: E402

SESSION = "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"


class McpKnowledgeEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-mcp-e2e-")).resolve()
        self.config = make_config(self.tmp, sharing=False)
        self.engine = self.tmp / "cc.engine.json"
        self.env_patch = patch.dict(
            os.environ,
            {
                "MINDIE_CC_CONFIG": str(self.config),
                "MINDIE_DIAGNOSTICS_ROOT": str(self.tmp / "diagnostics"),
            },
        )
        self.env_patch.start()
        community = json.loads((self.tmp / "cc.community.json").read_text(encoding="utf-8"))
        community["repository"] = None
        (self.tmp / "cc.community.json").write_text(
            json.dumps(community, indent=2) + "\n", encoding="utf-8"
        )
        consent_store.record_choice(self.tmp / "mindie-consent.json", "disabled")

        store = Store(self.tmp / "domain", "vllm-ascend")
        try:
            self.seeded = store.create_draft(
                kind="experience",
                title="Ascend 910B HCCL ring initialization timeout",
                summary="Diagnose and resolve HCCL_CONNECT_TIMEOUT on Ascend 910B RoCEv2.",
                content=(
                    "Verify HCCL_IF_IP and ranktable device NIC mappings with "
                    "hccn_tool -i 0 -ip -g when HCCL ring initialization times out."
                ),
                conditions={"chip": "910b"},
            )
            store.advance_search_index()
        finally:
            store.close()

    def tearDown(self):
        try:
            retire_service(str(self.engine), control_timeout=5)
        except Exception:
            pass
        finally:
            self.env_patch.stop()
            shutil.rmtree(self.tmp, ignore_errors=True)

    def _call_knowledge(self, req_id: int, tool_name: str, arguments: dict) -> dict:
        call_id = f"call_e2e_{req_id}"
        native_name = f"mcp__plugin_mindie-agent_knowledge__{tool_name}"
        identity.publish_bind(SESSION, call_id, native_name, arguments)
        response = mcp_server.handle(
            "knowledge",
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                    "_meta": {"claudecode/toolUseId": call_id},
                },
            },
        )
        return response["result"]

    def test_mcp_knowledge_query_explain_feedback_end_to_end(self):
        project = self.tmp / "project"
        project.mkdir(parents=True, exist_ok=True)
        lease = admission.activate(SESSION, project_root=project)
        self.assertEqual(lease["session"], SESSION)

        # 1. knowledge_query
        query_res = self._call_knowledge(
            1,
            "knowledge_query",
            {"query": "HCCL ring initialization timeout", "limit": 5},
        )
        self.assertFalse(query_res.get("isError"), f"Unexpected query error: {query_res}")
        structured_query = query_res["structuredContent"]
        self.assertEqual(structured_query["domain"], "vllm-ascend")
        self.assertGreaterEqual(len(structured_query["results"]), 1)
        first_hit = structured_query["results"][0]
        self.assertEqual(first_hit["entry_id"], self.seeded["entry_id"])
        block_ref = first_hit["ref"]

        # 2. knowledge_explain
        explain_res = self._call_knowledge(
            2,
            "knowledge_explain",
            {"ref": block_ref},
        )
        self.assertFalse(explain_res.get("isError"), f"Unexpected explain error: {explain_res}")
        structured_explain = explain_res["structuredContent"]
        self.assertEqual(structured_explain["entry_id"], self.seeded["entry_id"])
        self.assertIn("hccn_tool", structured_explain["content"])
        feedback_ref = structured_explain["feedback_ref"]

        # 3. knowledge_feedback
        feedback_res = self._call_knowledge(
            3,
            "knowledge_feedback",
            {
                "ref": feedback_ref,
                "rating": "up",
                "reason": "Verified HCCL NIC mapping fix on Ascend 910B.",
            },
        )
        self.assertFalse(feedback_res.get("isError"), f"Unexpected feedback error: {feedback_res}")
        structured_feedback = feedback_res["structuredContent"]
        self.assertEqual(structured_feedback["entry_id"], self.seeded["entry_id"])
        self.assertEqual(structured_feedback["rating"], "up")
        self.assertTrue(structured_feedback["vote_id"])


if __name__ == "__main__":
    unittest.main()
