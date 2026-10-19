"""Native Claude Code MCP feedback on exact observed graded-unit revisions."""

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

import support  # noqa: E402
import identity  # noqa: E402
import mcp_server  # noqa: E402
from mindie_knowledge.graded.units import GradedUnit  # noqa: E402
from mindie_knowledge.loop.activation import Admission  # noqa: E402
from mindie_knowledge.loop.cli import rpc as shared_rpc  # noqa: E402
from mindie_knowledge.loop.engine import Engine  # noqa: E402
from mindie_knowledge.loop.store import Store, session_key  # noqa: E402
from mindie_knowledge.loop.transport import Service  # noqa: E402


SESSION = "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"
UNIT_ID = "unit-graded-feedback-entry"


def graded_unit(revision=1, body="Fixture graded feedback rule for the observed revision.\n"):
    return GradedUnit.from_parts({
        "schema": "mindie-graded-unit/1",
        "id": UNIT_ID,
        "tier": "L2",
        "category": "sop",
        "evidence_strength": "single_observation",
        "domain_path": "ops/graded-feedback",
        "revision": revision,
        "inherits_from": [],
        "title": "Synthetic graded feedback entry",
        "triggers": {
            "error_signatures": ["SYNTHETIC_GRADED_FEEDBACK"],
            "keywords": ["gradedfeedbackprobe"],
        },
        "conditions": {"soc": ["Ascend910B"]},
        "provenance": {
            "source_type": "session_distill",
            "source_locator": "fixture://graded-feedback-entry",
            "source_digest": "c" * 64,
            "evidence_refs": ["fixture-observation"],
            "supersedes": [],
        },
    }, body, f"ops/graded-feedback/{UNIT_ID}.md")


class GradedFeedbackEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-graded-feedback-")).resolve()
        (self.tmp / "project").mkdir()
        with patch.object(support, "redactor_path", return_value=sys.executable):
            self.config = support.make_config(self.tmp)
        adapter = json.loads(self.config.read_text(encoding="utf-8"))
        engine_path = Path(adapter["engine_config"])
        config = json.loads(engine_path.read_text(encoding="utf-8"))
        self.store = Store(config["root"], config["domain"])
        self.admission = Admission(config["admission_path"])
        self.admission.activate(SESSION, project_root=str(self.tmp / "project"))
        self.engine = Engine(
            self.store,
            settings_path=config.get("community_config"),
            admission=self.admission,
            redactor_executable=config.get("redactor_executable"),
            summary_command=config.get("summary_command"),
        )
        self.service = Service(self.engine, admission=self.admission)
        self.env_patch = patch.dict(os.environ, {
            "MINDIE_CC_CONFIG": str(self.config),
            "MINDIE_DIAGNOSTICS_ROOT": str(self.tmp / "diagnostics"),
        })
        self.env_patch.start()
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
        finally:
            try:
                self.service.http.shutdown()
                self.service.http.server_close()
                self.thread.join(timeout=5)
            finally:
                try:
                    self.store.close()
                finally:
                    self.env_patch.stop()
                    shutil.rmtree(self.tmp, ignore_errors=True)

    def _install(self, unit):
        self.store.graded.install([unit])

    def _call(self, number, name, arguments):
        call_id = f"call_graded_feedback_{number}"
        native_name = f"mcp__plugin_mindie-agent_knowledge__{name}"
        identity.publish_bind(SESSION, call_id, native_name, arguments)
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

    def _query(self, number):
        result = self._call(number, "knowledge_query", {
            "query": "gradedfeedbackprobe",
            "include_unknown": True,
            "limit": 5,
        })
        self.assertFalse(result["isError"], result)
        return result["structuredContent"]["results"][0]

    def _feedback(self, number, ref, rating, reason):
        result = self._call(number, "knowledge_feedback", {
            "ref": ref, "rating": rating, "reason": reason,
        })
        self.assertFalse(result["isError"], result)
        return result["structuredContent"]

    def _votes(self):
        rows = [dict(row) for row in self.store.db.execute("SELECT * FROM votes")]
        root = self.store.db.execute(
            "SELECT opaque FROM opaque_roots WHERE root_hash=?",
            (session_key(SESSION),),
        ).fetchone()
        return rows, (root["opaque"] if root else None)

    def test_query_feedback_records_exact_observed_revision_and_native_session(self):
        unit = graded_unit()
        self._install(unit)

        hit = self._query(200)
        observed = f"mindie://{self.store.domain}/units/{UNIT_ID}@{unit.content_sha256}"
        self.assertEqual(hit["feedback_ref"], observed)
        self.assertEqual(hit["revision"], 1)

        vote = self._feedback(201, hit["feedback_ref"], "up",
                              "Confirmed during the fixture run.")
        self.assertEqual(vote["unit_id"], UNIT_ID)
        self.assertEqual(vote["revision"], 1)
        self.assertEqual(vote["content_digest"], unit.metadata["content_digest"])
        self.assertEqual(vote["rating"], "up")

        rows, opaque = self._votes()
        self.assertIsNotNone(opaque)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_id"], UNIT_ID)
        self.assertEqual(rows[0]["revision"], unit.content_sha256)
        self.assertEqual(rows[0]["root_opaque"], opaque)

    def test_stale_ref_keeps_the_observed_revision_after_the_unit_is_revised(self):
        original = graded_unit()
        self._install(original)

        first = self._query(210)
        revised = graded_unit(
            2, "Fixture graded feedback rule corrected for the next revision.\n")
        self._install(revised)
        second = self._query(211)
        self.assertNotEqual(first["feedback_ref"], second["feedback_ref"])
        self.assertEqual(
            second["feedback_ref"],
            f"mindie://{self.store.domain}/units/{UNIT_ID}@{revised.content_sha256}",
        )

        old_vote = self._feedback(212, first["feedback_ref"], "down",
                                  "The first revision failed.")
        new_vote = self._feedback(213, second["feedback_ref"], "up",
                                  "The correction worked.")
        self.assertEqual((old_vote["revision"], old_vote["content_digest"]),
                         (1, original.metadata["content_digest"]))
        self.assertEqual((new_vote["revision"], new_vote["content_digest"]),
                         (2, revised.metadata["content_digest"]))

        rows, _ = self._votes()
        by_revision = {row["revision"]: row for row in rows}
        self.assertEqual(set(by_revision), {original.content_sha256, revised.content_sha256})
        self.assertEqual(by_revision[original.content_sha256]["rating"], "down")
        self.assertEqual(by_revision[revised.content_sha256]["rating"], "up")


if __name__ == "__main__":
    unittest.main()
