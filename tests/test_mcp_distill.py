"""C6 MCP entry, native bind forwarding and real Service/Engine routing.

The model boundary is one local fake native `claude`; the knowledge Service,
Engine, SummaryLedger and graded application are the real shared sources.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from support import SCRIPTS, make_config, native_fixture, redactor_path

try:
    from mindie_knowledge.graded import distill
except ImportError:  # 0.0.2 runtime has no C6 API; this lane needs joint source.
    raise unittest.SkipTest("joint C6 runtime is not on PYTHONPATH")

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import admission  # noqa: E402
import identity  # noqa: E402
import mcp_server  # noqa: E402
from mindie_knowledge import consent_store  # noqa: E402
from mindie_knowledge.loop.activation import Admission  # noqa: E402
from mindie_knowledge.loop.cli import rpc as shared_rpc  # noqa: E402
from mindie_knowledge.loop.engine import Engine  # noqa: E402
from mindie_knowledge.loop.store import Store, session_key  # noqa: E402
from mindie_knowledge.loop.transport import Service  # noqa: E402

SESSION = "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"
INACTIVE = "745fae2e-ef23-4e7d-9559-f4dd0cb9db6b"
INPUTS = [{
    "kind": "experiment_ledger",
    "outcome": "verified",
    "source_locator": "ledger://mcp-fixture-run-1",
    "content": "Fixture graph probe observed under the declared conditions.",
    "evidence_refs": ["mcp-run-1"],
    "repetitions": 2,
}]


def fake_native(root):
    return native_fixture(Path(root) / "fake-claude",
        "import json, os, sys\n"
        "prompt = sys.stdin.buffer.read().decode()\n"
        "payload = json.loads(prompt.split('\\n\\n', 1)[1])\n"
        "request = payload['request']\n"
        "source = request['sanitized_inputs'][0]\n"
        "marker = os.environ['MINDIE_CC_DISTILL_CALLS']\n"
        "count = int(open(marker).read()) + 1 if os.path.exists(marker) else 1\n"
        "open(marker, 'w').write(str(count))\n"
        "frontmatter = dict(\n"
        "    schema='mindie-graded-unit/1', id='unit-mcp-distill-fixture',\n"
        "    tier='L2', category='sop', evidence_strength='single_observation',\n"
        "    domain_path='ops/fixture', inherits_from=[],\n"
        "    title='MCP fixture graph probe',\n"
        "    triggers={'error_signatures': [], 'keywords': ['fixture']},\n"
        "    conditions={'soc': ['Ascend910B']},\n"
        "    provenance=dict(source_type='session_distill',\n"
        "        source_locator=source['source_locator'],\n"
        "        source_digest=source['source_digest'],\n"
        "        evidence_refs=source['evidence_refs'], supersedes=[]),\n"
        ")\n"
        "decision = dict(schema_version='mindie-distill-decision/1',\n"
        "    session_digest=request['session_digest'],\n"
        "    evidence_inputs=request['evidence_inputs'],\n"
        "    action='propose_graded_units', drop_reason=None,\n"
        "    units=[dict(target_unit_id=frontmatter['id'], target_tier='L2',\n"
        "        category='sop', evidence_strength='single_observation',\n"
        "        domain_path='ops/fixture', operation='create',\n"
        "        frontmatter=frontmatter,\n"
        "        markdown_body='Apply the fixture rule under its declared conditions.')])\n"
        "print(json.dumps(dict(type='result', subtype='success', is_error=False,\n"
        "    num_turns=1, result='', structured_output=decision,\n"
        "    usage=dict(input_tokens=10, cache_creation_input_tokens=0,\n"
        "               cache_read_input_tokens=0, output_tokens=5))), flush=True)\n"
    )


def missing_distill_api():
    """Simulate a paired runtime without the C6 module."""
    import builtins

    real = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "mindie_knowledge.graded" and fromlist and "distill" in fromlist:
            raise ImportError("simulated old runtime")
        if name == "mindie_knowledge.graded.distill":
            raise ImportError("simulated old runtime")
        return real(name, globals, locals, fromlist, level)

    return patch("builtins.__import__", side_effect=guarded)


def _distill_args(task_state="verified_milestone"):
    outcome = "in_flight" if task_state == "in_flight" else INPUTS[0]["outcome"]
    return {"inputs": [dict(INPUTS[0], outcome=outcome)], "task_state": task_state}


class DistillCallUnitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        config = make_config(self.root)
        environment = patch.dict(os.environ, MINDIE_CC_CONFIG=str(config))
        environment.start()
        self.addCleanup(environment.stop)
        self.authority = admission.gate()
        self.lease = self.authority.activate(SESSION, project_root=str(self.root))

    def test_tools_list_exposes_distill_without_identity_properties(self):
        response = mcp_server.handle(
            "knowledge", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {item["name"]: item for item in response["result"]["tools"]}
        self.assertIn("knowledge_distill", tools)
        schema = tools["knowledge_distill"]["inputSchema"]
        self.assertEqual(set(schema["properties"]),
                         {"inputs", "task_state", "domain", "related_refs", "proposal"})
        self.assertEqual(schema["required"], ["inputs", "task_state"])
        self.assertNotIn("_session_id", json.dumps(schema))
        self.assertNotIn("_activation", json.dumps(schema))
        self.assertNotIn("session_id", json.dumps(schema))

    def test_inactive_lease_stops_before_service_or_model(self):
        self.authority.deactivate(SESSION)
        with patch("knowledge_service.ensure_service") as ensure, \
             patch("mindie_knowledge.loop.cli.rpc") as rpc:
            with self.assertRaises(ValueError):
                mcp_server.knowledge_call("knowledge_distill", _distill_args(), SESSION)
        ensure.assert_not_called()
        rpc.assert_not_called()

    def test_missing_shared_runtime_is_explicitly_unavailable(self):
        with missing_distill_api(), \
             patch("knowledge_service.ensure_service") as ensure, \
             patch("mindie_knowledge.loop.cli.rpc") as rpc:
            with self.assertRaisesRegex(ValueError, "unavailable"):
                mcp_server.knowledge_call("knowledge_distill", _distill_args(), SESSION)
        ensure.assert_not_called()
        rpc.assert_not_called()

    def test_active_lease_is_forwarded_as_the_capture_token(self):
        connection = {"url": "http://127.0.0.1:1", "token": "service-token"}
        with patch("knowledge_service.ensure_service", return_value=connection), \
             patch("mcp_server._distill_timeout", return_value=412.5), \
             patch("mindie_knowledge.loop.cli.rpc", return_value={"status": "noop"}) as rpc:
            answer = mcp_server.knowledge_call(
                "knowledge_distill", _distill_args("in_flight"), SESSION)
        self.assertEqual(answer, {"status": "noop"})
        self.assertEqual(rpc.call_args.args[0], connection)
        self.assertEqual(rpc.call_args.args[1], "distill")
        payload = rpc.call_args.args[2]
        self.assertEqual(payload["_session_id"], SESSION)
        self.assertEqual(payload["_activation"], self.lease["token"])
        self.assertNotIn("_session_verified", payload)
        self.assertEqual(rpc.call_args.kwargs["timeout"], 412.5)

    def test_distill_rejects_extra_identity_or_bad_shape_before_service(self):
        bad = [
            {"inputs": INPUTS, "task_state": "in_flight", "_activation": "spoof"},
            {"inputs": [], "task_state": "in_flight"},
            {"inputs": INPUTS, "task_state": "finished-by-stop"},
            {"inputs": INPUTS},
        ]
        for args in bad:
            with self.subTest(args=args), \
                     patch("knowledge_service.ensure_service") as ensure:
                with self.assertRaises(ValueError):
                    mcp_server.knowledge_call("knowledge_distill", args, SESSION)
                ensure.assert_not_called()

    def test_distill_forwards_related_refs_and_foreground_proposal(self):
        connection = {"url": "http://127.0.0.1:1", "token": "service-token"}
        proposal = {
            "schema_version": "mindie-distill-decision/1",
            "session_digest": "a" * 64,
            "evidence_inputs": ["experiment_ledger"],
            "action": "noop",
            "drop_reason": "already_covered",
            "units": [],
        }
        args = {
            **_distill_args(),
            "related_refs": ["unit-existing-fixture"],
            "proposal": proposal,
        }
        with patch("knowledge_service.ensure_service", return_value=connection), \
             patch("mcp_server._distill_timeout", return_value=90.0), \
             patch("mindie_knowledge.loop.cli.rpc",
                   return_value={"status": "noop"}) as rpc:
            answer = mcp_server.knowledge_call("knowledge_distill", args, SESSION)
        self.assertEqual(answer, {"status": "noop"})
        payload = rpc.call_args.args[2]
        self.assertEqual(payload["related_refs"], ["unit-existing-fixture"])
        self.assertEqual(payload["proposal"], proposal)
        self.assertEqual(payload["_activation"], self.lease["token"])
        self.assertEqual(rpc.call_args.kwargs["timeout"], 90.0)

    def test_distill_rejects_bad_related_refs_or_proposal_before_service(self):
        bad = [
            {"related_refs": "unit-existing-fixture"},
            {"related_refs": ["unit-existing-fixture", 7]},
            {"related_refs": [""]},
            {"proposal": []},
            {"proposal": "mindie-distill-decision/1"},
            {"unexpected": True},
        ]
        for extra in bad:
            with self.subTest(extra=extra), \
                     patch("knowledge_service.ensure_service") as ensure:
                with self.assertRaises(ValueError):
                    mcp_server.knowledge_call(
                        "knowledge_distill", {**_distill_args(), **extra}, SESSION)
                ensure.assert_not_called()

    def test_query_forwards_new_graded_parameters_unchanged(self):
        connection = {"url": "http://127.0.0.1:1", "token": "service-token"}
        args = {"query": "fixture", "domain_path": "ops/fixture",
                "token_budget": 321, "include_unknown": True}
        with patch("knowledge_service.ensure_service", return_value=connection), \
             patch("mindie_knowledge.loop.cli.rpc", return_value={"results": []}) as rpc:
            self.assertEqual(mcp_server.knowledge_call("knowledge_query", args, SESSION),
                             {"results": []})
        payload = rpc.call_args.args[2]
        for key, value in args.items():
            self.assertEqual(payload[key], value)
        self.assertEqual(rpc.call_args.kwargs["timeout"], mcp_server.KNOWLEDGE_RPC_TIMEOUT)

    def test_distill_timeout_mirrors_the_shared_rpc_budget(self):
        from mindie_knowledge.loop import summary_budget
        with patch("mindie_knowledge.loop.transcript_redaction._redaction_timeout",
                   return_value=11.0), \
             patch.object(summary_budget, "IDENTITY_SECONDS", 10.0), \
             patch.object(summary_budget, "worker_timeout", return_value=315.0):
            self.assertEqual(mcp_server._distill_timeout(),
                             2 * 11.0 + 10.0 + 315.0 + 6 * 5 + 5.0)


class DistillEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-mcp-distill-")).resolve()
        self.redactor = redactor_path()
        self.native = fake_native(self.tmp)
        self.calls = self.tmp / "native-calls"
        profile = self.tmp / "profile"
        profile.mkdir()
        (profile / "settings.json").write_text(json.dumps({
            "env": {"ANTHROPIC_MODEL": "fixture-model"},
        }), encoding="utf-8")
        with patch("support.redactor_path", return_value=str(self.redactor)):
            self.config = make_config(
                self.tmp, sharing=True,
                summary_command=[sys.executable, str(SCRIPTS / "organizer.py")],
            )
        adapter = json.loads(self.config.read_text(encoding="utf-8"))
        adapter["claude_config_dir"] = str(profile)
        self.config.write_text(json.dumps(adapter), encoding="utf-8")
        self.engine = self.tmp / "cc.engine.json"
        self.env_patch = patch.dict(os.environ, {
            "MINDIE_CC_CONFIG": str(self.config),
            "MINDIE_DIAGNOSTICS_ROOT": str(self.tmp / "diagnostics"),
            "MINDIE_DIAGNOSTICS_CONFIG": str(self.tmp / "diagnostics.json"),
            "MINDIE_CC_BIN": str(self.native),
            "MINDIE_CC_DISTILL_CALLS": str(self.calls),
        })
        self.env_patch.start()
        consent_store.record_choice(self.tmp / "mindie-consent.json", "contribute")
        engine_config = json.loads(self.engine.read_text(encoding="utf-8"))
        self.store = Store(engine_config["root"], engine_config["domain"])
        self.admission = Admission(engine_config["admission_path"])
        self.engine_obj = Engine(
            self.store,
            settings_path=engine_config.get("community_config"),
            admission=self.admission,
            redactor_executable=engine_config.get("redactor_executable"),
            summary_command=engine_config.get("summary_command"),
        )
        self.service = Service(self.engine_obj, admission=self.admission)
        self.ensure_patch = patch(
            "knowledge_service.ensure_service", return_value=self.service.connection)
        self.ensure_patch.start()
        self.thread = threading.Thread(target=self.service.serve, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                shared_rpc(self.service.connection, "status", timeout=0.5)
                break
            except Exception:
                if not self.thread.is_alive():
                    break
                time.sleep(0.02)
        else:
            self.fail("in-process knowledge service did not become ready")

    def tearDown(self):
        try:
            self.ensure_patch.stop()
            self.service.http.shutdown()
            self.thread.join(timeout=5)
        finally:
            try:
                self.store.close()
            finally:
                self.env_patch.stop()
                shutil.rmtree(self.tmp, ignore_errors=True)

    def _call(self, number, tool_name, arguments, session=SESSION):
        call_id = f"call_distill_{number}"
        native_name = f"mcp__plugin_mindie-agent_knowledge__{tool_name}"
        identity.publish_bind(session, call_id, native_name, arguments)
        response = mcp_server.handle("knowledge", {
            "jsonrpc": "2.0",
            "id": number,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
                "_meta": {"claudecode/toolUseId": call_id},
            },
        })
        return response["result"]

    def test_distill_route_requires_lease_and_does_not_call_model_for_in_flight(self):
        inactive = self._call(1, "knowledge_distill", _distill_args(), session=INACTIVE)
        self.assertTrue(inactive["isError"])
        self.assertFalse(self.calls.exists())

        project = self.tmp / "project"
        lease = admission.activate(SESSION, project_root=project)
        self.assertEqual(lease["session"], SESSION)

        applied = self._call(2, "knowledge_distill", _distill_args())
        self.assertFalse(applied["isError"], applied)
        payload = applied["structuredContent"]
        self.assertEqual(payload["status"], "applied")
        self.assertEqual(payload["admitted_unit_ids"], ["unit-mcp-distill-fixture"])
        self.assertEqual(self.calls.read_text(), "1")

        noop = self._call(3, "knowledge_distill", _distill_args("in_flight"))
        self.assertFalse(noop["isError"], noop)
        self.assertEqual(noop["structuredContent"]["status"], "noop")
        self.assertEqual(
            noop["structuredContent"]["decision"]["drop_reason"],
            "in_flight_incomplete",
        )
        self.assertEqual(self.calls.read_text(), "1")

        spoofed = self._call(4, "knowledge_distill", {
            **_distill_args(), "_activation": "spoofed",
        })
        self.assertTrue(spoofed["isError"])
        self.assertEqual(self.calls.read_text(), "1")

        store = Store(self.tmp / "domain", "vllm-ascend")
        try:
            found = store.query("fixture", domain_path="ops/fixture",
                                token_budget=500, include_unknown=True)
        finally:
            store.close()
        self.assertEqual(found["results"][0]["id"], "unit-mcp-distill-fixture")
        self.assertEqual(found["token_budget"], 500)

        query = self._call(5, "knowledge_query", {
            "query": "fixture", "domain_path": "ops/fixture",
            "token_budget": 500, "include_unknown": True,
        })
        self.assertFalse(query["isError"], query)
        self.assertEqual(query["structuredContent"]["token_budget"], 500)
        self.assertEqual(query["structuredContent"]["results"][0]["id"],
                         "unit-mcp-distill-fixture")

    def test_batch_explain_reads_the_installed_unit_closure(self):
        admission.activate(SESSION, project_root=self.tmp / "project")
        applied = self._call(10, "knowledge_distill", _distill_args())
        self.assertFalse(applied["isError"], applied)
        ref = f"mindie://{self.store.domain}/units/unit-mcp-distill-fixture@1"

        explained = self._call(11, "knowledge_explain", {
            "refs": [ref],
            "conditions": {"soc": "Ascend910B"},
            "known_refs": [],
            "token_budget": 2048,
        })
        self.assertFalse(explained["isError"], explained)
        page = explained["structuredContent"]
        self.assertEqual(page["domain"], self.store.domain)
        self.assertEqual([unit["id"] for unit in page["units"]],
                         ["unit-mcp-distill-fixture"])
        self.assertEqual(page["units"][0]["ref"], ref)
        self.assertIn("Apply the fixture rule", page["units"][0]["content"])
        self.assertEqual(page["unresolved"], [])
        self.assertIn("usage", page)

    def test_foreground_proposal_applies_without_a_model_call(self):
        admission.activate(SESSION, project_root=self.tmp / "project")
        proposal = {
            "schema_version": "mindie-distill-decision/1",
            "session_digest": session_key(SESSION),
            "evidence_inputs": ["experiment_ledger"],
            "action": "noop",
            "drop_reason": "already_covered",
            "units": [],
        }
        response = self._call(12, "knowledge_distill", {
            **_distill_args(),
            "related_refs": ["unit-existing-fixture"],
            "proposal": proposal,
        })
        self.assertFalse(response["isError"], response)
        result = response["structuredContent"]
        self.assertEqual(result["status"], "noop")
        self.assertEqual(result["decision"], proposal)
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["model_execution"], "foreground_proposal")
        self.assertEqual(result["operation_status"], "completed")
        self.assertFalse(self.calls.exists())

    def test_failed_or_uncertain_distill_is_not_a_successful_tool_result(self):
        admission.activate(SESSION, project_root=self.tmp / "project")
        for number, status in enumerate(("failed", "outcome_unknown", "invoking"), 20):
            with self.subTest(status=status), patch.object(self.engine_obj, "distill_selected", return_value={
                "status": status, "attempt_id": "fixture-attempt", "model_replayed": False,
                "publication": "not_requested",
            }):
                response = self._call(number, "knowledge_distill", _distill_args())
                self.assertTrue(response["isError"])
                self.assertEqual(response["structuredContent"]["operation_status"], status)
        self.assertFalse(self.calls.exists())

    def test_paid_decision_survives_local_application_failure(self):
        admission.activate(SESSION, project_root=self.tmp / "project")
        with patch.object(self.store.graded, "install", side_effect=OSError("controlled local write failure")):
            response = self._call(30, "knowledge_distill", _distill_args())
        self.assertFalse(response["isError"], response)
        result = response["structuredContent"]
        self.assertEqual(result["operation_status"], "local_application_failed")
        self.assertEqual(result["model_execution"], "completed")
        self.assertEqual(result["result"]["units"][0]["target_unit_id"], "unit-mcp-distill-fixture")
        self.assertIn("controlled local write failure", result["local_error"]["message"])
        applied = self._call(31, "knowledge_distill", _distill_args())
        self.assertEqual(applied["structuredContent"]["status"], "applied")
        self.assertEqual(self.calls.read_text(), "1")


if __name__ == "__main__":
    unittest.main()
