import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import transcript  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "transcript.jsonl"
SESSION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / f"{SESSION}.jsonl"
        shutil.copy(FIXTURE, self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_identify_and_public_records(self):
        ident = transcript.identify(self.path)
        self.assertIsNotNone(ident)
        result = transcript.read_material(str(self.path), 0, session_id=SESSION)
        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["records"], 0)
        self.assertIn("No matching notes yet", result["text"])
        self.assertIn("knowledge_query", result["text"])
        self.assertIn("call_00_abc", result["text"])
        self.assertNotIn("omitted-placeholder", result["text"])
        self.assertNotIn("subagent hidden", result["text"])
        self.assertNotIn("Base directory for this skill", result["text"])
        self.assertNotIn("/mindie-agent:init", result["text"])

    def test_not_before_excludes_inherited(self):
        result = transcript.read_material(
            str(self.path), 0, session_id=SESSION, not_before=1e18
        )
        self.assertEqual(result["records"], 0)

    def test_wrong_session_file(self):
        other = self.tmp / "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
        shutil.copy(self.path, other)
        result = transcript.read_material(str(other), 0, session_id=SESSION)
        self.assertEqual(result["status"], "wrong-task")

    def test_real_probe_public_blocks_only(self):
        path = (
            Path.home()
            / ".claude/projects/-Users-maoxx241-code-mindie-lifecycle-20260920-acceptance-cc-probe"
            / "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a.jsonl"
        )
        if not path.is_file():
            self.skipTest("root probe transcript is not present")
        result = transcript.read_material(
            str(path), 0, session_id="645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"
        )
        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["records"], 0)
        self.assertIn("mcp__mindie_probe__echo", result["text"])
        self.assertNotIn("signature", result["text"])

    def test_partial_line_does_not_consume(self):
        with self.path.open("ab") as stream:
            stream.write(b'{"type":"assistant","sessionId":"' + SESSION.encode())
        size = self.path.stat().st_size
        result = transcript.read_material(str(self.path), 0, session_id=SESSION)
        self.assertTrue(result["partial"] or result["end"] < size)


if __name__ == "__main__":
    unittest.main()
