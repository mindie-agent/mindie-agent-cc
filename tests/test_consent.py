"""F2 consent authority for the CC adapter: profile-shared persistent
choice, legacy migration, damaged-state honesty. Real files, no network."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import SCRIPTS, make_config

ROOT = Path(__file__).resolve().parents[1]


class ConsentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MINDIE_CC_CONFIG"] = str(make_config(self.tmp))
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        for name in ("entry", "entry_state", "consent", "paths", "sharing"):
            sys.modules.pop(name, None)
        sys.path.insert(0, str(SCRIPTS))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("MINDIE_CC_CONFIG", None)
        os.environ.pop("XDG_CONFIG_HOME", None)

    def _payload(self, session=None):
        import entry

        return entry._knowledge_status_payload(session)

    def test_choice_persists_and_is_never_reasked(self):
        import entry_state

        payload = self._payload()
        self.assertEqual(payload["choices"], [])
        self.assertEqual(payload["experience"], "needs-configuration")
        __import__("consent").record_choice("later")
        payload = self._payload()
        self.assertEqual(payload["first_use"], "later")
        self.assertEqual(payload["choices"], [])
        self.assertTrue(payload["repeat"])

    def test_legacy_marker_choice_migrates_once(self):
        """Status does not migrate. Entry attach imports the marker once."""
        from paths import first_use_path
        import consent
        import entry

        marker = first_use_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"choice": "read-only"}))
        payload = self._payload()
        self.assertIsNone(payload.get("first_use"))
        self.assertEqual(payload["choices"], [])
        self.assertFalse(consent.consent_path().exists())
        project = self.tmp / "project"
        project.mkdir()
        event = dict(
            hook_event_name="UserPromptExpansion",
            expansion_type="slash_command",
            command_name="mindie-agent",
            command_source="plugin",
            command_args="",
            session_id="645fae2e-ef23-4e7d-9559-f4dd0cb9db6a",
            prompt_id="143eccc4-86da-4095-bfa9-9b8f3416b31f",
            cwd=str(project.resolve()),
            transcript_path=str((project / "transcript.jsonl").resolve()),
        )
        entered = entry.dispatch_event(event)
        self.assertEqual(consent.load().get("choice"), "read-only")
        self.assertEqual(entered.get("choices"), [])
        marker.unlink()
        again = self._payload()
        self.assertEqual(again.get("first_use"), "read-only")
        self.assertEqual(again.get("choices"), [])

    def test_corrupt_marker_does_not_reonboard(self):
        from paths import first_use_path

        first_use_path().parent.mkdir(parents=True, exist_ok=True)
        first_use_path().write_text("{broken-json")
        payload = self._payload()
        self.assertEqual(payload["choices"], [])
        self.assertTrue(payload["repeat"])

    def test_corrupt_consent_is_a_fault_not_onboarding(self):
        import consent

        path = consent.consent_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken-json")
        payload = self._payload()
        self.assertEqual(payload["choices"], [])
        self.assertEqual(payload["consent_state"], "corrupt")
        self.assertIn("consent_error", payload)

    def test_corrupt_settings_is_a_fault_not_onboarding(self):
        import consent

        consent.resolve_community_path().write_text("{broken-json")
        payload = self._payload()
        self.assertEqual(payload["choices"], [])
        self.assertEqual(payload["sharing"]["state"], "corrupt")

    def test_kimi_choice_is_reused_in_this_profile(self):
        raw = os.environ.get("MINDIE_TEST_KIMI_SCRIPTS")
        self.assertTrue(
            raw,
            "set MINDIE_TEST_KIMI_SCRIPTS to the other adapter's scripts directory",
        )
        kimi_scripts = Path(raw)
        self.assertTrue((kimi_scripts / "consent.py").is_file(), kimi_scripts)
        (self.tmp / "kimi.json").write_text(json.dumps({}))
        env = dict(os.environ)
        env.pop("MINDIE_CC_CONFIG", None)
        env["MINDIE_KIMI_CONFIG"] = str(self.tmp / "kimi.json")
        code = (
            "import sys;sys.path.insert(0,sys.argv[1]);"
            "import consent;consent.record_choice('later')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(kimi_scripts)],
            env=env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self._payload()
        self.assertEqual(payload["first_use"], "later")
        self.assertEqual(payload["choices"], [])

    def test_isolated_profile_does_not_inherit(self):
        import entry_state

        __import__("consent").record_choice("later")
        other = self.tmp / "other-profile"
        other.mkdir()
        (other / "cc.json").write_text(json.dumps({}))
        env = dict(os.environ, MINDIE_CC_CONFIG=str(other / "cc.json"))
        code = (
            "import sys,json;sys.path.insert(0,sys.argv[1]);"
            "import consent;print(json.dumps(consent.load()))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(SCRIPTS)],
            env=env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "missing")


if __name__ == "__main__":
    unittest.main()
