"""Actual adapter -> local logs -> outbox -> mock Issue transport.

These synthetic checks do not start native services or publish public Issues.
"""
import os
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import diagnostic_support
from mindie_diagnostics.ingestion import ingest
import consent
from mindie_knowledge import consent_store
from mindie_diagnostics.outbox import Outbox
from mindie_diagnostics.reporter import TransportError, publish_one


@contextmanager
def workspace():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw).resolve()
        try:
            yield root
        finally:
            # Native Windows cannot remove a test directory with live log handles.
            from mindie_diagnostics import logging
            with logging._LOCK:
                key = (diagnostic_support.COMPONENT, str(root / "logs"))
                recorder = logging._FAILURE_RECORDERS.pop(key, None)
                if recorder is not None:
                    recorder.close()


class Publisher:
    repository = "example/diagnostics-test"
    timeout = 30

    def __init__(self, uncertain=False):
        self.reads = self.posts = 0
        self.issue = None
        self.body = None
        self.uncertain = uncertain

    def begin_cycle(self):
        pass

    def end_cycle(self):
        pass

    def find_issue(self, item):
        self.reads += 1
        return self.issue

    def create_issue(self, title, body):
        self.posts += 1
        self.body = body
        self.issue = {"number": 1, "html_url": "https://github.com/example/diagnostics-test/issues/1"}
        if self.uncertain:
            raise TransportError("github_timeout", uncertain=True)
        return self.issue


class AutomaticReportingTests(unittest.TestCase):
    def test_fault_is_redacted_deduplicated_and_unknown_post_only_reconciles(self):
        for uncertain in (False, True):
            with self.subTest(uncertain=uncertain), workspace() as root:
                logs, policy = root / "logs", root / "policy.json"
                env = {"MINDIE_DIAGNOSTICS_ROOT": str(logs),
                       "MINDIE_DIAGNOSTICS_CONFIG": str(policy),
                       "MINDIE_CC_CONFIG": str(root / "cc.json")}
                with patch.dict(os.environ, env), patch.object(diagnostic_support, "REPOSITORY", Publisher.repository):
                    consent.record_choice("contribute")
                    configured = diagnostic_support.activate_reporting(ensure=False)
                    self.assertEqual(configured["status"], "configured")
                    original = {"isError": True, "structuredContent": {"outcome": "failed"},
                                "content": [{"type": "text", "text": "original business result"}]}
                    for _ in range(2):
                        diagnostic = diagnostic_support.failure(
                            "mcp.request", "decode", "protocol_mismatch",
                            exception=RuntimeError("PRIVATE_TRANSCRIPT token=SECRET_CANARY"))
                        self.assertTrue(diagnostic["recorded"])
                    attached = diagnostic_support.attach(original, diagnostic)
                    self.assertTrue(attached["isError"])
                    self.assertEqual(attached["structuredContent"], original["structuredContent"])
                    self.assertEqual(attached["content"][0], original["content"][0])
                    clock = [1000.0]
                    queue = Outbox(root / "outbox.db", clock=lambda: clock[0])
                    self.assertEqual(ingest(logs, queue)["enqueued"], 2)
                    self.assertEqual(len(queue.rows()), 1)
                    publisher = Publisher(uncertain)
                    first = publish_one(queue, publisher)
                    self.assertEqual(first["status"], "uncertain" if uncertain else "published")
                    clock[0] += 4000
                    if uncertain:
                        self.assertEqual(publish_one(queue, publisher)["status"], "reconciled")
                    self.assertEqual(publish_one(queue, publisher)["status"], "idle")
                    self.assertEqual(publisher.posts, 1)
                    self.assertEqual(publisher.reads, 2 if uncertain else 1)
                    for excluded in ("PRIVATE_TRANSCRIPT", "SECRET_CANARY", str(root),
                                     diagnostic["incident_id"], "original business result"):
                        self.assertNotIn(excluded, publisher.body)
                    self.assertIn("mindie-agent-cc", publisher.body)

    def test_disabled_faults_are_local_and_not_backfilled(self):
        with workspace() as root:
            logs, policy = root / "logs", root / "policy.json"
            with patch.dict(os.environ, {"MINDIE_DIAGNOSTICS_ROOT": str(logs),
                                       "MINDIE_DIAGNOSTICS_CONFIG": str(policy),
                       "MINDIE_CC_CONFIG": str(root / "cc.json")}):
                diagnostic = diagnostic_support.failure("mcp.request", "decode", "protocol_mismatch")
                self.assertTrue(diagnostic["recorded"])
                queue = Outbox(root / "outbox.db")
                self.assertEqual(ingest(logs, queue)["enqueued"], 0)
                consent.record_choice("contribute")
                self.assertEqual(diagnostic_support.activate_reporting(ensure=False)["status"], "configured")
                self.assertEqual(ingest(logs, Outbox(root / "fresh.db"))["enqueued"], 0)

    def test_disabling_contribution_revokes_a_queued_issue_before_post(self):
        with workspace() as root:
            logs, policy = root / "logs", root / "policy.json"
            with patch.dict(os.environ, {"MINDIE_DIAGNOSTICS_ROOT": str(logs),
                                         "MINDIE_DIAGNOSTICS_CONFIG": str(policy),
                                         "MINDIE_CC_CONFIG": str(root / "cc.json")}), \
                    patch.object(diagnostic_support, "REPOSITORY", Publisher.repository):
                consent.record_choice("contribute")
                diagnostic_support.activate_reporting(ensure=False)
                self.assertTrue(diagnostic_support.failure("mcp.request", "decode", "protocol_mismatch")["recorded"])
                queue = Outbox(root / "outbox.db")
                self.assertEqual(ingest(logs, queue)["enqueued"], 1)
                consent.record_choice("disabled")
                # The old policy is intentionally not refreshed: the final POST
                # guard must consult the live master authority itself.
                publisher = Publisher()
                self.assertEqual(publish_one(queue, publisher)["status"], "withdrawn")
                self.assertEqual(publisher.posts, 0)

    def test_missing_runtime_reports_unavailable_without_local_shadow_state(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            script = (
                "import json,sys;sys.path.insert(0,sys.argv[1]);"
                "from diagnostic_support import failure;"
                "print(json.dumps(failure('mcp.request','decode','protocol_mismatch',"
                "exception=RuntimeError('PRIVATE_SECRET'))))"
            )
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-c", script,
                 str(Path(__file__).resolve().parents[1] / "scripts")],
                env=dict(os.environ, MINDIE_DIAGNOSTICS_ROOT=str(root / "logs"),
                         MINDIE_DIAGNOSTICS_CONFIG=str(root / "policy.json")),
                text=True, capture_output=True, timeout=5,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout), {"logging_failed": True})
            self.assertEqual(result.stderr.strip(),
                             "MindIE diagnostic storage unavailable; original outcome unchanged.")
            self.assertEqual(list(root.iterdir()), [])
