"""Native-origin fixtures: component authorization, not host acceptance."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from support import make_config
from test_entry import SESSION, expansion

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import entry
import diagnostic_support
import consent


class ReportingEntryTests(unittest.TestCase):
    def test_native_choice_is_once_and_independent(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = make_config(root)
            with patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(config), "XDG_CONFIG_HOME": str(root / "xdg")}):
                event = expansion("mindie-agent:reporting-enable")
                with patch.object(diagnostic_support, "configure_reporting", return_value={"enabled": True}) as configure, patch.object(diagnostic_support, "reporting_status", return_value={"enabled": True}):
                    self.assertTrue(entry.dispatch_event(event)["enabled"])
                    self.assertTrue(entry.dispatch_event(event)["already"])
                    configure.assert_called_once_with(True, sys.executable)
                from admission import gate
                self.assertIsNone(gate().active_lease(SESSION))
                from sharing import public_status
                self.assertFalse(public_status()["enabled"])

    def test_non_native_and_subagent_cannot_enable(self):
        with patch.object(diagnostic_support, "configure_reporting") as configure:
            for overrides in ({"command_source": "user"}, {"agent_id": "subagent"}):
                with self.assertRaises(ValueError):
                    entry.dispatch_event(expansion("mindie-agent:reporting-enable", **overrides))
            configure.assert_not_called()

    def test_damaged_consent_cannot_enable_the_real_reporter(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = make_config(root)
            with patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(config)}):
                path = consent.consent_path()
                path.write_text("{broken")
                with patch.object(diagnostic_support, "configure_reporting") as configure:
                    result = entry._record_reporting_decision(True)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["consent_error"]["state"], "corrupt")
                configure.assert_not_called()
                self.assertEqual(path.read_text(), "{broken")

    def test_explicit_preference_precedes_policy_change_and_preserves_contribution(self):
        with tempfile.TemporaryDirectory() as raw:
            config = make_config(Path(raw))
            with patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(config)}):
                consent.record_choice("read-only")
                def configure(enabled, python):
                    self.assertEqual(consent.load()["reporting"], "enabled")
                    self.assertEqual(consent.load()["choice"], "read-only")
                    return dict(status="unavailable", enabled=None)
                with patch.object(diagnostic_support, "configure_reporting", side_effect=configure):
                    result = entry._record_reporting_decision(True)
                self.assertEqual(result["saved_reporting"], "enabled")
                self.assertFalse(result["policy_change_confirmed"])

    def test_reporting_offer_does_not_repeat_after_setup(self):
        with tempfile.TemporaryDirectory() as raw:
            config = make_config(Path(raw))
            with patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(config)}):
                with patch.object(entry, "_knowledge_status_payload", return_value={}), \
                        patch.object(diagnostic_support, "reporting_status",
                                     return_value={"status": "not_configured"}):
                    self.assertIn("reporting_choice", entry.status_payload())
                    consent.record_choice("read-only")
                    self.assertNotIn("reporting_choice", entry.status_payload())
                    for decision in ("enabled", "disabled", "later"):
                        consent.record_reporting(decision)
                        self.assertNotIn("reporting_choice", entry.status_payload())
