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
            }))
            with patch.dict(organizer.os.environ, {}, clear=True), patch(
                "paths.load_adapter_config", return_value={"claude_config_dir": tmp}
            ):
                env = organizer.native_environment()
            self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "fixture-secret")
            self.assertEqual(organizer.organizer_model(env), "selected-model")
            self.assertEqual(organizer.organizer_effort(env), "high")
            self.assertNotIn("UNRELATED_SETTING", env)
            self.assertNotIn("hooks", env)

    def test_normalize_core_schema(self):
        result = organizer.normalize(
            {
                "entries": [
                    {
                        "entry_id": None,
                        "title": "Case",
                        "summary": "Abstract",
                        "content": "Detailed body",
                        "conditions": {"commit": "abc"},
                    }
                ]
            }
        )
        self.assertEqual(result["entries"][0]["content"], "Detailed body")
        self.assertEqual(result["entries"][0]["conditions"]["commit"], "abc")

    def test_body_alias_and_public_json_result(self):
        converted = organizer.normalize(
            {"entries": [{"title": "T", "summary": "S", "body": "B"}]}
        )
        self.assertEqual(converted["entries"][0]["content"], "B")
        text = json.dumps(
            {"type": "result", "result": '{"entries":[]}', "session_id": "x"}
        )
        public = organizer.public_result_text(text)
        self.assertEqual(organizer.extract_json(public), {"entries": []})

    def test_no_shipped_host_model_default(self):
        self.assertFalse(hasattr(organizer, "DEFAULT_MODEL"))
        source = Path(organizer.__file__).read_text()
        self.assertNotIn("deepseek-flash", source)
        self.assertNotIn("DEFAULT_MODEL", source)


if __name__ == "__main__":
    unittest.main()
