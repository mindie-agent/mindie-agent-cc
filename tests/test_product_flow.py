"""Configuration outcomes; native Claude/model acceptance is separate."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from support import SCRIPTS, make_config

sys.path.insert(0, str(SCRIPTS))


class ProductFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, MINDIE_CC_CONFIG=str(make_config(self.root)),
                         XDG_CONFIG_HOME=str(self.root / 'xdg'))
        env.start()
        self.addCleanup(env.stop)
        import entry
        self.entry = entry

    def test_saved_contribution_does_not_hide_missing_configuration(self):
        import consent
        consent.record_choice('contribute')
        payload = self.entry.status_payload()
        self.assertEqual(payload['experience'], 'needs-configuration')
        self.assertEqual(payload['choices'], [])

    def test_legacy_declines_stay_disabled_without_a_new_menu(self):
        import consent
        for choice in ('read-only', 'later', 'disabled'):
            consent.record_choice(choice)
            payload = self.entry.status_payload()
            self.assertEqual(payload['experience'], 'disabled')
            self.assertEqual(payload['choices'], [])
            self.assertEqual(consent.load()['choice'], choice)

    def test_removed_modes_cannot_complete_new_setup(self):
        import consent
        for word in ('read-only', 'later'):
            with self.assertRaises(ValueError):
                self.entry._init_choice_from_native(word)
        self.assertIsNone(consent.load()['choice'])

    def test_invalid_target_does_not_record_completed_configuration(self):
        import consent
        result = self.entry._apply_contribute({}, 'test-session',
                    dict(command_args='contribute --repository invalid'), True)
        self.assertFalse(result['contribute']['recorded'])
        self.assertIsNone(consent.load()['choice'])
