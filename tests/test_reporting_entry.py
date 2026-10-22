"""One native contribution decision configures both delivery paths."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from support import make_config
from test_entry import expansion

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import entry
import diagnostic_support
import consent


class ReportingEntryTests(unittest.TestCase):
    def test_fixed_runtime_and_background_service_share_the_profile_policy(self):
        import mindie_launch
        from paths import service_launch_context
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = make_config(root)
            adapter = json.loads(config.read_text())
            runtime = root / "runtime" / "adapter.json"
            runtime.parent.mkdir()
            runtime.write_text(json.dumps(adapter))
            env = {k: v for k, v in os.environ.items()
                   if k not in {"MINDIE_DIAGNOSTICS_CONFIG", "MINDIE_DIAGNOSTICS_ROOT"}}
            env["MINDIE_CC_CONFIG"] = str(runtime)
            with patch.dict(os.environ, env, clear=True):
                expected = str(config.parent / "diagnostics.json")
                self.assertEqual(str(diagnostic_support.reporting_config_path()), expected)
                self.assertEqual(mindie_launch._child_env(
                    {"adapter_config": str(runtime)})["MINDIE_DIAGNOSTICS_CONFIG"], expected)
                self.assertEqual(service_launch_context(runtime, adapter)["paths"]
                                 ["MINDIE_DIAGNOSTICS_CONFIG"], expected)
                consent.record_choice("contribute")
                diagnostic_support.activate_reporting(ensure=False)
                self.assertEqual(json.loads(Path(expected).read_text())["consent_config"],
                                 str(config.parent / "mindie-consent.json"))

    def test_contribution_handoff_uses_same_profile_and_finishes_worker_activation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = make_config(root)
            policy = root / "diagnostics.json"
            with patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(config),
                                         "MINDIE_DIAGNOSTICS_CONFIG": str(policy),
                                         "MINDIE_DIAGNOSTICS_ROOT": ""}):
                with patch.object(entry, "_prepare_capture_service", return_value="prepared"):
                    result = entry._finish_contribution({})
                self.assertEqual(result["status"], "reporter_pending")
                self.assertTrue(consent.load()["choice_revision"])
                self.assertEqual(consent.load()["choice"], "contribute")
                self.assertIsNone(consent.load()["reporting"])
                reporting = result["reporting"]
                self.assertTrue(reporting["run_outside_hook"])
                self.assertEqual(reporting["worker"]["status"], "not_started")
                self.assertEqual(reporting["command"][-2:], ["--config", str(config)])
                before = consent.consent_path().read_bytes()
                with patch("mindie_diagnostics.reporting_commands.ensure", return_value={"status": "running"}) as start:
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        code = diagnostic_support.main(reporting["command"][2:])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out.getvalue())["status"], "running")
                start.assert_called_once()
                self.assertEqual(Path(start.call_args.args[0]), policy)
                self.assertEqual(consent.consent_path().read_bytes(), before)

    def test_legacy_reporting_commands_guide_without_changing_either_authority(self):
        with tempfile.TemporaryDirectory() as raw:
            config = make_config(Path(raw))
            with patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(config)}):
                consent.record_choice("disabled")
                before = consent.consent_path().read_bytes()
                result = entry.dispatch_event(expansion("mindie-agent:reporting-enable"))
                self.assertEqual(result["status"], "contribution_choice_required")
                self.assertIn("contribute", result["enable"])
                self.assertEqual(consent.consent_path().read_bytes(), before)

    def test_non_native_and_subagent_cannot_enable(self):
        with patch.object(diagnostic_support, "activate_reporting") as configure:
            for overrides in ({"command_source": "user"}, {"agent_id": "subagent"}):
                with self.assertRaises(ValueError):
                    entry.dispatch_event(expansion("mindie-agent:reporting-enable", **overrides))
            configure.assert_not_called()

    def test_damaged_consent_preserves_bytes_and_never_prepares_worker(self):
        with tempfile.TemporaryDirectory() as raw:
            config = make_config(Path(raw))
            with patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(config)}):
                path = consent.consent_path()
                path.write_text("{broken")
                with patch.object(diagnostic_support, "activation_handoff") as configure, \
                        patch.object(entry, "_prepare_capture_service") as capture:
                    result = entry._finish_contribution({})
                self.assertEqual(result["status"], "degraded")
                self.assertEqual(result["consent_error"]["state"], "corrupt")
                configure.assert_not_called()
                capture.assert_not_called()
                self.assertEqual(path.read_text(), "{broken")

    def test_worker_failure_is_visible_after_consent_was_saved(self):
        with tempfile.TemporaryDirectory() as raw:
            config = make_config(Path(raw))
            with patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(config),
                                         "MINDIE_DIAGNOSTICS_CONFIG": str(Path(raw) / "diagnostics.json")}):
                consent.record_choice("contribute")
                with patch("mindie_diagnostics.reporting_commands.ensure", return_value={"status": "degraded"}):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        code = diagnostic_support.main(["activate", "--config", str(config)])
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(out.getvalue())["worker"]["status"], "degraded")
                self.assertEqual(consent.load()["choice"], "contribute")
