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
        by_name = {item["name"]: item for item in tools}
        self.assertEqual(
            set(by_name),
            {"mindie_status", "knowledge_query", "knowledge_explain",
             "knowledge_feedback", "knowledge_distill", "knowledge_sync"},
        )
        for ro_name in ("mindie_status", "knowledge_query", "knowledge_explain"):
            self.assertIs(by_name[ro_name]["annotations"]["readOnlyHint"], True)
        for mut_name in ("knowledge_feedback", "knowledge_distill", "knowledge_sync"):
            self.assertIs(by_name[mut_name]["annotations"]["readOnlyHint"], False)
        for item in tools:
            props = item["inputSchema"].get("properties") or {}
            self.assertNotIn("request_nonce", props)
            self.assertNotIn("session_id", props)

    def test_knowledge_calls_work_without_pretool_bind_and_preserve_receipt_honesty(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import identity
        import mcp_server

        with patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(self.config)}), \
                patch.object(mcp_server, "knowledge_call", return_value={"cards": [{"id": "u1"}]}) as kcall:
            # 1. Subagent / unhooked call with ZERO `_meta` falls back cleanly to `_CONNECTION_SESSION_ID`
            subagent_resp = mcp_server.handle("knowledge", {
                "jsonrpc": "2.0",
                "id": "subagent-1",
                "method": "tools/call",
                "params": {"name": "knowledge_query", "arguments": {"query": "AscendC"}},
            })
            self.assertFalse(subagent_resp["result"]["isError"])
            self.assertEqual(subagent_resp["result"]["structuredContent"], {"cards": [{"id": "u1"}]})
            self.assertEqual(kcall.call_args.args[2], mcp_server._CONNECTION_SESSION_ID)

            # 2. Unbound `claudecode/toolUseId` also falls back cleanly instead of failing closed
            unbound_resp = mcp_server.handle("knowledge", {
                "jsonrpc": "2.0",
                "id": "unbound-2",
                "method": "tools/call",
                "params": {
                    "name": "knowledge_query",
                    "arguments": {"query": "AscendC"},
                    "_meta": {"claudecode/toolUseId": "call_00_missing"},
                },
            })
            self.assertFalse(unbound_resp["result"]["isError"])

            # 3. Optional `claim_bind` SQLite/OSError resilience falls back cleanly; TimeoutError still raises
            import sqlite3
            with patch.object(mcp_server, "claim_bind", side_effect=sqlite3.OperationalError("database is locked")):
                sqlite_fallback = mcp_server.handle("knowledge", {
                    "jsonrpc": "2.0",
                    "id": "sqlite-fb",
                    "method": "tools/call",
                    "params": {
                        "name": "knowledge_query",
                        "arguments": {"query": "AscendC"},
                        "_meta": {"claudecode/toolUseId": "call_00_locked"},
                    },
                })
            self.assertFalse(sqlite_fallback["result"]["isError"])

            # 4. Receipt Honesty: post-success SQLite failure keeps isError=False + receipt_warning + receipt_unconfirmed=True
            identity.publish_bind(
                SESSION,
                "call_00_receipt_warn",
                "mcp__plugin_mindie-agent_knowledge__knowledge_query",
                {"query": "AscendC"},
            )
            with patch.object(mcp_server, "finish_bind", side_effect=OSError(28, "disk full")):
                honest = mcp_server.handle("knowledge", {
                    "jsonrpc": "2.0",
                    "id": "honest-3",
                    "method": "tools/call",
                    "params": {
                        "name": "knowledge_query",
                        "arguments": {"query": "AscendC"},
                        "_meta": {"claudecode/toolUseId": "call_00_receipt_warn"},
                    },
                })
            self.assertFalse(honest["result"]["isError"])
            sc = honest["result"]["structuredContent"]
            self.assertIs(sc["receipt_unconfirmed"], True)
            self.assertTrue(sc["receipt_warning"])

    def test_distill_and_sync_budgets_and_feedback_cwd_scope_and_component_cwd(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import admission
        import mcp_server
        import mindie_launch
        import native_mcp
        from mindie_knowledge import consent_store

        # 1. Principle 10 helper timeout budgets: distill >= 450s, sync >= 330s, <= .mcp.json 660s
        mcp_timeout_sec = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["knowledge"]["timeout"] / 1000.0
        distill_outer = mindie_launch._dispatch_budget(
            "knowledge", {"method": "tools/call", "params": {"name": "knowledge_distill", "arguments": {}}}
        )
        sync_outer = mindie_launch._dispatch_budget(
            "knowledge", {"method": "tools/call", "params": {"name": "knowledge_sync", "arguments": {}}}
        )
        self.assertGreaterEqual(distill_outer, 450.0)
        self.assertGreaterEqual(sync_outer, 330.0)
        self.assertGreaterEqual(mcp_timeout_sec, distill_outer)
        self.assertGreaterEqual(mcp_timeout_sec, sync_outer)
        self.assertEqual(native_mcp.READ_LIMIT, 64)

        # 2. `knowledge_feedback` refreshes stale in-scope lease root when called from outside `project_roots`
        sharing_dir = self.tmp / "fb-scope"
        sharing_cfg = make_config(sharing_dir, sharing=True)
        consent_store.record_choice(sharing_dir / "mindie-consent.json", "contribute")
        outside = sharing_dir / "outside"
        outside.mkdir(parents=True)
        with patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(sharing_cfg)}):
            admission.ensure_default_lease(SESSION, project_root=str(sharing_dir / "project"), not_before=0.0)
            self.assertEqual(
                Path(admission.knowledge_lease(SESSION)["project_root"]).resolve(),
                (sharing_dir / "project").resolve(),
            )
            with patch("knowledge_service.ensure_service", return_value={"url": "http://127.0.0.1:1", "token": "t"}), \
                    patch("mindie_knowledge.loop.cli.rpc", return_value={"ok": True}):
                mcp_server.knowledge_call(
                    "knowledge_feedback",
                    {"ref": "mindie://vllm-ascend/units/u1@1", "rating": "up"},
                    SESSION,
                    cwd=str(outside),
                )
            self.assertEqual(
                Path(admission.knowledge_lease(SESSION)["project_root"]).resolve(),
                outside.resolve(),
            )

        # 3. `components` surface forwards effective host cwd (`MINDIE_HOST_CWD`) when unhooked
        host_ws = self.tmp / "host-workspace"
        host_ws.mkdir()
        seen_cwd = []
        with patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(self.config), "MINDIE_HOST_CWD": str(host_ws)}), \
                patch("component_tools.execute", side_effect=lambda name, args, sess, cwd=None: seen_cwd.append(cwd) or {"content": [], "isError": False}):
            comp_resp = mcp_server.handle("components", {
                "jsonrpc": "2.0", "id": "comp-cwd", "method": "tools/call",
                "params": {"name": "diagnostics_status", "arguments": {}},
            })
        self.assertFalse(comp_resp["result"]["isError"])
        self.assertEqual(seen_cwd, [str(host_ws)])

    def test_read_pool_executes_knowledge_query_and_explain_concurrently_during_slow_mutations(self):
        from io import BytesIO
        import threading
        import mcp_server

        mut_entered = threading.Event()
        release_mut = threading.Event()
        seen_reads = []
        outputs = []

        def fake_knowledge_call(name, args, session, **_kw):
            if name == "knowledge_feedback":
                mut_entered.set()
                release_mut.wait(timeout=5.0)
                return {"recorded": True}
            seen_reads.append(name)
            return {"tool": name}

        requests = [
            {"jsonrpc": "2.0", "id": f"mut-{i}", "method": "tools/call",
             "params": {"name": "knowledge_feedback", "arguments": {"ref": "mindie://d/units/u@1", "rating": "up"}}}
            for i in range(mcp_server.MUTATION_POOL_WORKERS)
        ] + [
            {"jsonrpc": "2.0", "id": "ro-query", "method": "tools/call",
             "params": {"name": "knowledge_query", "arguments": {"query": "mla"}}},
            {"jsonrpc": "2.0", "id": "ro-explain", "method": "tools/call",
             "params": {"name": "knowledge_explain", "arguments": {"ref": "mindie://d/units/u@1"}}},
        ]

        class ControlledStdin:
            def __init__(self, items):
                self._lines = [(json.dumps(x) + "\n").encode("utf-8") for x in items]
                self._idx = 0

            def readline(self):
                if self._idx < len(self._lines):
                    line = self._lines[self._idx]
                    self._idx += 1
                    if self._idx == mcp_server.MUTATION_POOL_WORKERS:
                        mut_entered.wait(timeout=2.0)
                    return line
                # Wait until both read-only calls finish BEFORE releasing slow mutations!
                deadline = time.monotonic() + 3.0
                while len(seen_reads) < 2 and time.monotonic() < deadline:
                    import time as _t
                    _t.sleep(0.01)
                release_mut.set()
                return b""

        import time
        fake_stdin = type("FakeStdin", (), {"buffer": ControlledStdin(requests)})()
        with patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(self.config)}), \
                patch.object(mcp_server, "knowledge_call", side_effect=fake_knowledge_call), \
                patch.object(mcp_server, "_resident_binding", return_value=None), \
                patch.object(mcp_server, "send", side_effect=outputs.append), \
                patch("sys.stdin", fake_stdin):
            mcp_server.serve("knowledge")

        by_id = {msg["id"]: msg for msg in outputs}
        self.assertIn("ro-query", by_id)
        self.assertIn("ro-explain", by_id)
        self.assertFalse(by_id["ro-query"]["result"]["isError"])
        self.assertFalse(by_id["ro-explain"]["result"]["isError"])
        self.assertCountEqual(seen_reads, ["knowledge_query", "knowledge_explain"])

    def test_status_default_on_works_with_or_without_bind_and_rejects_replay(self):
        try:
            from mindie_knowledge.loop import settings as _settings  # noqa: F401
        except ImportError:
            self.skipTest("pinned mindie_knowledge.loop.settings is not available")
        env = env_for(self.config)
        sys.path.insert(0, str(ROOT / "scripts"))
        os.environ["MINDIE_CC_CONFIG"] = str(self.config)
        import identity

        unhooked = self._once(
            "knowledge",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "mindie_status", "arguments": {}},
            },
            env=env,
        )
        self.assertFalse(unhooked["result"].get("isError"))
        self.assertTrue(json.loads(unhooked["result"]["content"][0]["text"])["knowledge_enabled"])

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
        self.assertTrue(payload["knowledge_enabled"])
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

    def test_remote_tools_follow_the_trusted_profile_env(self):
        import mcp_server
        from remote_dev.mcp.tools import list_tools

        with patch.dict(os.environ, {"REMOTE_DEV_HARNESS_PROFILE": "claude"}):
            expected = list_tools("claude")
            listed = mcp_server.handle("remote", {
                "id": 1, "method": "tools/list",
            })["result"]["tools"]
            tools = {item["name"]: item for item in listed}
        self.assertEqual(listed, expected)
        self.assertIn("remote_read", tools)
        self.assertEqual(set(tools), {item["name"] for item in expected})
        authentication = tools["remote_auth"]["inputSchema"]["properties"]
        self.assertIn("password_env", authentication)
        self.assertTrue(authentication["password"]["writeOnly"])
        self.assertNotIn("password", tools["remote_read"]["inputSchema"]["properties"])
        self.assertTrue(
            {"remote_Read", "remote_Write", "remote_Edit", "remote_Bash",
             "remote_TaskOutput", "remote_TaskStop", "remote_NotebookEdit",
             "remote_BashOutput"} <= set(tools)
        )
        self.assertNotIn("remote_TaskList", tools)
        self.assertNotIn("remote_write_stdin", tools)


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
                self.assertEqual(root, state / "remote")
                self.assertTrue((root / "remote-layout.json").is_file())
                (root / "job.json").write_text('{"state":"uncertain"}')
                raise RuntimeError("submission uncertain")

            with patch.object(mcp_server, "state_dir", return_value=state), \
                    patch.dict(os.environ, {"REMOTE_DEV_HARNESS_PROFILE": "codex"}), \
                    patch("remote_dev.mcp.tools.call_tool", side_effect=uncertain):
                with self.assertRaisesRegex(RuntimeError, "submission uncertain"):
                    mcp_server.remote_call("remote_bash", {}, session)
            self.assertEqual(receipt_layout.receipt_root(state / "remote"), state / "remote")
            self.assertIn("uncertain", (state / "remote" / "job.json").read_text())

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
    def _shared_call(self, name, args):
        from remote_dev.core import endpoint
        from remote_dev.mcp.tools import call_tool

        with patch.object(endpoint, "_RESOLVERS", []), \
                patch.object(endpoint, "_load_env_resolvers", lambda: None), \
                patch.object(endpoint, "_read_endpoint_aliases_and_default",
                             lambda: ({}, None)):
            return call_tool(name, args, profile="canonical")

    def test_missing_port_preserves_shared_input_repair(self):
        import mcp_server
        from remote_dev.core.errors import EndpointError

        args = {"host": "PRIVATE_ENDPOINT_SENTINEL", "command": "true"}
        with self.assertRaises(EndpointError) as caught:
            self._shared_call("remote_bash", args)
        result = mcp_server.remote_stage_failure(
            args, "remote_bash", "helper_failed", caught.exception)
        structured = result["structuredContent"]
        self.assertTrue(result["isError"])
        self.assertEqual(structured["category"], "caller")
        self.assertEqual(structured["submission_state"], "not_sent")
        self.assertEqual(structured["remote_outcome"], "not_sent")
        self.assertFalse(structured["retryable"])
        self.assertEqual(structured["input_repair"]["missing"], ["port"])
        self.assertIn("host and port", structured["input_repair"]["action"])
        text = result["content"][0]["text"]
        self.assertIn("Missing required argument(s): port", text)
        self.assertIn("host and port", text)
        self.assertIn("category=caller", text)
        self.assertIn("submission_state=not_sent", text)
        rendered = json.dumps(result)
        self.assertNotIn("PRIVATE_ENDPOINT_SENTINEL", rendered)
        self.assertNotIn(str(caught.exception), rendered)

    def test_missing_operation_argument_preserves_shared_input_repair(self):
        import mcp_server

        args = {"host": "192.0.2.10", "port": 22}
        with self.assertRaises(ValueError) as caught:
            self._shared_call("remote_bash", args)
        result = mcp_server.remote_stage_failure(
            args, "remote_bash", "helper_failed", caught.exception)
        structured = result["structuredContent"]
        self.assertEqual(structured["input_repair"]["missing"], ["command"])
        self.assertIn("Retry with command", structured["input_repair"]["action"])
        text = result["content"][0]["text"]
        self.assertIn("Missing required argument(s): command", text)
        self.assertIn("no remote effect occurred", text)

    def test_real_auth_failure_preserves_safe_recovery_hint(self):
        import mcp_server
        from remote_dev.core.errors import AUTH_ERROR_HINTS, RemoteExecutionError

        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ, {
                "REMOTE_DEV_AUTH_DIR": raw, "PRIVATE_MISSING_SSH_PASSWORD": ""}):
            with self.assertRaises(RemoteExecutionError) as caught:
                self._shared_call("remote_auth", {
                    "host": "192.0.2.10", "port": 22, "user": "fixture",
                    "password_env": "PRIVATE_MISSING_SSH_PASSWORD",
                })
        result = mcp_server.remote_stage_failure(
            {}, "remote_auth", "helper_failed", caught.exception)
        details = result["structuredContent"]
        self.assertEqual(details["category"], "credential_unavailable")
        self.assertEqual(details["submission_state"], "not_sent")
        self.assertEqual(details["remote_outcome"], "not_sent")
        self.assertIn(AUTH_ERROR_HINTS["credential_unavailable"], result["content"][0]["text"])
        self.assertNotIn("PRIVATE_MISSING_SSH_PASSWORD", json.dumps(result))

    def test_auth_maintenance_uncertainty_keeps_business_submission(self):
        import mcp_server
        from remote_dev.core.errors import RemoteExecutionError

        for state in ("not_sent", "uncertain"):
            with self.subTest(state=state):
                error = RemoteExecutionError("PRIVATE_STDERR", category="connection_unavailable",
                                             submission_state=state)
                error.bootstrap = dict(status="unknown", installation="unknown",
                                       key_verified=False, error="PRIVATE_STDERR")
                result = mcp_server.remote_stage_failure({}, "remote_auth", "helper_failed", error)
                details = result["structuredContent"]
                self.assertEqual(details["submission_state"], state)
                self.assertEqual(details["remote_outcome"], "not_sent" if state == "not_sent" else "unconfirmed")
                self.assertEqual(details["bootstrap"], dict(status="unknown", installation="unknown", key_verified=False))
                self.assertNotIn("PRIVATE_STDERR", json.dumps(result))

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
