"""Native MCP entry routes two configured semantic repositories by domain.

The default and target engine configs, Stores, Admissions, Engines and Services
are real shared sources; only the in-process service handoff is mapped to the
already-running local services. The graded fixture is the real one from
``test_graded_feedback_entry`` so the same unit id carries different revisions
and bodies in two independent domains.
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

ROOT = Path(__file__).resolve().parents[1]
for location in (ROOT / "tests", ROOT / "scripts"):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

import entry  # noqa: E402
import identity  # noqa: E402
import mcp_server  # noqa: E402
import support  # noqa: E402
from mindie_knowledge import consent_store  # noqa: E402
from mindie_knowledge.loop.activation import Admission  # noqa: E402
from mindie_knowledge.loop.cli import rpc as shared_rpc  # noqa: E402
from mindie_knowledge.loop.engine import Engine  # noqa: E402
from mindie_knowledge.loop.store import Store  # noqa: E402
from mindie_knowledge.loop.transport import Service  # noqa: E402
from test_graded_feedback_entry import graded_unit  # noqa: E402

SESSION = "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"
DISTILL_SESSION = "745fae2e-ef23-4e7d-9559-f4dd0cb9db6b"
DEFAULT_DOMAIN = "vllm-ascend"
TARGET_DOMAIN = "operators"
UNIT_ID = "unit-graded-feedback-entry"
DISTILL_INPUTS = [{
    "kind": "experiment_ledger",
    "outcome": "verified",
    "source_locator": "ledger://domain-routing-fixture",
    "content": "Fixture target-domain evidence for the routing entry.",
    "evidence_refs": ["domain-routing-fixture"],
    "repetitions": 2,
}]


class DomainRoutingEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-domain-routing-")).resolve()
        with patch.object(support, "redactor_path", return_value=sys.executable):
            self.config = support.make_config(self.tmp, sharing=False)
            self.target_tmp = self.tmp / "target"
            self.target_tmp.mkdir()
            support.make_config(self.target_tmp, sharing=True)

        self.default_engine_path = self.tmp / "cc.engine.json"
        self.target_engine_path = self.target_tmp / "cc.engine.json"
        self.marker = self.tmp / "target-model-called"
        native = self.tmp / "target-native.py"
        native.write_text(
            "from pathlib import Path\n"
            f"Path({str(self.marker)!r}).write_text('called')\n"
            "print('{}')\n",
            encoding="utf-8",
        )
        target_engine = json.loads(
            self.target_engine_path.read_text(encoding="utf-8"))
        target_engine["domain"] = TARGET_DOMAIN
        target_engine["summary_command"] = [sys.executable, str(native)]
        self.target_engine_path.write_text(
            json.dumps(target_engine, indent=2) + "\n", encoding="utf-8")

        adapter = json.loads(self.config.read_text(encoding="utf-8"))
        adapter["domain_configs"] = {
            TARGET_DOMAIN: str(self.target_engine_path.resolve()),
        }
        self.config.write_text(
            json.dumps(adapter, indent=2) + "\n", encoding="utf-8")

        self.default_engine_config = json.loads(
            self.default_engine_path.read_text(encoding="utf-8"))
        self.target_engine_config = json.loads(
            self.target_engine_path.read_text(encoding="utf-8"))
        self.default_store = Store(
            self.default_engine_config["root"],
            self.default_engine_config["domain"],
        )
        self.target_store = Store(
            self.target_engine_config["root"],
            self.target_engine_config["domain"],
        )
        self.default_admission = Admission(
            self.default_engine_config["admission_path"])
        self.target_admission = Admission(
            self.target_engine_config["admission_path"])
        self.default_engine = Engine(
            self.default_store,
            settings_path=self.default_engine_config.get("community_config"),
            admission=self.default_admission,
            redactor_executable=self.default_engine_config.get("redactor_executable"),
            summary_command=self.default_engine_config.get("summary_command"),
        )
        self.target_engine = Engine(
            self.target_store,
            settings_path=self.target_engine_config.get("community_config"),
            admission=self.target_admission,
            redactor_executable=self.target_engine_config.get("redactor_executable"),
            summary_command=self.target_engine_config.get("summary_command"),
        )
        self.default_service = Service(
            self.default_engine, admission=self.default_admission)
        self.target_service = Service(
            self.target_engine, admission=self.target_admission)
        self.services = (self.default_service, self.target_service)

        # The target repository owns its sharing authority; the default
        # repository's consent/settings are never copied to it. No target
        # lease is pre-seeded: public query/explain must not activate one.
        consent_store.record_choice(
            self.target_tmp / "mindie-consent.json", "contribute")

        self.env_patch = patch.dict(os.environ, {
            "MINDIE_CC_CONFIG": str(self.config),
            "MINDIE_DIAGNOSTICS_ROOT": str(self.tmp / "diagnostics"),
            "MINDIE_DIAGNOSTICS_CONFIG": str(self.tmp / "diagnostics.json"),
        })
        self.env_patch.start()
        self.connections = {
            str(self.default_engine_path.resolve()): self.default_service.connection,
            str(self.target_engine_path.resolve()): self.target_service.connection,
        }
        self.ensure_calls = []

        def ensure_service(engine_file=None):
            self.ensure_calls.append(engine_file)
            if engine_file is None:
                return self.default_service.connection
            return self.connections[str(Path(engine_file).resolve())]

        self.ensure_patch = patch(
            "knowledge_service.ensure_service", side_effect=ensure_service)
        self.ensure_patch.start()
        self.threads = []
        for service in self.services:
            thread = threading.Thread(target=service.serve, daemon=True)
            thread.start()
            self.threads.append(thread)
        for service in self.services:
            self._wait_ready(service)

    def _wait_ready(self, service):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                shared_rpc(service.connection, "status", timeout=0.5)
                return
            except Exception:
                time.sleep(0.02)
        self.fail("in-process knowledge service did not become ready")

    def tearDown(self):
        try:
            self.ensure_patch.stop()
        finally:
            for service in self.services:
                try:
                    service.http.shutdown()
                    service.http.server_close()
                except Exception:
                    pass
            for thread in self.threads:
                thread.join(timeout=5)
            try:
                self.default_store.close()
            finally:
                try:
                    self.target_store.close()
                finally:
                    self.env_patch.stop()
                    shutil.rmtree(self.tmp, ignore_errors=True)

    def _call(self, number, name, arguments, session=SESSION):
        call_id = f"call_domain_routing_{number}"
        native_name = f"mcp__plugin_mindie-agent_knowledge__{name}"
        identity.publish_bind(session, call_id, native_name, arguments)
        response = mcp_server.handle("knowledge", {
            "jsonrpc": "2.0",
            "id": number,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
                "_meta": {"claudecode/toolUseId": call_id},
            },
        })
        return response["result"]

    def _query(self, number, **extra):
        arguments = {
            "query": "gradedfeedbackprobe",
            "include_unknown": True,
            "limit": 5,
        }
        arguments.update(extra)
        result = self._call(number, "knowledge_query", arguments)
        self.assertFalse(result["isError"], result)
        payload = result["structuredContent"]
        self.assertEqual(payload["domain"], extra.get("domain", DEFAULT_DOMAIN))
        self.assertEqual(len(payload["results"]), 1)
        return payload["results"][0]

    def test_query_and_uri_reads_select_two_independent_domains(self):
        default_body = "Default domain graded rule for the routing fixture.\n"
        target_body = "Target operators graded rule for the routing fixture.\n"
        self.default_store.graded.install([graded_unit(1, default_body)])
        target_unit = graded_unit(9, target_body)
        self.target_store.graded.install([target_unit])

        default_hit = self._query(100)
        self.assertEqual(default_hit["revision"], 1)
        self.assertEqual(default_hit["ref"],
                         f"mindie://{DEFAULT_DOMAIN}/units/{UNIT_ID}@1")
        self.assertEqual(default_hit["feedback_ref"], default_hit["ref"])

        target_hit = self._query(101, domain=TARGET_DOMAIN)
        self.assertEqual(target_hit["revision"], 9)
        self.assertEqual(target_hit["ref"],
                         f"mindie://{TARGET_DOMAIN}/units/{UNIT_ID}@9")
        self.assertEqual(target_hit["feedback_ref"], target_hit["ref"])
        self.assertNotEqual(default_hit["ref"], target_hit["ref"])

        default_read = self._call(
            102, "knowledge_explain", {"ref": default_hit["ref"]})
        self.assertFalse(default_read["isError"], default_read)
        default_page = default_read["structuredContent"]
        self.assertEqual(default_page["domain"], DEFAULT_DOMAIN)
        self.assertIn(default_body.strip(), default_page["content"])
        self.assertNotIn(target_body.strip(), default_page["content"])

        target_read = self._call(
            103, "knowledge_explain", {"ref": target_hit["ref"]})
        self.assertFalse(target_read["isError"], target_read)
        target_page = target_read["structuredContent"]
        self.assertEqual(target_page["domain"], TARGET_DOMAIN)
        self.assertIn(target_body.strip(), target_page["content"])
        self.assertNotIn(default_body.strip(), target_page["content"])
        # Public reads never create a target lease, even when the target has
        # its own consent/scope configured.
        self.assertIsNone(self.target_admission.active_lease(SESSION))
        self.assertFalse(
            Path(self.target_engine_config["admission_path"]).exists())

        # Activate the target through the real native slash entry, not by
        # pre-seeding its Admission; feedback then uses that target lease.
        target_generation = json.loads(
            Path(self.target_engine_config["community_config"]).read_text(
                encoding="utf-8"))["generation"]
        activated = entry.dispatch_event({
            "hook_event_name": "UserPromptExpansion",
            "expansion_type": "slash_command",
            "command_name": "mindie-agent",
            "command_source": "plugin",
            "command_args": f"--domain {TARGET_DOMAIN}",
            "session_id": SESSION,
            "prompt_id": "domain-routing-feedback-activation",
            "cwd": str(self.target_tmp / "project"),
            "transcript_path": str(self.tmp / "transcript.jsonl"),
        })
        self.assertEqual(activated["domain"], TARGET_DOMAIN)
        self.assertEqual(activated["generation"], target_generation)
        self.assertEqual(activated["binding"]["engine_config"],
                         str(self.target_engine_path.resolve()))
        self.assertIsNotNone(self.target_admission.active_lease(SESSION))

        vote_result = self._call(104, "knowledge_feedback", {
            "ref": target_page["feedback_ref"],
            "rating": "up",
            "reason": "Confirmed during the target domain fixture run.",
        })
        self.assertFalse(vote_result["isError"], vote_result)
        vote = vote_result["structuredContent"]
        self.assertEqual(vote["unit_id"], UNIT_ID)
        self.assertTrue(vote["publishable"],
                        "target sharing consent and target lease must admit it")
        target_votes = [
            dict(row) for row in self.target_store.db.execute("SELECT * FROM votes")
        ]
        default_votes = [
            dict(row) for row in self.default_store.db.execute("SELECT * FROM votes")
        ]
        self.assertEqual(len(target_votes), 1)
        self.assertEqual(target_votes[0]["entry_id"], UNIT_ID)
        self.assertEqual(default_votes, [])

    def test_unknown_domain_and_conflicting_domain_reference_are_visible(self):
        unknown = self._call(110, "knowledge_query", {
            "query": "gradedfeedbackprobe",
            "include_unknown": True,
            "domain": "older-operators",
        })
        self.assertTrue(unknown["isError"])
        self.assertIn("unknown domain", unknown["content"][0]["text"])

        missing_uri = self._call(111, "knowledge_explain", {
            "ref": f"mindie://older-operators/units/{UNIT_ID}@1",
        })
        self.assertTrue(missing_uri["isError"])
        self.assertIn("unknown domain", missing_uri["content"][0]["text"])

        from knowledge_service import select_engine

        with self.assertRaisesRegex(ValueError, "conflicts"):
            select_engine(
                domain=TARGET_DOMAIN,
                ref=f"mindie://{DEFAULT_DOMAIN}/units/{UNIT_ID}@1",
            )

    def test_missing_target_distill_lease_fails_before_service_or_model(self):
        project = self.tmp / "default-project"
        project.mkdir()
        self.default_admission.activate(DISTILL_SESSION, project_root=str(project))
        self.assertIsNotNone(self.default_admission.active_lease(DISTILL_SESSION))
        self.assertIsNone(self.target_admission.active_lease(DISTILL_SESSION))
        self.ensure_calls.clear()

        result = self._call(120, "knowledge_distill", {
            "inputs": [dict(DISTILL_INPUTS[0])],
            "task_state": "verified_milestone",
            "domain": TARGET_DOMAIN,
        }, session=DISTILL_SESSION)

        self.assertTrue(result["isError"], result)
        self.assertIn("task binding is not active", result["content"][0]["text"])
        self.assertEqual(self.ensure_calls, [])
        self.assertFalse(self.marker.exists(),
                         "a missing target lease must fail before the model worker")

    def test_installed_runtime_without_selector_keeps_default_and_fails_explicit(self):
        import builtins

        from knowledge_service import select_engine

        real_import = builtins.__import__
        default_ref = f"mindie://{DEFAULT_DOMAIN}/units/{UNIT_ID}@1"
        target_ref = f"mindie://{TARGET_DOMAIN}/units/{UNIT_ID}@9"

        def missing_helper(name, *args, **kwargs):
            if name == "mindie_knowledge.loop.domain_routes":
                error = ModuleNotFoundError(
                    "No module named 'mindie_knowledge.loop.domain_routes'")
                error.name = name
                raise error
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=missing_helper):
            self.assertEqual(select_engine(), (None, None))
            self.assertEqual(select_engine(ref=default_ref), (None, None))
            with self.assertRaisesRegex(ValueError, "unavailable"):
                select_engine(domain=TARGET_DOMAIN)
            with self.assertRaisesRegex(ValueError, "unavailable"):
                select_engine(ref=target_ref)

            connection = {"url": "http://127.0.0.1:1", "token": "service-token"}
            with patch("knowledge_service.ensure_service",
                       return_value=connection) as ensure, \
                 patch("mindie_knowledge.loop.cli.rpc",
                       return_value={"results": []}) as rpc:
                self.assertEqual(
                    mcp_server.knowledge_call(
                        "knowledge_query", {"query": "fixture"}, SESSION),
                    {"results": []},
                )
                ensure.assert_called_once_with()
                self.assertNotIn("domain", rpc.call_args.args[2])

                ensure.reset_mock()
                with self.assertRaisesRegex(ValueError, "unavailable"):
                    mcp_server.knowledge_call(
                        "knowledge_explain", {"ref": target_ref}, SESSION)
                ensure.assert_not_called()

        def broken_internal(name, *args, **kwargs):
            if name == "mindie_knowledge.loop.domain_routes":
                error = ModuleNotFoundError("No module named 'internal_dependency'")
                error.name = "internal_dependency"
                raise error
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=broken_internal):
            with self.assertRaises(ModuleNotFoundError) as caught:
                select_engine(domain=TARGET_DOMAIN)
        self.assertEqual(caught.exception.name, "internal_dependency")


if __name__ == "__main__":
    unittest.main()
