import json
import unittest
import tempfile
from unittest.mock import patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import organizer  # noqa: E402


class OrganizerTests(unittest.TestCase):
    def test_selected_profile_only_materializes_provider_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "settings.json").write_text(json.dumps({
                "env": {"ANTHROPIC_AUTH_TOKEN": "fixture-secret",
                        "ANTHROPIC_MODEL": "selected-model",
                        "CLAUDE_CODE_EFFORT_LEVEL": "high",
                        "UNRELATED_SETTING": "must-not-import"},
                "hooks": {"Stop": "must-not-run"},
            }), encoding="utf-8")
            with patch.dict(organizer.os.environ, {}, clear=True), patch(
                "paths.load_adapter_config", return_value={"claude_config_dir": tmp}
            ):
                env = organizer.native_environment()
            self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "fixture-secret")
            self.assertEqual(organizer.organizer_model(env), "selected-model")
            self.assertEqual(organizer.organizer_effort(env), "low")
            self.assertNotIn("UNRELATED_SETTING", env)
            self.assertNotIn("hooks", env)

    def test_summary_cannot_return_a_body_or_entries(self):
        expected = dict(title='Public case', summary='Public observations')
        self.assertEqual(organizer.normalize(expected), expected)
        for result in (dict(expected, content='replacement'), {'entries': []}, dict(title='x', summary='')):
            with self.assertRaises(ValueError):
                organizer.normalize(result)

    def test_public_json_result(self):
        expected = dict(title='Public case', summary='Public observations')
        native = json.dumps(dict(type='result', result=json.dumps(expected), session_id='private'))
        self.assertEqual(organizer.normalize(organizer.extract_json(organizer.public_result_text(native))), expected)

    def test_no_shipped_host_model_default(self):
        self.assertFalse(hasattr(organizer, "DEFAULT_MODEL"))
        source = Path(organizer.__file__).read_text(encoding="utf-8")
        self.assertNotIn("deepseek-flash", source)
        self.assertNotIn("DEFAULT_MODEL", source)





class OrganizerFailureTests(unittest.TestCase):
    def test_typed_failures_do_not_include_provider_text(self):
        from bounded import CommandCancelled, CommandTimedOut, OutputLimitExceeded
        cases = ((CommandTimedOut("private"), "deadline"),
                 (OutputLimitExceeded("private"), "output_limit"),
                 (RuntimeError("private"), "native"))
        for failure, category in cases:
            with self.subTest(category=category), patch.object(
                organizer, "native_environment", return_value={}
            ), patch.object(organizer, "claude_bin", return_value="controlled"), patch.object(
                organizer, "run", side_effect=failure
            ):
                with self.assertRaises(organizer._Category) as raised:
                    organizer.run_native({})
                self.assertEqual(raised.exception.category, category)
                self.assertNotIn("private", str(raised.exception))
        with patch.object(organizer, "native_environment", return_value={}), patch.object(
            organizer, "claude_bin", return_value="controlled"
        ), patch.object(organizer, "run", side_effect=CommandCancelled("private")):
            with self.assertRaises(CommandCancelled):
                organizer.run_native({})


if __name__ == "__main__":
    unittest.main()
