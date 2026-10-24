import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
            {"mindie_status", "knowledge_query", "knowledge_explain",
             "knowledge_feedback", "knowledge_distill"},
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

    def test_status_without_knowledge_activation_still_requires_native_bind(self):
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
        self.assertFalse(payload["knowledge_enabled"])
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





class RemoteObservationBoundTests(unittest.TestCase):
    def test_only_poll_yield_is_capped(self):
        import mcp_server
        args = {"yield_time_ms": 300000, "timeout_ms": 600000}
        self.assertEqual(mcp_server.clamp_remote_args(args),
                         {"yield_time_ms": 30000, "timeout_ms": 600000})
        self.assertEqual(args["yield_time_ms"], 300000)
        for invalid in ("300000", True, 1.5):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                mcp_server.clamp_remote_args({"yield_time_ms": invalid})

    def test_uncertain_remote_effect_keeps_the_actual_session_layout(self):
        import mcp_server
        import receipt_layout
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ):
            state = Path(tmp)
            session = "native-session"

            def uncertain(_name, _args):
                self.assertEqual(os.environ.get("REMOTE_DEV_HARNESS_PROFILE"), "claude")
                root = Path(os.environ["REMOTE_DEV_STATE_DIR"])
                self.assertEqual(root, state / "remote" / session)
                self.assertTrue((root.parent / "remote-layout.json").is_file())
                (root / "job.json").write_text('{"state":"uncertain"}')
                raise RuntimeError("submission uncertain")

            with patch.object(mcp_server, "state_dir", return_value=state), \
                    patch.dict(os.environ, {"REMOTE_DEV_HARNESS_PROFILE": "codex"}), \
                    patch("remote_dev.mcp.tools.call_tool", side_effect=uncertain):
                with self.assertRaisesRegex(RuntimeError, "submission uncertain"):
                    mcp_server.remote_call("remote_bash", {}, session)
            self.assertEqual(receipt_layout.receipt_root(state / "remote"), state / "remote")
            self.assertIn("uncertain", (state / "remote" / session / "job.json").read_text())

    def test_incompatible_layout_prevents_dispatch_without_replacing_receipts(self):
        import mcp_server
        from remote_dev.core.errors import RemoteExecutionError
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            base = state / "remote"
            base.mkdir()
            layout = base / "remote-layout.json"
            layout.write_text(json.dumps({
                "schema": "mindie-cc-remote-layout/1", "format": 9,
                "release_version": "0.0.1",
            }))
            before = layout.read_bytes()
            with patch.object(mcp_server, "state_dir", return_value=state), \
                    patch("remote_dev.mcp.tools.call_tool") as dispatch:
                with self.assertRaises(RemoteExecutionError) as caught:
                    mcp_server.remote_call("remote_bash", {}, "native-session")
                self.assertEqual(caught.exception.submission_state, "not_sent")
                dispatch.assert_not_called()
            self.assertEqual(layout.read_bytes(), before)
            self.assertFalse((base / "native-session").exists())


class RemoteFailureProjectionTests(unittest.TestCase):
    def test_only_typed_fields_survive(self):
        import mcp_server
        from remote_dev.core.errors import RemoteExecutionError, caller_error
        result = mcp_server.remote_stage_failure(
            {"job_id": "original-job"}, "remote_job_status", "helper_failed",
            RemoteExecutionError("PRIVATE_SENTINEL", category="rpc_timeout",
                                 submission_state="uncertain", retryable=False))
        self.assertEqual(result["structuredContent"], {
            "stage": "helper_failed", "remote_outcome": "unconfirmed",
            "job_id": "original-job", "category": "rpc_timeout",
            "submission_state": "uncertain", "retryable": False})
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))
        result = mcp_server.remote_stage_failure(
            {}, "remote_bash", "helper_failed", caller_error("PRIVATE_SENTINEL"))
        self.assertEqual(result["structuredContent"]["remote_outcome"], "not_sent")
        self.assertNotIn("unconfirmed", json.dumps(result))
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))

    def test_unrecognized_category_cannot_expose_lowercase_secret(self):
        import mcp_server
        from remote_dev.core.errors import RemoteExecutionError
        result = mcp_server.remote_stage_failure(
            {}, "remote_bash", "helper_failed",
            RemoteExecutionError("other_secret", category="lower_case_secret"))
        self.assertEqual(result["structuredContent"]["category"], "internal")
        self.assertNotIn("secret", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
