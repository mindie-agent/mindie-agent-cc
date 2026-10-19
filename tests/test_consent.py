"""F2 consent authority for the CC adapter: profile-shared persistent
choice and damaged-state honesty. Real files, no network."""

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



    def test_corrupt_consent_is_a_fault_not_onboarding(self):
        import consent

        path = consent.consent_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken-json", encoding="utf-8")
        payload = self._payload()
        self.assertEqual(payload["choices"], [])
        self.assertEqual(payload["consent_state"], "corrupt")
        self.assertIn("consent_error", payload)

    def test_corrupt_settings_is_a_fault_not_onboarding(self):
        import consent

        consent.resolve_community_path().write_text("{broken-json", encoding="utf-8")
        payload = self._payload()
        self.assertEqual(payload["choices"], [])
        self.assertEqual(payload["sharing"]["state"], "corrupt")

    def test_kimi_choice_is_reused_in_this_profile(self):
        from mindie_knowledge import consent_store

        kimi_config = self.tmp / "kimi.json"
        kimi_config.write_text(json.dumps({}), encoding="utf-8")
        consent_store.record_choice(kimi_config.parent / "mindie-consent.json", "later")
        payload = self._payload()
        self.assertEqual(payload["first_use"], "later")
        self.assertEqual(payload["choices"], [])

    def test_isolated_profile_does_not_inherit(self):
        __import__("consent").record_choice("later")
        other = self.tmp / "other-profile"
        other.mkdir()
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

    def test_runtime_binding_does_not_change_the_saved_user_choices(self):
        import consent

        consent.record_choice("disabled")
        base = Path(os.environ["MINDIE_CC_CONFIG"])
        authority = consent.consent_path()
        before = authority.read_bytes()
        fixed = self.tmp / "runtime-binding" / "adapter.json"
        fixed.parent.mkdir()
        fixed.write_text(json.dumps({"base_config": str(base)}), encoding="utf-8")
        os.environ["MINDIE_CC_CONFIG"] = str(fixed)
        assert consent.consent_path() == authority
        assert consent.load()["choice"] == "disabled"
        assert consent.load()["choice_revision"]
        assert authority.read_bytes() == before


if __name__ == "__main__":
    unittest.main()
