"""Component checks for adapter projection and explicit reporting choice."""
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import diagnostic_support as support


class DiagnosticSupportTests(unittest.TestCase):
    def test_empty_reference_cannot_suppress_new_failure(self):
        incident = "a" * 32
        for old in ({}, {"logging_failed": False}, {"incident_id": "not-an-id"}):
            result = {"isError": True, "content": [{"type": "text", "text": "original"}],
                      "structuredContent": {"job_id": "keep-job", "diagnostic": old}}
            updated = support.attach(result, {"incident_id": incident, "logging_failed": False})
            self.assertEqual(updated["diagnostic"]["incident_id"], incident)
            self.assertEqual(updated["structuredContent"], result["structuredContent"])
            self.assertEqual(result["content"], [{"type": "text", "text": "original"}])

    def test_trusted_inner_reference_is_not_rewritten(self):
        inner = {"incident_id": "b" * 32, "logging_failed": False}
        result = {"content": [], "structuredContent": {"diagnostic": inner}}
        with patch.object(support, "_record", side_effect=AssertionError("must not record")):
            self.assertEqual(support.failure("mcp", "helper", "protocol",
                                            incident_id=inner["incident_id"]), inner)
            self.assertIs(support.attach(result, {"incident_id": "a" * 32}), result)

    def test_logging_failure_cannot_replace_original_error(self):
        class Broken:
            def write(self, value):
                raise BrokenPipeError("private-error")
        with patch.object(support, "_warned", False), patch.object(support, "_record", side_effect=ImportError("private-error")), patch("sys.stderr", Broken()):
            self.assertEqual(support.failure("mcp", "helper", "protocol"), {"logging_failed": True})

    def test_projection_drops_uncontracted_fields(self):
        raw = {"recorded": True, "incident_id": "a" * 32, "logging_failed": False,
               "secret": "never-export"}
        with patch.object(support, "_record", return_value=lambda *args, **kw: raw):
            self.assertEqual(support.failure("mcp", "helper", "protocol"),
                             {key: raw[key] for key in ("recorded", "incident_id", "logging_failed")})
        with patch.object(support, "_record", return_value=lambda *args, **kw: (True, "a" * 32, False)), patch("sys.stderr", io.StringIO()):
            self.assertEqual(support.failure("mcp", "helper", "protocol"), {"logging_failed": True})

    def test_windows_ensure_command_is_powershell_executable(self):
        command = [r"C:\Users\Owner's Name\python.exe", "-m", "mindie_diagnostics.cli",
                   "--config", r"C:\Users\Owner's Name\reporting.json"]
        with patch.object(support, "os", types.SimpleNamespace(name="nt")):
            line = support.ensure_command_line(command)
        self.assertEqual(line, "& 'C:\\Users\\Owner''s Name\\python.exe' '-m' "
                         "'mindie_diagnostics.cli' '--config' "
                         "'C:\\Users\\Owner''s Name\\reporting.json'")

    def test_stale_policy_cannot_override_a_disabled_or_legacy_authority(self):
        policy = dict(status="configured", enabled=True, worker={"status": "idle"})
        for saved in ({"state": "ok", "choice": "contribute", "choice_revision": None},
                      {"state": "ok", "choice": "disabled", "choice_revision": "a" * 32}):
            result = support.effective_reporting(policy, saved)
            self.assertFalse(result["enabled"])
        ready = {"state": "ok", "choice": "contribute", "choice_revision": "a" * 32}
        self.assertTrue(support.effective_reporting(policy, ready)["enabled"])
